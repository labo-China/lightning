import queue
import socket
import time
import os
from io import BytesIO
import threading
import multiprocessing
from ssl import SSLContext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Union, Dict, Callable, List, Optional, Tuple, Generator

from . import utility


class HookedSocket(socket.socket):

    def __init__(self, sock: socket.socket, buffer: int = 1024):
        fd = sock.detach()
        super().__init__(fileno = fd)  # Rebuild a socket object from given socket as super object
        self.buffer = buffer
        self.stack = BytesIO()

    def _send_to_stack(self, data: bytes):
        length = len(data)
        sent = 0
        self.stack.seek(0, 1)
        write = self.stack.write
        push = self.push
        buffer = self.buffer
        while length:
            tosend = min(length, buffer - self.stack.getbuffer().nbytes)
            d = data[:tosend]
            sent += write(d)
            length -= tosend
            push(min(self.stack.getbuffer().nbytes, buffer))
        return sent

    def send(self, data: bytes, _: int = ...) -> int:
        self._send_to_stack(data)
        return len(data)

    def sendall(self, data: bytes, _: int = ...) -> None:
        self._send_to_stack(data)

    def sendfile(self, file, offset: int = 0, count: Optional[int] = None) -> int:
        """Copied from the _sendfile_use_send method of socket to implement hook features."""
        self._check_sendfile_params(file, offset, count)
        if self.gettimeout() == 0:
            raise ValueError("non-blocking sockets are not supported")
        if offset:
            file.seek(offset)
        blocksize = min(count, 8192) if count else 8192
        total_sent = 0
        # localize variable access to minimize overhead
        file_read = file.read
        sock_send = self._send_to_stack
        try:
            while True:
                if count:
                    blocksize = min(count - total_sent, blocksize)
                    if blocksize <= 0:
                        break
                data = memoryview(file_read(blocksize))
                if not data:
                    break  # EOF
                while True:
                    try:
                        sent = sock_send(data)
                    except BlockingIOError:
                        continue
                    else:
                        total_sent += sent
                        if sent < len(data):
                            data = data[sent:]
                        else:
                            break
            return total_sent
        finally:
            if total_sent > 0 and hasattr(file, 'seek'):
                file.seek(offset + total_sent)

    def push(self, size: int = None):
        total_sent = 0
        count = size or self.buffer
        blocksize = min(count, self.buffer) if count else self.buffer
        sock_send = super().send
        file_read = self.stack.read
        self.stack.seek(0, 0)
        while True:
            if count:
                blocksize = min(count - total_sent, blocksize)
                if blocksize <= 0:
                    break
            data = memoryview(file_read(blocksize))
            if not data:
                break  # EOF
            while True:
                sent = sock_send(data)
                total_sent += sent
                if sent < len(data):
                    data = data[sent:]
                else:
                    break

    def close_(self) -> None:
        self.push(self.stack.getbuffer().nbytes)  # flush buffer
        super().close()


@dataclass
class Request:
    """Request that recevived from the parser of server"""
    addr: Tuple[str, int] = field(default = ('127.0.0.1', -1))
    method: str = field(default = 'GET')
    url: str = field(default = '/')
    version: str = field(default = 'HTTP/1.1')
    keyword: Dict[str, str] = field(default_factory = dict)
    arg: set = field(default_factory = set)
    header: Dict[str, str] = field(default_factory = dict)
    query: str = field(default = '')
    conn: socket.socket = None
    path: str = None

    def __post_init__(self):
        """Set path to url if path not specfied"""
        self.path = self.path or self.url

    def content(self, buffer: int = 1024) -> bytes:
        """
        Get the content of the Request.\n
        :param buffer: buffer size of content
        """
        return utility.recv_all(conn = self.conn, buffer = buffer)

    def iter_content(self, buffer: int = 1024) -> Generator[bytes, None, None]:
        """
        Get the content of the Request.\n
        :param buffer: buffer size of content (if is_iter is True or it will be ingored)
        """
        content = True
        while content:
            content = self.conn.recv(buffer)
            yield content

    def generate(self) -> bytes:
        """Generate the original request data from known request"""
        args = '&'.join(self.arg)
        keyword = '&'.join([f'{k[0]}={k[1]}' for k in self.keyword.items()])
        param = ('?' + args + ('&' + keyword if keyword else '')) if args or keyword else ''
        line = f'{self.method} {self.url.removesuffix("/") + param} {self.version}\r\n'
        header = '\r\n'.join([f'{k}:{self.header[k]}' for k in self.header.keys()])
        return (line + header + '\r\n\r\n').encode()

    def get_overview(self) -> str:
        ht = '\n'.join(': '.join((h, self.header[h])) for h in self.header)
        pr = '\n'.join(f'{k[0]}: {k[1]}' for k in self.keyword.items()) + '\n' + '; '.join(self.arg)
        return f'<Request [{self.url}]> from {self.addr} {self.version}\n' \
               f'Method: {self.method}\n' \
               f'Headers: \n' \
               f'{ht}\n' \
               f'Params:' \
               f'{pr}'

    def __repr__(self) -> str:
        return f'Request[{self.method} -> {self.url}]'


@dataclass
class Response:
    """Response with normal initral function."""
    code: int = 200
    desc: str = None
    version: str = field(default = 'HTTP/1.1')
    header: Dict[str, str] = field(default_factory = dict)
    timestamp: Union[datetime, int] = int(time.time())
    content: Union[bytes, str] = b''
    encoding: str = 'utf-8'

    def __post_init__(self):
        # fill the desciption of request
        if not self.desc:
            self.desc = utility.HTTP_CODE[self.code]
        # convert int timestamp to timestamp object
        if isinstance(self.timestamp, int):
            self.timestamp = datetime.fromtimestamp(self.timestamp, tz = timezone.utc)
        # convert string content to byte content with its encoding
        if isinstance(self.content, str):
            self.content = bytes(self.content, self.encoding)
        self.header = {
                          'Content-Type': 'text/plain',
                          'Content-Length': str(len(self.content)),
                          'Server': 'Lighting',
                          'Date': self.timestamp.strftime('%a, %d %b %Y %H:%M:%S GMT')
                      } | self.header
        # Reset the content-type in header
        self.header['Content-Type'] = self.header['Content-Type'].rsplit(';charset=')[0]
        self.header['Content-Type'] += f';charset={self.encoding}'

    def generate(self) -> bytes:
        """Returns the encoded HTTP response data."""
        hd = '\r\n'.join([': '.join([h, self.header[h]]) for h in self.header])
        text = f'{self.version} {self.code} {self.desc}\r\n' \
               f'{hd}\r\n\r\n'
        return text.encode(self.encoding) + self.content

    def __repr__(self) -> str:
        return f'Response[{self.code}]'

    def __bool__(self) -> bool:
        return self.code != 200 or self.content is not None


InterfaceFunction = Callable[[Request], Optional[Union[Response, str]]]


class Interface:
    """The main HTTP server handler."""

    def __init__(self, func: InterfaceFunction, default_resp: Response = Response(code = 500),
                 pre: List[Callable[[Request], Union[Request, Response, str, None]]] = None,
                 post: List[Callable[[Request, Response], Response]] = None, strict: bool = False):
        """
        :param func: A function to handle requests
        :param default_resp: HTTP content will be send when the function meets expections
        :param strict: if it is True, the interface will catch exceed path in interfaces and return a 404 response.
        :param pre: things to do before the function processes request, the result will cover the function.
        :param post: things to do post the function processed request, they should return a Response object.
        """
        self.default_resp = default_resp
        self._function = func
        self.strict = strict
        self.pre = pre or []
        self.post = post or []

    def __enter__(self):
        return self

    def process(self, request: Request) -> Response:
        """
        Let the function processes the request and returns the result
        :param request:  the request that will be processed
        """
        if self.strict and (request.path != '/'):
            return Response(404)
        for p in self.pre:
            res = p(request)
            if isinstance(res, Request):
                request = res
            elif isinstance(res, Response):
                return res
            elif isinstance(res, str):
                return Response(content = res)
        resp = self._function(request) or Response()
        if isinstance(resp, str):
            resp = Response(content = resp)
        for a in self.post:
            resp = a(request, resp)
        return resp

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class WSGIInterface(Interface):
    def __init__(self, application: Callable[[dict, Callable], Optional[bytes]], *args, **kwargs):
        # remove 'pre' and 'post' arguments, they are unusable in WSGI standards.
        self.app = application
        self.response: Response = Response()
        kwargs.pop('pre')
        kwargs.pop('post')
        super().__init__(func = self.call, *args, **kwargs)

    def call(self, request) -> Response:
        self.app(self.get_environ(request), self.start_response)
        return self.response

    def start_response(self, status: str, header: List[Tuple[str, str]], exc_info: list = None):
        if exc_info:
            raise exc_info[0].with_traceback(exc_info[1])
        code, desc = status.split(' ', 1)
        self.response = Response(code = code, desc = desc, header = dict(header))

    @staticmethod
    def get_environ(request: Request) -> dict:
        environ = {}
        environ.update(dict(os.environ))
        environ['REQUEST_METHOD'] = request.method.upper()
        environ['SCRIPT_NAME'] = request.url.removesuffix(request.path)
        environ['PATH_INFO'] = request.path.removeprefix('/')
        environ['QUERY_STRING'] = request.query
        environ['CONTENT_TYPE'] = request.header.get('content-type') or ''
        environ['CONTENT_LENGTH'] = request.header.get('content-length') or ''
        environ['SERVER_NAME'], environ['SERVER_PORT'] = request.conn.getsockname()
        environ['SERVER_PROTOCOL'] = request.version
        map(lambda h: environ.update({f'HTTP_{h[0].upper()}': h[1]}), request.header.items())  # Set HTTP_xxx variables
        environ['wsgi.version'] = (1, 0)
        environ['wsgi.url_scheme'] = 'https' if isinstance(request.conn, SSLContext) else 'http'
        environ['wsgi.input'] = request.conn.makefile()
        environ['wsgi.errors'] = None  # unavailable now
        environ['wsgi.multithread'] = environ['wsgi.multiprocess'] = False
        environ['wsgi.run_once'] = True  # This server supports running application for multiple times
        return environ


class MethodInterface(Interface):
    """A interface class that supports specify interfaces by method"""

    def __init__(self, get: InterfaceFunction = None, head: InterfaceFunction = None, post: InterfaceFunction = None,
                 put: InterfaceFunction = None, delete: InterfaceFunction = None, connect: InterfaceFunction = None,
                 options: InterfaceFunction = None, trace: InterfaceFunction = None, patch: InterfaceFunction = None,
                 strict: bool = True, *args, **kwargs):
        self.methods = {'GET': get, 'HEAD': head, 'POST': post, 'PUT': put, 'DELETE': delete, 'CONNECT': connect,
                        'OPTIONS': options or (self.options_ if strict else options), 'TRACE': trace, 'PATCH': patch}
        super().__init__(func = self.select, *args, **kwargs)

    def select(self, request: Request) -> Optional[Response]:
        if request.method in self.methods:
            method = self.methods.get(request.method)
            if method:
                return method(request)
            else:
                return Response(405)  # disallowed request method
        else:
            return Response(400)  # incorrect request method

    def options_(self, request: Request) -> Response:
        """Default "OPTIONS" method implementation"""
        method_set = {self.get, self.head, self.post, self.put, self.delete,
                      self.connect, self.options, self.trace, self.patch}
        avaliable_methods = map(lambda x: x.__name__.upper(), filter(lambda x: bool(x), method_set))
        resp = Response(header = {'Allow': ','.join(avaliable_methods)})
        if request.header.get('Origin'):
            resp.header.update({'Access-Control-Allow-Origin': request.header['Origin']})
        return resp


DefaultInterface = Interface(lambda request: Response(content = request.get_overview()))


class Node(Interface):
    """A interface that provides a main-interface to carry sub-Interfaces."""

    def __init__(self, interface_map: Union[Callable[[], Dict[str, Interface]], Dict[str, Interface]] = None,
                 default_interface: Interface = DefaultInterface, default_resp: Response = Response(code = 500)):
        """
        :param interface_map: Initral mapping for interfaces
        :param default_interface: a special interface that actives when no interface be matched
        :param default_resp: HTTP content will be send when the function meets expections
        """
        self._map = interface_map or {}
        self._tree: Dict[str, Interface] = {}
        self.default_interface = default_interface
        super().__init__(self.select, default_resp)

    def select(self, request: Request) -> Response:
        """Select the matched interface and let it process requests"""
        interface_map = self.map
        for interface in sorted(interface_map):
            if request.path.startswith(interface + '/') or request.path == interface:
                target_interface = interface_map[interface]
                path = interface
                break
        else:
            target_interface = self.default_interface
            path = request.url
        # process 'path'
        request.path = request.path.removeprefix(path)
        return target_interface.process(request)

    @property
    def map(self) -> Dict[str, Interface]:
        """Return interface map as a dictonary"""
        return {**(self._map() if isinstance(self._map, Callable) else self._map), **self._tree}

    def bind(self, pattern: str, interface_or_method: Union[Interface, List[str]] = None, *args, **kwargs):
        r"""
        Bind a interface or function into this node.

        :param pattern: the url prefix that used to match requests.
        :param interface_or_method: If the function has been using as a normal function, the value is the Interface
            needs to bind. If the function has been using as a decorator or the value is None, the value is expected
            HTTP methods list. If the value is None, it means the Interface has no limits on methods.
        """
        if not pattern.startswith('/'):
            pattern = '/' + pattern
        if isinstance(interface_or_method, Interface):
            self._tree[pattern] = interface_or_method
            return
        else:
            def decorator(func):
                if isinstance(interface_or_method, list) and interface_or_method is not None:
                    method_list = set(map(lambda x: x.lower(), interface_or_method))  # convert mothod list to lowercase
                    parameter = dict(zip(method_list, [func] * len(method_list)))  # prepare the parameter for interface
                    self._tree[pattern] = MethodInterface(*args, **parameter, **kwargs)
                else:
                    self._tree[pattern] = Interface(func = func, *args, **kwargs)

            return decorator


class Session:
    def __init__(self, interface: Interface, request: Request):
        self.interface = interface
        self.request = request

    def execute(self):
        """Execute the interface with request"""
        with self.interface as i:
            resp = i.process(self.request)
            if resp:
                self.request.conn.sendall(resp.generate())
            self.request.conn.close()
        try:
            self.request.conn.sendall(self.interface.default_resp.generate())
        except OSError:
            pass  # interface processed successful
        finally:
            self.request.conn.close()


class Worker:
    base_type = None

    def __init__(self, request_queue: queue.Queue, timeout: float = 30):
        super().__init__()
        self.queue = request_queue
        self.running_state = False
        self.timeout = timeout

    def run(self):
        self.running_state = True
        while self.running_state:
            try:
                self.queue.get(timeout = self.timeout).execute()
            except queue.Empty:
                continue
            except KeyboardInterrupt:
                return
        return

    def __del__(self):
        print(f'{self.name} closed successful.')


class ThreadWorker(Worker, threading.Thread):
    base_type = threading.Thread
    queue_type = queue.Queue

    def __init__(self, request_queue: queue.Queue, timeout: float = 30):
        super().__init__(request_queue = request_queue, timeout = timeout)
        self.setDaemon(True)


class ProcessWorker(Worker, multiprocessing.Process):
    """This class is unavailable for some unknown reason. Don`t use it!!!"""
    base_type = multiprocessing.Process
    queue_type = multiprocessing.Queue

    def __init__(self, request_queue: multiprocessing.Queue, timeout: float = 30):
        super().__init__(request_queue = request_queue, timeout = timeout)


__all__ = ['Request', 'Response', 'Interface', 'MethodInterface', 'Node', 'HookedSocket', 'Session', 'Worker',
           'ThreadWorker', 'ProcessWorker', 'DefaultInterface']
