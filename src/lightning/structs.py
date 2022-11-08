import copy
import logging
import pathlib
import socket
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Union, Callable, Generator

from .utility import Method, recv_all, HTTP_CODE, CaseInsensitiveDict


@dataclass
class Request:
    """
    Request object which include HTTP headers and parsed request line
    Note: the object will only receive HTTP head.
    To get the request body, call content() or iter_content()
    """
    addr: tuple[str, int] = field(default = ('127.0.0.1', -1))
    method: Method = field(default = 'GET')
    url: str = field(default = '/')
    version: str = field(default = 'HTTP/1.1')
    keyword: dict[str, str] = field(default_factory = dict)
    arg: set = field(default_factory = set)
    header: CaseInsensitiveDict[str, str] = field(default_factory = CaseInsensitiveDict)
    query: str = field(default = '')
    conn: socket.socket = None
    path: str = ''

    def __post_init__(self):
        """Set path to url if path not specfied"""
        self.path = self.path or self.url

    def content(self, buffer: int = 1024) -> bytes:
        """
        Get request content.\n
        :param buffer: buffer size of content
        """
        return recv_all(conn = self.conn, buffer = buffer)

    def iter_content(self, buffer: int = 1024) -> Generator[bytes, None, None]:
        """
        Get request content as an iterator.\n
        :param buffer: buffer size of content
        """
        content = True
        while content:
            content = self.conn.recv(buffer)
            yield content

    def generate(self) -> bytes:
        """
        Generate the original request data from known request.
        The generated data might be INCONSISTENT with original request
        """
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
    r"""
    Response object which include HTTP header and response body
    Note: you cannot create an instance by using Response('str_or_bytes')
    For these cases, use Response.create_from(obj) instead.
    """
    code: int = 200
    desc: str = None
    version: str = field(default = 'HTTP/1.1')
    header: dict[str, str] = field(default_factory = dict)
    timestamp: Union[datetime, int] = field(default_factory = lambda: int(time.time()))
    content: Union[bytes, str] = b''
    encoding: str = 'utf-8'

    def __post_init__(self):
        # fill request desciptions
        if not self.desc:
            self.desc = HTTP_CODE[self.code]
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
        """Returns encoded HTTP response data."""
        hd = '\r\n'.join([': '.join([h, self.header[h]]) for h in self.header])
        text = f'{self.version} {self.code} {self.desc}\r\n' \
               f'{hd}\r\n\r\n'
        return text.encode(self.encoding) + self.content

    @staticmethod
    def create_from(obj: Union['Sendable', int], **kwargs):
        """Convert a Sendable object into a Response object"""
        if obj is None:
            return None
        if isinstance(obj, Response):
            return obj
        elif isinstance(obj, (str, bytes)):
            return Response(content = obj, **kwargs)
        else:
            return Response(obj, **kwargs)

    def __call__(self, *args, **kwargs):
        # provide a call method here so that Response object could act as a static Responsive object
        return self

    def __repr__(self) -> str:
        return f'Response[{self.code}]'

    def __bool__(self) -> bool:
        return self.code != 200 or self.content != b''


Sendable = Union[Response, str, bytes, None]
Responsive = Callable[[Request], Sendable]


class Interface:
    """The HTTP server handler. It produces Responses to send."""

    def __init__(self, get_or_method: Union[dict[Method, Responsive], Responsive] = None,
                 generic: Responsive = Response(405), fallback: Responsive = Response(500),
                 fpre: list[Callable[[Request], Union[Request, Sendable]]] = None,
                 fpost: list[Callable[[Request, Response], Sendable]] = None,
                 desc: str = None, strict: bool = False):
        r"""
        :param get_or_method: a method-responsive-style dict. If a Responsive object is given, it will be GET handler
        :param generic: the default handler if no function is matched with request method
        :param fallback: function to call when an Exception is raised during processing requests
            it`s return value will be the final response
        :param strict: whether the interface will catch extra path in interfaces and return a 404 response
        :param desc: description about the interface. It will show instead of default message when calling __repr__
        :param fpre: things to do before processing request, it will be sent as final response
            if a Response object is returned
        :param fpost: things to do after the function processed request
        """
        if isinstance(get_or_method, dict):
            for m in get_or_method:
                setattr(self, m.lower(), get_or_method[m])
        elif isinstance(get_or_method, Callable):
            setattr(self, 'get', get_or_method)
        self.default_methods = {'HEAD': self.head_, 'OPTIONS': self.options_}
        self.generic = generic
        self._fallback = fallback
        self.fpre = fpre or []
        self.fpost = fpost or []
        self.desc = desc
        self.strict = strict

    @staticmethod
    def create_from(obj: Union['Interface', Responsive], **kwargs):
        """Convert a Responsive object into an Interface object"""
        if isinstance(obj, Interface):
            return obj
        elif hasattr(obj, '__call__'):
            return Interface(obj, **kwargs)
        else:
            raise ValueError(f'{obj} is not responsive nor callable')

    def _select_method(self, request: Request) -> Sendable:
        """Return a response which is produced by specified method in request"""
        method = request.method
        if method not in Method.__args__:
            logging.warning(f'Request method {method} is invaild. Sending 400-Response instead.')
            return Response(400)  # incorrect request method

        if hasattr(self, method.lower()):
            return getattr(self, method.lower())(request)
        elif method in self.default_methods:
            return self.default_methods[method](request)
        else:
            return self.generic(request)

    def find_methods(self):
        return tuple(m for m in Method.__args__ if hasattr(self, m.lower()))

    def fallback(self, request: Request) -> Response:
        return Response.create_from(self._fallback(request))

    def process(self, request: Request) -> Response:
        """
        Let target function process the request and return the result\n
        *Note*: You **MUST NOT** override this method\n
        :param request: the request to process
        """
        if self.strict and (request.path != ''):
            logging.warning(f'Request path {request.path} is out of root directory. Sending 404-Response instead')
            return Response(404)

        for pre in self.fpre:
            res = pre(request)
            if isinstance(res, Request):
                request = res
            elif isinstance(res, Sendable):
                return Response.create_from(res)

        resp = Response.create_from(self._select_method(request))
        for pst in self.fpost:
            resp = Response.create_from(pst(request, resp))
        return resp

    def options_(self, request: Request) -> Response:
        """Default "OPTIONS" method implementation"""
        resp = Response(header = {'Allow': ','.join(self.find_methods())})
        if request.header.get('Origin'):
            resp.header.update({'Access-Control-Allow-Origin': request.header['Origin']})
        return resp

    def head_(self, request: Request) -> Response:
        """Default "HEAD" method implementation"""
        if not hasattr(self, 'get') and self.generic == Response(405):
            return Response(405)
        request.method = 'GET'
        resp = Response.create_from(self.process(request))
        resp.content = b''
        return resp

    def __call__(self, *args, **kwargs):
        return self.process(*args, **kwargs)

    def __repr__(self) -> str:
        def name(obj):
            if hasattr(obj, '__name__'):
                return obj.__name__
            return str(obj)

        template = self.__class__.__name__ + '[{}]'
        if self.desc:
            return template.format(self.desc)
        if not self.find_methods():
            return template.format(name(self.generic))

        method_map = []
        for method in self.find_methods():
            method_map.append(method.upper() + ':' + name(getattr(self, method.lower())))
        return template.format('|'.join(method_map))


class Node(Interface):
    """An interface that provides a main-interface to carry sub-Interfaces."""

    def __init__(self, interface_map: dict[str, Interface] = None,
                 interface_callback: Callable[[], dict[str, Interface]] = None,
                 default: Responsive = Response(404), *args, **kwargs):
        """
        :param interface_map: initral mapping for interfaces
        :param interface_callback: the function to call whenever getting mapping in order to modify mapping dynamically
        :param default: the default root interface
        """
        super().__init__(generic = self._process, *args, **kwargs)
        self.map_static: dict[str, Interface] = interface_map or {}
        self.map_callback = interface_callback
        self.bind('/', default)
        self.last_call: str = ''

    def select_target(self, request: Request) -> tuple[Responsive, Request]:
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
            raise ValueError(f'Path "{req_path}" is not a valid path')

    def _process(self, request: Request) -> Sendable:
        target, request = self.select_target(request)
        self.last_call = repr(target)
        return target(request)

    def get_map(self) -> dict[str, Interface]:
        """Return interface map as a dictonary"""
        if not self.map_callback:
            return self.map_static
        return dict({**self.map_static, **self.map_callback()})

    def bind(self, pattern: str, interface_or_method: Union[Responsive, list[Method]] = None, *args, **kwargs):
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
            if self.map_static:  # avoid log event for binding default interface
                logging.info(f'{interface_or_method} is bound on {pattern}')
            self.map_static[pattern] = interface_or_method
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


__all__ = ['Request', 'Response', 'Interface', 'Responsive', 'Sendable', 'Node']
