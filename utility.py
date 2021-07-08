import socket
from urllib.parse import unquote


def recv_request_head(conn: socket.socket):
    timeout = conn.gettimeout()
    conn.settimeout(0.1)
    stack = conn.recv(1)
    conn.settimeout(timeout)
    while b'\r\n\r\n' not in stack:
        current_recv = conn.recv(1)
        stack += current_recv
        if not current_recv:
            break
    return stack.split(b'\r\n\r\n', 1)[0]


def recv_all(conn, buffer: int = 1024):
    content = b''
    content_length = buffer
    while content_length == buffer:
        c = conn.recv(buffer)
        print(c)
        content += c
        content_length = len(c)
    return content


def parse_req(content: bytes):
    line, *head = content.decode().split('\r\n')
    method, uv = line.split(' ', 1)
    url, ver = uv.rsplit(' ', 1)
    url = unquote(url)
    path, param = url.split('?', 1) if '?' in url else [url, '']
    keyword, arg = {}, set()
    for p in param.split('&'):
        if '=' in p:
            k, w = p.split('=')
            keyword[k] = w
        else:
            arg.add(p)
    header = dict(h.replace(' ', '').split(':', 1) for h in head)

    return {'method': method, 'url': path, 'keyword': keyword,
            'arg': arg, 'version': ver, 'header': header}


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
