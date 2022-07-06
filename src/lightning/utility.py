import socket
from urllib.parse import unquote
from typing import Literal


def format_header(header: str) -> str:
    return '-'.join(map(lambda x: x.capitalize(), header.split('-')))


def recv_request_line(conn: socket.socket) -> bytes:
    stack = conn.recv(16)
    while b'\r\n' not in stack:
        current_recv = conn.recv(1)
        stack += current_recv
        if not current_recv:
            break
    return stack.split(b'\r\n')[0]


def recv_request_head(conn: socket.socket) -> bytes:
    stack = conn.recv(4)
    while b'\r\n\r\n' not in stack:
        current_recv = conn.recv(1)
        stack += current_recv
        if not current_recv:
            break
    return stack.split(b'\r\n\r\n')[0]


def recv_all(conn: socket.socket, buffer: int = 1024) -> bytes:
    content = b''
    c = True
    while c:
        c = conn.recv(buffer)
        content += c
    return content


def parse_req(content: bytes) -> dict:
    line, *head = content.decode().split('\r\n')
    method, uv = line.split(' ', 1)
    url, ver = uv.rsplit(' ', 1)

    url = unquote(url)
    path, param = url.split('?', 1) if '?' in url else [url, '']
    path = path + '/' if not path.endswith('/') else path

    keyword, arg = {}, set()
    for p in param.split('&'):
        if '=' in p:
            k, w = p.split('=')
            keyword[k] = w
        else:
            arg.add(p)

    header = {}
    for h, v in (h.split(':', 1) for h in head):
        header[format_header(h.strip())] = v.strip()

    return {'method': method, 'url': path, 'keyword': keyword,
            'arg': arg, 'version': ver, 'header': header, 'query': '?' + param if param else ''}


HTTP_CODE = {
    100: "Continue",
    101: "Switching Protocols",
    102: "Processing",
    200: "OK",
    201: "Created",
    202: "Accepted",
    203: "Non-Authoritative Information",
    204: "No Content",
    205: "Reset Content",
    206: "Partial Content",
    207: "Multi-Status",
    300: "Multiple Choices",
    301: "Moved Permanently",
    302: "Move Temporarily",
    303: "See Other",
    304: "Not Modified",
    305: "Use Proxy",
    306: "Switch Proxy",
    307: "Temporary Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    402: "Payment Required",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    406: "Not Acceptable",
    407: "Proxy Authentication Required",
    408: "Request Timeout",
    409: "Conflict",
    410: "Gone",
    411: "Length Required",
    412: "Precondition Failed",
    413: "Request Entity Too Large",
    414: "Request-URI Too Long",
    415: "Unsupported Media Type",
    416: "Requested Range Not Satisfiable",
    417: "Expectation Failed",
    418: "I'm a teapot",
    421: "Misdirected Request",
    422: "Unprocessable Entity",
    423: "Locked",
    424: "Failed Dependency",
    425: "Too Early",
    426: "Upgrade Required",
    449: "Retry With",
    451: "Unavailable For Legal Reasons",
    500: "Internal Server Error",
    501: "Not Implemented",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
    505: "HTTP Version Not Supported",
    506: "Variant Also Negotiates",
    507: "Insufficient Storage",
    509: "Bandwidth Limit Exceeded",
    510: "Not Extended",
    600: "Unparseable Response Headers"
}
Method = Literal['GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'CONNECT', 'TRACE', 'OPTIONS']
__all__ = ['recv_request_line', 'recv_request_head', 'recv_all', 'parse_req', 'HTTP_CODE', 'Method']
