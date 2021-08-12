import logging
import re
import time
from mimetypes import guess_type
from os import listdir
from os.path import getsize, basename, isdir, isfile
from re import Pattern, match, compile
from typing import Dict, List, Union, Callable, Iterable, Generator
from .structs import Interface, Response, Request, MethodInterface, Node, DefaultInterface


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
            header = {'Content-Disposition': 'attachment;filename=' + self.filename}
            request.conn.sendall(Response(header = {'Content-Length': str(self.filesize),
                                                    'Content-Type': mime,
                                                    'Accept-Ranges': 'bytes'} | header).generate())
            request.conn.sendfile(file)
        request.conn.close()

    def __repr__(self) -> str:
        return f'File[{self.path}]'


class Folder(Node):
    FilterType = Union[Callable[[str], bool], Pattern, str]
    _FilterParam = Union[Iterable[FilterType], FilterType]

    def __init__(self, path: str, file_depth: int = -1, lazy: bool = True, update_time: int = 60,
                 file_filter: _FilterParam = None, file_blocker: _FilterParam = None):
        self.path = path + '/' if not path.endswith('/') else path
        self.dirname = basename(path.removesuffix('/'))
        self.file_depth = file_depth
        self.lazy = lazy
        self.update_time = update_time
        self.last_update = 0
        self.filter = self._convert_filter(file_filter or '.')
        self.blocker = self._convert_filter(file_blocker or [])
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

    @staticmethod
    def _convert_filter(obj: _FilterParam) -> Iterable[Callable[[str], bool]]:
        if isinstance(obj, str) or not hasattr(obj, '__iter__'):
            obj: List[Folder.FilterType] = [obj]

        def convert(f: Folder.FilterType) -> Callable[[str], bool]:
            if isinstance(f, str):
                # Convert string to regex pattern
                # For string starts with "*.", converter will recognize it as a file suffix
                string = r'.*?\.{}$'.format(f.split('*.', 1)[-1])
                f = re.compile(string) if f.startswith('*.') else re.compile(f)
            if isinstance(f, Pattern):
                return lambda s: bool(f.match(s))
            else:
                if not callable(f):
                    raise TypeError('Filter must be a string, regex pattern or callable object')
                return f

        return set(map(convert, obj))

    def _is_passable(self, string: str) -> bool:
        for filter_ in self.filter:
            if filter_(string):
                break
        else:
            return False
        for blocker in self.blocker:
            if blocker(string):
                return False
        else:
            return True

    def _filter(self, seq: Iterable[str]) -> Iterable[str]:
        return filter(self._is_passable, seq)

    def load_file(self) -> Dict[str, Interface]:
        if self.file_depth == 0:
            return {}
        m = {}
        try:
            for name in listdir(self.path):
                abs_path = self.path + name
                if isfile(abs_path):
                    if self._is_passable(name):
                        m.update({'/' + name: File(abs_path)})
                elif isdir(abs_path) and (self.file_depth == -1 or self.file_depth >= 2):
                    next_file_depth = self.file_depth - 1 if self.file_depth != -1 else -1
                    m.update({'/' + name: Folder(abs_path, file_depth = next_file_depth, file_filter = self.filter,
                                                 file_blocker = self.blocker, lazy = self.lazy)})
        except PermissionError:
            return {}
        except FileNotFoundError:
            return m
        return m

    def get_map(self) -> Dict[str, Interface]:
        if not self._file_map or time.time() - self.last_update > self.update_time:
            self.update_map()
        return self._file_map

    def update_map(self):
        logging.info(f'Updating the map of {self.path}...')
        mapping = self.load_file()
        self._file_map = mapping
        self.last_update = time.time()

    @staticmethod
    def generate_default(request: Request, file_list: dict) -> str:
        prev_url = request.url.removesuffix('/').rsplit('/', 1)[0]
        content = f'<html><head><title>Index of {request.url}</title></head><body bgcolor="white">' \
                  f'<h1>Index of {request.url}</h1><hr><pre><a href="{prev_url}">../</a>\n'
        folder = filter(lambda f: isinstance(f, Folder), file_list.values())
        file = filter(lambda f: isinstance(f, File), file_list.values())
        for x in folder:
            content += f'<a href="{request.url + x.dirname}">{x.dirname}/</a>\n'
        for y in file:
            content += f'<a href="{request.url + y.filename}">{y.filename}</a>\n'
        return content + '</pre></body></html>'

    def default(self, request: Request) -> Response:
        if self.status:
            return Response(code = self.status)
        request.path = request.path.removesuffix('/')
        f = self.map
        if request.path:
            for x in request.path.removeprefix('/').split('/'):
                if x in f:
                    f = f[x]
                else:
                    return Response(code = 404)
        return Response(content = self.generate_default(request, f), header = {'Content-Type': 'text/html'})

    def __repr__(self) -> str:
        return f'Folder[{self.path}]'


__all__ = ['File', 'Folder', 'DefaultInterface']
