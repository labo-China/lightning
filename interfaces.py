import time
from mimetypes import guess_type
from os import listdir
from os.path import getsize, basename, isdir, isfile, exists
from urllib.parse import quote
from typing import Dict

from structs import Interface, Response, Request, MethodInterface, Node, DefaultInterface


class File(MethodInterface):
    """The file interface"""
    def __init__(self, path: str, filename: str = None, range_support: bool = True, updateble: bool = False):
        self.path = path
        self.range = range_support
        self.update = updateble
        self.filename = filename or basename(path)
        self.filesize = getsize(self.path)
        super().__init__(get = self.download)

    def download(self, request: Request):
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
            request.conn.sendfile(file, start, left)
        else:
            mime = guess_type(self.filename)[0] or 'application/octet-stream'
            header = {'Content-Disposition':
                      'attachment;filename=' + self.filename} if request.keyword.get('view') else {}
            request.conn.sendall(Response(header = {'Content-Length': str(self.filesize),
                                                    'Content-Type': mime,
                                                    'Accept-Ranges': 'bytes'} | header).generate())
            request.conn.sendfile(file)
        request.conn.close()


class Folder(Node):
    def __init__(self, path: str, file_only: bool = False, lazy: bool = True, update_time: int = 60):
        self.path = path + '/' if not path.endswith('/') else path
        self.dirname = basename(path.removesuffix('/'))
        self.file_only = file_only
        self.lazy = lazy
        self.update_time = update_time
        self.last_update = 0
        self._file_map = {}

        try:
            listdir(self.path)
        except PermissionError:
            self.status = 403
        except FileNotFoundError:
            self.status = 404
        else:
            self.status = 0

        super().__init__(interface_map = self.get_map if lazy else self.load_file(),
                         default_interface = Interface(self.default))

    def load_file(self) -> Dict[str, Interface]:
        m = {}
        try:
            for name in listdir(self.path):
                abs_path = self.path + name
                if isfile(abs_path):
                    m.update({'/' + name: File(abs_path)})
                elif isdir(abs_path) and not self.file_only:
                    m.update({'/' + name: Folder(abs_path, file_only = self.file_only, lazy = self.lazy)})
        except PermissionError:
            return {}
        return m

    def get_map(self) -> Dict[str, Interface]:
        if not self._file_map or time.time() - self.last_update > self.update_time:
            self.update_map()
        return self._file_map

    def update_map(self):
        print(f'Updating the map of {self.path}...', end = '')
        mapping = self.load_file()
        self._file_map = mapping
        self.last_update = time.time()
        print('Success')

    @staticmethod
    def generate_default(request: Request, file_list: dict) -> str:
        prev_url = request.url.rsplit('/', 1)[0]
        content = f'<html><head><title>Index of {request.url}</title></head><body bgcolor="white">' \
                  f'<h1>Index of {request.url}</h1><hr><pre><a href="{prev_url}">../</a>\n'
        folder = filter(lambda f: isinstance(f, Folder), file_list.values())
        file = filter(lambda f: isinstance(f, File), file_list.values())
        for x in folder:
            content += f'<a href="{request.url + "/" + x.dirname}">{x.dirname}/</a>\n'
        for y in file:
            content += f'<a href="{request.url + "/" + y.filename}">{y.filename}</a>\n'
        return content

    def default(self, request: Request) -> Response:
        if self.status:
            return Response(code = self.status)
        f = self.map
        if request.path:
            for x in request.path.removeprefix('/').split('/'):
                if x in f:
                    f = f[x]
                else:
                    return Response(code = 404)
        return Response(content = self.generate_default(request, f), header = {'Content-Type': 'text/html'})
