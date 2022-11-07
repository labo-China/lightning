import fnmatch
import logging
import pathlib
from mimetypes import guess_type
from os.path import getsize, basename
from os import scandir, DirEntry
from ssl import SSLContext
from typing import Callable, Optional, Iterable
from urllib.parse import quote

from .structs import Interface, Response, Request


class File(Interface):
    """The file interface"""

    def __init__(self, path: str, filename: str = None, mime: str = None,
                 range_support: bool = True, force_download: bool = False):
        super().__init__(self.download, desc = path)
        self.path = path
        self.range = range_support
        # self.update = updateble
        self.filename = filename or basename(path)
        self.filesize = getsize(self.path)
        self.mime = mime or self.get_mime()
        logging.debug(f'Using MIME "{self.mime}" for {self}')
        self.force_download = force_download

    def download(self, request: Request):
        def sendfile(conn, *args):
            try:
                conn.sendfile(*args)
            except (ConnectionAbortedError, ConnectionResetError):  # the error is raised by certain downloaders
                pass

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
        else:
            header = {'Content-Disposition': 'attachment;filename=' + self.filename} if self.force_download else {}
            request.conn.sendall(Response(header = {'Content-Length': str(self.filesize),
                                                    'Content-Type': self.mime,
                                                    'Accept-Ranges': 'bytes'} | header).generate())
            sendfile(request.conn, file)

    def get_mime(self) -> str:
        """Get a proper MIME for a file. It will check the filename, Then for the content"""
        builtin_mime = guess_type(self.filename, strict = False)
        if builtin_mime[0]:
            return builtin_mime[0]

        with open(self.path, 'rb') as fd:
            if b'\x00' in fd.read(1024):  # Is it a binary file?
                return 'application/octet-stream'
            else:
                return 'text/plain'


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
        super().__init__(self.main, desc = root)

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
__all__ = ['File', 'StorageView', 'WSGIInterface', 'Echo', 'Empty']
