import socket


def recv_request_line(conn: socket.socket):
    stack = b''
    while b'\r\n' not in stack:
        current_recv = conn.recv(1)
        stack += current_recv
        if not current_recv:
            break
    return stack.split(b'\r\n', 1)[0]


def recv_all(conn, buffer: int = 1024):
    content = b''
    content_length = buffer
    while content_length == buffer:
        c = conn.recv(buffer)
        content += c
        content_length = len(c)
    return content


def parse_request_line(line: bytes) -> dict:
    from urllib.parse import unquote
    method, url, ver = unquote(line.decode()).split(maxsplit = 2)
    path, param = url.split('?', 1) if '?' in url else [url, '']
    params = dict([tuple(p.split('=')) for p in param.split('&') if '=' in p])
    return {'method': method, 'url': unquote(path), 'param': params, 'version': ver}


def parse_content(content: bytes) -> dict:
    header, data = content.split(b'\r\n\r\n')
    headers = [tuple(h.decode().strip().split(':')) for h in header.split(b'\r\n')]
    return {'header': headers, 'content': data}


def parse_request(content: bytes) -> dict:
    line = parse_request_line(content)
    return {**line, **parse_content(line['content'])}
