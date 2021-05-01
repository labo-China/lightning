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
        del self.conn  # remove raw handler

    def __repr__(self):
        ht = '<br/>'.join(': '.join(h) for h in self.headers)
        return f'<Request [{self.url}]> from {self.addr} {self.version}<br/>' \
               f'Method: {self.method}<br/>' \
               f'{ht}<br/>' \
               f'Extra data: {self.extra_data}'


class Response:
    pass


class Handler:
    def __init__(self, func):
        self._func = func

    def execute(self, *args, **kwargs):
        raise NotImplementedError('Not Implemented')


class Interface:
    def __init__(self, func, req_type: type):
        self._function = func
        self.req_type = req_type

    def __enter__(self):
        return self

    def process(self, request: self.req_type):
        return self._function(request)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type or exc_val or exc_tb:
            print(exc_type, exc_val, exc_tb)

        return True


class BaseInterface(Handler):
    def __init__(self, func):
        super().__init__(func)

    def execute(self, conn, addr):
        return self._func(conn, addr)


class BytesInterface(Handler):
    def __init__(self, func, allowed_param: list = None, allowed_method: list = None):
        super().__init__(func)
        self._param = allowed_param or []
        self._method = allowed_method or ['GET']

    def execute(self, request: Request):
        return self._func(request)


class StringInterface(Handler):
    def __init__(self, func, allowed_param: list = None, allowed_method: list = None):
        super().__init__(func)
        self._param = allowed_param or []
        self._method = allowed_method or ['GET']

    def execute(self, request: Request):
        from datetime import datetime
        time = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
        content = self._func(request)
        h = f'HTTP/1.1 200 OK\r\nDate:{time}\r\nContent-Length:{len(content.encode("utf-8"))}\r\n' \
            f'Content-Type:text/html\r\n\r\n{content}'.encode('utf-8')
        return h
