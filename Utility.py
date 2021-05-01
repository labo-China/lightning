def recv_request_line(conn):
    from urllib.parse import unquote
    stack = b''
    while b'\r\n' not in stack:
        current_recv = conn.recv(1)
        stack += current_recv
        if len(current_recv) < buffer:
            break
    return unquote(stack.split(b'\r\n', 1)[0].decode())


def recv_all(conn, buffer: int = 1024):
    content = b''
    content_length = buffer
    while content_length == buffer:
        c = conn.recv(buffer)
        content += c
        content_length = len(c)
    return content


def parse_request_line(content: bytes) -> dict:
    from urllib.parse import unquote
    line, extend = content.split(b'\r\n', 1)
    method, url, ver = line.decode().split(maxsplit = 2)
    path, param = url.split('?', 1) if '?' in url else url, ''
    params = {}
    for p in param.split('&'):
        x = p.split('=')
        params.update({x[0]: x[1]} if x[0] else {})
    return {'method': method, 'url': unquote(path), 'param': params, 'version': ver, 'content': extend}


def parse_content(content: bytes) -> dict:
    header, data = content.split(b'\r\n\r\n')
    headers = [tuple(h.decode().strip().split(':')) for h in header.split(b'\r\n')]
    return {'header': headers, 'content': data}


def parse_request(content: bytes) -> dict:
    line = parse_request_line(content)
    return {**line, **parse_content(line['content'])}
