import socket
from typing import Union, Tuple, List
from re import Pattern, compile


class RAWRequest:
    def __init__(self, addr: tuple, method: str, url: str, version: str, param: dict, content: bytes,
                 conn: socket.socket = None, **ext_data):
        self.addr = addr
        self.method = method
        self.url = url
        self.param = param
        self.version = version
        self.content = content
        self.conn = conn
        self.extra_data = ext_data


class Request(RAWRequest):
    def __init__(self, header: list, content: bytes, **ext_data):
        super().__init__(**ext_data, content = content)
        self.headers = header
        self.content = content
        self.extra_data = ext_data

    def __repr__(self):
        ht = '<br/>'.join(': '.join(h) for h in self.headers)
        return f'<Request [{self.url}]> from {self.addr} {self.version}<br/>' \
               f'Method: {self.method}<br/>' \
               f'{ht}<br/>' \
               f'Extra data: {self.extra_data}'


class Response:
    pass


class Interface:
    def __init__(self, func, req_type: type, default_msg: bytes = 'HTTP/1.1 500 Internal Server Error/r/n/r/n'):
        self.msg = b''
        self.default_msg = default_msg
        self._function = func
        self.req_type = req_type

    def __enter__(self):
        return self

    def process(self, request: Union[RAWRequest, Request]):
        self.msg = self._function(request)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type or exc_val or exc_tb:
            self.msg = self.default_msg
            print(exc_type, exc_val, exc_tb)
        return True
