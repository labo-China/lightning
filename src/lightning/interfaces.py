import copy
import fnmatch
import logging
import pathlib
from mimetypes import guess_type
from os.path import getsize, basename
from os import scandir, DirEntry
from ssl import SSLContext
from typing import Callable, Optional, Iterable, Union
from urllib.parse import quote

from .utility import Method
from .structs import Interface, Response, Request, Responsive, Sendable


class File(Interface):
    """The file interface"""

    def __init__(self, path: str, filename: str = None, mime: str = None,
                 range_support: bool = True, updateble: bool = False, force_download: bool = False):
        self.path = path
        self.range = range_support
        self.update = updateble
        self.filename = filename or basename(path)
        self.filesize = getsize(self.path)
        self.mime = mime or guess_type(self.filename)[0] or 'application/octet-stream'
        self.force_download = force_download
        super().__init__(self.download, desc = path)

    def download(self, request: Request):
        def sendfile(conn, *args):
            try:
                conn.sendfile(*args)
            except (ConnectionAbortedError, ConnectionResetError):  # the error is raised by certain downloaders
                pass
            finally:
                request.conn.close()  # block the worker to stop sending response

        try:
            file = open(self.path, 'rb')
        except PermissionError:
            return Response(code = 403)
        except FileNotFoundError:
            return Response(code = 404)

        if request.header.get('Range') and self.range:
            start, end = map(lambda x: int(x) if x else None, request.header.get('Range').split('=')[1].split('-'))
            start = start or 0
            end = end or self.filesize - 1
            left = end - start + 1

            if 0 > start or start > end or end >= self.filesize:
                return Response(code = 416)
            request.conn.sendall(Response(code = 206, header = {
                'Content-Length': str(left),
                'Content-Type': 'multipart/byteranges',
                'Accept-Ranges': 'bytes',
                'Content-Range': f'bytes {start}-{end}/{self.filesize}'}).generate())
            sendfile(request.conn, file, start, left)
            return Response(206)
        else:
            mime = guess_type(self.filename)[0] or 'application/octet-stream'
            header = {'Content-Disposition': 'attachment;filename=' + self.filename} if self.force_download else {}
            request.conn.sendall(Response(header = {'Content-Length': str(self.filesize),
                                                    'Content-Type': mime,
                                                    'Accept-Ranges': 'bytes'} | header).generate())
            sendfile(request.conn, file)
            return Response(200)


class StorageView(Interface):
    def __init__(self, root: str, depth: int = 0, enable_view: bool = True, allow_exceeded_links: bool = False, *rules):
        """
        :param root: the root directory
        :param depth: the max depth (Note: path like '/foo/bar' has depth of 2)
        :param enable_view: whether the interface will return an HTML page to list files when requesting a directory
        :param rules: fnmatch-style file patterns
        """
        self.root = pathlib.Path(root)
        self.depth = depth
        self.enable_view = enable_view
        self.allow_exceed_links = allow_exceeded_links
        self.rules = rules
        super().__init__(self.main)

    def main(self, request: Request):
        # return 403-Forbidden if depth is given and request is deeper than it
        if self.depth:
            p = pathlib.Path(request.path)
            depth = len(p.parts)
            if depth > self.depth + 1:
                return Response(403)

        path = self.root.joinpath(request.path.removeprefix('/'))
        # check some condtitions
        if not path.exists():
            return Response(404)
        if path.is_symlink():
            path = path.resolve()
            if not (self.allow_exceed_links or path.is_relative_to(self.root)):
                return Response(403)
        if not self.test_accessibility(path):
            return Response(403)

        if path.is_file():
            file = File(str(path))
            return file.download(request)
        elif path.is_dir():
            if not request.url.endswith('/'):
                return Response(301, header = {'Location': request.url + '/'})
            if self.enable_view:
                return Response(content = self.render(request, path), header = {'Content-Type': 'text/html'})
            else:
                return Response(403)

    @staticmethod
    def test_accessibility(path: pathlib.Path) -> bool:
        try:
            if path.is_file():
                open(path, 'rb').close()
            elif path.is_dir():
                scandir(path)
        except PermissionError:
            return False
        else:
            return True

    def get_fd_set(self, path: pathlib.Path):
        folder, file = set(), set()
        fd: DirEntry
        for fd in scandir(path):
            name = basename(fd.path.removesuffix('/'))
            if fd.is_dir():
                folder.add(name)
            else:
                for r in self.rules:
                    if not fnmatch.fnmatch(name, r):
                        break
                else:
                    file.add(name)
        return folder, file

    def render(self, request: Request, path: pathlib.Path) -> str:
        prev_url = pathlib.PurePosixPath(request.url).parent
        content = f'<html><head><title>Index of {request.url}</title></head><body bgcolor="white">' \
                  f'<h1>Index of {request.url}</h1><hr><pre><a href="{prev_url}">../</a>\n'

        folder, file = self.get_fd_set(path)
        for x in sorted(folder):
            content += f'<a href="{quote(request.url + x)}/">{x}/</a>\n'
        for y in sorted(file):
            content += f'<a href="{quote(request.url + y)}">{y}</a>\n'
        return content + '</pre></body></html>'


class WSGIInterface(Interface):
    def __init__(self, application: Callable[[dict, Callable], Optional[Iterable[bytes]]], *args, **kwargs):
        """
        :param application: a callble object to run as a WSGI application
        """
        # remove 'pre' and 'post' arguments, they are unusable in WSGI standards.
        self.app = application
        self.response: Response = Response()
        self.content_iter: Iterable = ()
        kwargs.update({'pre': None, 'post': None})
        super().__init__(self.call, *args, **kwargs)

    def call(self, request: Request) -> Response:
        resp = self.app(self.get_environ(request), self.start_response)
        self.content_iter = (resp or []).__iter__()
        data = self.response.generate()
        try:
            while data == self.response.generate():  # Waiting for the first non-empty response
                data += self.content_iter.__next__()
            for d in self.content_iter:
                request.conn.send(d)
        except StopIteration:
            request.conn.send(data)  # Send an HTTP response with an empty body if no content is provided
        finally:
            # Reset static variables
            self.response = Response()
            self.content_iter = ()
        return Response()  # Avoid interface to send response data

    def start_response(self, status: str, header: list[tuple[str, str]] = None, exc_info: tuple = None):
        """
        Submit an HTTP response header, it will not be sent immediately.\n
        :param status: HTTP status code and its description
        :param header: HTTP headers like [(key1, value1), (key2, value2)]
        :param exc_info: if an error occured, it is an exception tuple, otherwise it is None
        """
        logging.info(f'start_response is called by {self.app.__name__}')
        if exc_info:
            raise exc_info[1].with_traceback(exc_info[2])
        code, desc = status.split(' ', 1)
        self.response = Response(code = code, desc = desc, header = dict(header or {}))

    @staticmethod
    def get_environ(request: Request) -> dict:
        """Return a dict includes WSGI varibles from a request"""
        environ = {}
        environ.update(dict(os.environ))
        environ['REQUEST_METHOD'] = request.method.upper()
        environ['SCRIPT_NAME'] = request.url.removesuffix(request.path)
        environ['PATH_INFO'] = request.path.removeprefix('/')
        environ['QUERY_STRING'] = request.query
        environ['CONTENT_TYPE'] = request.header.get('Content-Type') or ''
        environ['CONTENT_LENGTH'] = request.header.get('Content-Length') or ''
        environ['SERVER_NAME'], environ['SERVER_PORT'] = request.conn.getsockname()
        environ['SERVER_PROTOCOL'] = request.version
        for k, v in request.header.items():  # Set HTTP_xxx variables
            environ.update({f'HTTP_{k.upper()}': v[1]})
        environ['wsgi.version'] = (1, 0)
        environ['wsgi.url_scheme'] = 'https' if isinstance(request.conn, SSLContext) else 'http'
        environ['wsgi.input'] = request.conn.makefile(mode = 'rwb')
        environ['wsgi.errors'] = None  # unavailable now
        environ['wsgi.multithread'] = environ['wsgi.multiprocess'] = False
        environ['wsgi.run_once'] = True  # This server supports running application for multiple times
        return environ

    def __repr__(self) -> str:
        return f'WSGIInterface[{self.app.__name__}]'


class Node(Interface):
    """An interface that provides a main-interface to carry sub-Interfaces."""

    def __init__(self, interface_map: dict[str, Interface] = None,
                 interface_callback: Callable[[], dict[str, Interface]] = None,
                 default: Responsive = Response(404), *args, **kwargs):
        """
        :param interface_map: initral mapping for interfaces
        :param interface_callback: the function to call whenever getting mapping in order to modify mapping dynamically
        :param default: the final interface when no interfaces were matched
        """
        super().__init__(generic = self._process, *args, **kwargs)
        self.map_static: dict[str, Interface] = interface_map or {}
        self.map_callback = interface_callback
        self.default = default
        self.last_call: str = ''

    def select_target(self, request: Request) -> tuple[Interface, Request]:
        """Select the matched interface then return it with adjusted request"""
        interface_map = self.get_map()
        req_path = pathlib.PurePosixPath(request.path)

        for bound_path in sorted(interface_map.keys(), key = lambda x: (x.count('/'), x), reverse = True):
            # sort it to make interface_map like ['/foo/bar', '/foo', '/goo', '/']
            if req_path.is_relative_to(bound_path):
                target = interface_map[bound_path]
                path = request.path.removeprefix(bound_path)
                new_req = copy.copy(request)
                if path == '.':
                    new_req.path = ''
                elif path and not path.startswith('/'):
                    new_req.path = '/' + path
                else:
                    new_req.path = path
                return target, new_req
        else:
            return Interface.create_from(self.default), request

    def _process(self, request: Request) -> Sendable:
        target, request = self.select_target(request)
        self.last_call = repr(target)
        return target(request)

    def get_map(self) -> dict[str, Interface]:
        """Return interface map as a dictonary"""
        if not self.map_callback:
            return self.map_static
        return dict({**self.map_static, **self.map_callback()})

    def bind(self, pattern: str, interface_or_method: Union[Interface, list[Method]] = None, *args, **kwargs):
        r"""
        Bind an interface or function into this node.
        :param pattern: the url prefix to match requests.
        :param interface_or_method: If the function is called as a normal function, the value is the Interface
            needs to bind. If the function is called as a decorator or the value is None, the value is expected
            HTTP methods list. If the value is None, it means the Interface will be a GET handler by default.
        """
        if pattern and not pattern.startswith('/'):
            pattern = '/' + pattern
        if pattern != '/':
            pattern = pattern.removesuffix('/')
        if isinstance(interface_or_method, Interface) or hasattr(interface_or_method, '__call__'):
            # called as a normal function
            self.map_static[pattern] = interface_or_method
            logging.info(f'{interface_or_method} is bound on {pattern}')
            return

        def decorator(func):  # called as a decorator
            if interface_or_method is not None:
                parameter = dict.fromkeys(interface_or_method, func)
                self.bind(pattern, Interface(get_or_method = parameter, *args, **kwargs))
            else:
                self.bind(pattern, Interface(func, *args, **kwargs))
            return func  # return the given function to keep it

        return decorator

    def __repr__(self) -> str:
        if self.last_call:
            ret = self.last_call
            self.last_call = ''
        else:
            ret = super().__repr__()
        return ret


def _echo(request: Request) -> Response:
    """Return a human-readable information of request"""
    ht = '\n\t'.join(': '.join((h, request.header[h])) for h in request.header)
    pr = ' '.join(f'{k[0]}:{k[1]}' for k in request.keyword.items())
    content = f'<Request [{request.url}]> from {request.addr} {request.version}\n' \
              f'Method: {request.method}\n' \
              f'Headers: \n' \
              f'\t{ht}\n' \
              f'Query:{request.query}\n' \
              f'Params:{pr}\n' \
              f'Args:{" ".join(request.arg)}\n'
    return Response(content = content)


Echo = Interface(generic = _echo)
Empty = Interface(lambda: Response(204))
__all__ = ['File', 'StorageView', 'Node', 'WSGIInterface', 'Echo', 'Empty']
