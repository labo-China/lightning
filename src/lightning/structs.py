import logging
import multiprocessing
import queue
import socket
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from ssl import SSLContext
from typing import Union, Callable, Optional, Generator, Iterable

from .utility import Method
from . import utility


@dataclass
class Request:
    """Request that recevived from the parser of server"""
    addr: tuple[str, int] = field(default = ('127.0.0.1', -1))
    method: Method = field(default = 'GET')
    url: str = field(default = '/')
    version: str = field(default = 'HTTP/1.1')
    keyword: dict[str, str] = field(default_factory = dict)
    arg: set = field(default_factory = set)
    header: dict[str, str] = field(default_factory = dict)
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
        :param buffer: buffer size of content
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

    def __repr__(self) -> str:
        return f'Request[{self.method} -> {self.url}]'


@dataclass
class Response:
    """Response with normal initral function."""
    code: int = 200
    desc: str = None
    version: str = field(default = 'HTTP/1.1')
    header: dict[str, str] = field(default_factory = dict)
    timestamp: Union[datetime, int] = int(time.time())
    content: Union[bytes, str] = b''
    encoding: str = 'utf-8'

    def __post_init__(self):
        # fill request desciptions
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
                          'Server': 'Lightning',
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

    def __call__(self, *args, **kwargs):
        # provide a call method here so that Response object could act as a static Responsive object
        return self

    def __repr__(self) -> str:
        return f'Response[{self.code}]'

    def __bool__(self) -> bool:
        return self.code != 200 or self.content != b''


Sendable = Union[Response, str, bytes, None]
Responsive = Callable[[Request], Sendable]


def create_response(obj: Sendable = None, **kwargs) -> Response:
    if isinstance(obj, Response):
        return obj
    elif isinstance(obj, Union[str, bytes]):
        return Response(content = obj, **kwargs)
    else:
        return Response(**kwargs)


class Interface:
    """The main HTTP server handler."""

    def __init__(self, get_or_method: Union[dict[Method, Responsive], Responsive] = None,
                 generic: Responsive = Response(405), fallback: Responsive = Response(500),
                 pre: list[Callable[[Request], Union[Request, Sendable]]] = None,
                 post: list[Callable[[Request, Response], Sendable]] = None,
                 desc: str = None, strict: bool = False):
        r"""
        :param get_or_method: a method-responsive-style dict, if a Responsive object is given, it will be GET handler
        :param generic: default handler if no function is matched with method
        :param strict: if it is True, the interface will catch extra path in interfaces and return a 404 response
        :param desc: description about the interface. It will show instead of default message when calling __repr__
        :param pre: things to do before processing request, it will be sent as final response
        if a Response object is given
        :param post: things to do after the function processed request, they should return a Response object
        """
        if not get_or_method:
            self.methods = {}
        elif isinstance(get_or_method, dict):
            self.methods = get_or_method
        else:
            # assume it is a Responsive object
            self.methods = {'GET': get_or_method}
        self.methods: dict[Method, Responsive] = self.methods  # correct type suggestion mistakes caused by some IDEs
        self.generic = generic
        self.fallback = fallback
        self.pre = pre or []
        self.post = post or []
        self.desc = desc
        self.strict = strict

    def __enter__(self):
        return self

    def _select_method(self, request: Request) -> Sendable:
        """Return a response which is produced by specified method in request"""
        method = request.method
        if method not in Method.__args__:
            return Response(400)  # incorrect request method

        handler = self.methods.get(method)
        if handler:
            return handler(request)
        else:
            return self.generic(request)

    def process(self, request: Request) -> Response:
        """
        Let the function processes the request and returns the result\n
        :param request:  the request that will be processed
        """
        if self.strict and (request.path != '/'):
            return Response(404)

        for pre in self.pre:
            res = pre(request)
            if isinstance(res, Request):
                request = res
            elif isinstance(res, Sendable):
                return create_response(res)

        resp = create_response(self._select_method(request))
        for pst in self.post:
            resp = create_response(pst(request, resp))
        return resp

    def options_(self, request: Request) -> Response:
        """Default "OPTIONS" method implementation"""
        resp = Response(header = {'Allow': ','.join(self.methods.keys())})
        if request.header.get('Origin'):
            resp.header.update({'Access-Control-Allow-Origin': request.header['Origin']})
        return resp

    def head_(self, request: Request) -> Response:
        """Default "HEAD" method implementation"""
        if 'GET' not in self.methods:
            return Response(405)
        resp = create_response(self.methods['GET'](request))
        resp.content = b''
        return resp

    def __call__(self, *args, **kwargs):
        return self.process(*args, **kwargs)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_tb:
            traceback.print_exception(exc_type, value = exc_val, tb = exc_tb)
        return True

    def __repr__(self) -> str:
        def name(obj):
            if hasattr(obj, '__name__'):
                return obj.__name__
            return str(obj)

        template = self.__class__.__name__ + '[{}]'
        if self.desc:
            return template.format(self.desc)
        if not self.methods:
            return template.format(name(self.generic))

        method_map = []
        for method, func in self.methods.items():
            method_map.append(method.upper() + ':' + name(func))
        return template.format('|'.join(method_map))


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
        map(lambda h: environ.update({f'HTTP_{h[0].upper()}': h[1]}), request.header.items())  # Set HTTP_xxx variables
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
        :param interface_map: Initral mapping for interfaces
        :param interface_callback: the function to call whenever getting mapping in order to modify mapping dynamically
        :param default: the final interface when no interfaces were matched
        """
        super().__init__(generic = self.select, *args, **kwargs)
        self.map_static: dict[str, Interface] = interface_map or {}
        self.map_callback = interface_callback
        self.default = default
        self.last_call: str = ''

    def select(self, request: Request) -> Response:
        """Select the matched interface and let it process requests"""
        interface_map = self.get_map()
        for req_path in sorted(interface_map):
            if request.path.startswith(req_path + '/') or request.path == req_path:
                target = interface_map[req_path]
                path = req_path
                break
        else:
            target = self.default
            path = request.url
        # process 'path'
        request.path = request.path.removeprefix(path)
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

        :param pattern: the url prefix that used to match requests.
        :param interface_or_method: If the function is called as a normal function, the value is the Interface
            needs to bind. If the function is called as a decorator or the value is None, the value is expected
            HTTP methods list. If the value is None, it means the Interface will be a GET handler by default.
        """
        if not pattern.startswith('/'):
            pattern = '/' + pattern

        if isinstance(interface_or_method, Interface):  # called as a normal function
            self.map_static[pattern] = interface_or_method
            logging.info(f'{interface_or_method} is bind on {pattern}')
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


class Session:
    def __init__(self, interface: Interface, request: Request):
        self.interface = interface
        self.request = request

    def execute(self):
        """Execute the interface with request"""
        with self.interface as i:
            resp = i.process(self.request)
            logging.info(f'{i} processed successful with {resp}')
            if resp and not getattr(self.request.conn, '_closed'):
                self.request.conn.sendall(resp.generate())
            self.request.conn.close()
        try:
            self.request.conn.sendall(self.interface.fallback(self.request).generate())
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
        logging.info(f'{self.name} is running')
        self.running_state = True
        while self.running_state:
            try:
                self.queue.get(timeout = self.timeout).execute()
            except queue.Empty:
                continue
        return

    def __del__(self):
        logging.info(f'{self.name} closed successful.')


class ThreadWorker(Worker, threading.Thread):
    base_type = threading.Thread
    queue_type = queue.Queue

    def __init__(self, request_queue: queue.Queue, timeout: float = 30):
        super().__init__(request_queue = request_queue, timeout = timeout)
        self.daemon = True


class ProcessWorker(Worker, multiprocessing.Process):
    """This class is unavailable for some unknown reason. Don`t use it!!!"""
    base_type = multiprocessing.Process
    queue_type = multiprocessing.Queue

    def __init__(self, request_queue: multiprocessing.Queue, timeout: float = 30):
        super().__init__(request_queue = request_queue, timeout = timeout)


__all__ = ['Request', 'Response', 'Interface', 'Node', 'Session',
           'Worker', 'ThreadWorker', 'ProcessWorker', 'WSGIInterface']
