import socket
import threading
import Utility
import time
from datetime import datetime, timezone
from typing import Union, Tuple, List, Dict, Callable
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
    def __init__(self, addr: tuple, header: list, content: bytes, **ext_data):
        super().__init__(addr = addr, **ext_data, content = content)
        self.headers = header
        self.content = content
        self.extra_data = self.extra_data

    def __repr__(self):
        ht = '\n'.join(': '.join(h) for h in self.headers)
        pr = '\n'.join(f'{p}: {self.param[p]}' for p in self.param.keys()) if self.param else ''
        return f'<Request [{self.url}]> from {self.addr} {self.version}\n' \
               f'Method: {self.method}\n' \
               f'Headers: \n' \
               f'{ht}\n' \
               f'Params:' \
               f'{pr}\n' \
               f'Extra data: {self.extra_data}'


class RAWResponse:
    def __init__(self, content: bytes):
        self.content = content

    def generate(self):
        return self.content


class Response(RAWResponse):
    def __init__(self, code: int = 200, version: str = 'HTTP/1.1', headers: Dict[str, str] = None,
                 content: Union[bytes, str] = b'', encoding: str = 'utf-8', timestamp: int = int(time.time())):
        self.timestamp = datetime.fromtimestamp(timestamp, tz = timezone.utc)
        self.code = code
        self.version = version
        content = bytes(content, encoding) if isinstance(content, str) else content
        default_headers = {
            'Content-Type': 'text/plain',
            'Content-Length': str(len(content)),
            'Server': 'Lighting',
            'Date': self.timestamp.strftime('%a, %d %b %Y %H:%M:%S GMT')
        }
        self.headers = default_headers | (headers or {})
        super(Response, self).__init__(content)

    def generate(self):
        hd = '\r\n'.join([': '.join([h, self.headers[h]]) for h in self.headers])
        text = f'{self.version} {self.code} {Utility.HTTP_code[self.code]}\r\n' \
               f'{hd}\r\n\r\n'
        return text.encode('utf-8') + self.content


class Interface:
    def __init__(self, func: Callable[[Union[RAWRequest, Request]], RAWResponse], req_type: type,
                 default_msg: RAWResponse = RAWResponse(b'HTTP/1.1 500 Internal Server Error/r/n/r/n')):
        self.resp: RAWResponse = RAWResponse(b'')
        self.default_resp = default_msg
        self._function = func
        self.req_type = req_type

    def __enter__(self):
        return self

    def process(self, request: Union[RAWRequest, Request]):
        self.resp = self._function(request)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type or exc_val or exc_tb:
            self.resp = self.default_resp
            print(exc_type, exc_val, exc_tb)
        del self
        return True


class Worker:
    def __init__(self):
        self.is_working = False

    def handle(self, request, cls):
        self.is_working = True
        cls.active_worker += 1
        request.process()
        self.is_working = False
        cls.active_worker -= 1


class Processer:
    def __init__(self, queue: List[Interface], process_limit: int = 16):
        self.queue = queue
        self.process_limit = process_limit
        self.active_worker = 0
        self.worker_list: List[Worker] = []
        for _ in range(process_limit):
            self.worker_list.append(Worker())

    def handle(self):
        from time import sleep
        while True:
            if self.queue and self.active_worker < self.process_limit:
                for w in self.worker_list:
                    if not w.is_working and self.queue:
                        print(f'{self.worker_list.index(w)} is working {self.active_worker} {len(self.queue)} remain')
                        t = threading.Thread(target = w.handle, kwargs = {'request': self.queue[0], 'cls': self})
                        self.queue.pop(0)
                        t.start()
            if not self.queue:
                print('queue empty')
                break
