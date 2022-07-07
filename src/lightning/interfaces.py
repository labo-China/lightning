import fnmatch
import pathlib
from mimetypes import guess_type
from os.path import getsize, basename
from os import scandir, DirEntry
from .structs import Interface, Response, Request


class File(Interface):
    """The file interface"""

    def __init__(self, path: str, filename: str = None, range_support: bool = True, updateble: bool = False):
        self.path = path
        self.range = range_support
        self.update = updateble
        self.filename = filename or basename(path)
        self.filesize = getsize(self.path)
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
            end = end or self.filesize
            left = end - start + 1

            if start > end or end > self.filesize:
                return Response(code = 416)
            request.conn.sendall(Response(code = 206, header = {
                'Content-Length': str(end - start + 1),
                'Content-Type': 'application/octet-stream',
                'Content-Disposition': 'attachment;filename=' + self.filename,
                'Content-Range': f'bytes {start}-{end}/{self.filesize}'}).generate())
            sendfile(request.conn, file, start, left)
            return Response(206)
        else:
            mime = guess_type(self.filename)[0] or 'application/octet-stream'
            header = {'Content-Disposition': 'attachment;filename=' + self.filename}
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
            if depth > self.depth:
                return Response(403)

        path = self.root.joinpath(request.path)
        # check some condtitions
        if not path.exists():
            return Response(404)
        if path.is_symlink():
            parents = path.parents
            path = path.resolve()
            if not (self.allow_exceed_links or self.root in parents):
                return Response(403)
        if not self.test_accessibility(path):
            return Response(403)

        if path.is_file():
            file = File(str(path))
            return file.download(request)
        elif path.is_dir():
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
            content += f'<a href="{request.url + x}">{x}/</a>\n'
        for y in sorted(file):
            content += f'<a href="{request.url + y}">{y}</a>\n'
        return content + '</pre></body></html>'


def _debug(request: Request) -> Response:
    """Return a human-readable information of request"""
    ht = '\n    '.join(': '.join((h, request.header[h])) for h in request.header)
    pr = ' '.join(f'{k[0]}:{k[1]}' for k in request.keyword.items())
    content = f'<Request [{request.url}]> from {request.addr} {request.version}\n' \
              f'Method: {request.method}\n' \
              f'Headers: \n' \
              f'{ht}\n' \
              f'Query:{request.query}\n' \
              f'Params:{pr}\n' \
              f'Args:{" ".join(request.arg)}\n'
    return Response(content = content)


Debug = Interface(generic = _debug)
Empty = Interface(lambda: Response(204))
__all__ = ['File', 'StorageView', 'Debug', 'Empty']
