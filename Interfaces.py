from Structs import Request, BytesInterface


class DefaultInterface(BytesInterface):
    def __init__(self):
        super().__init__(lambda x: None)

    def execute(self, request: Request):
        from datetime import datetime
        req = repr(request)
        time = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
        return f'HTTP/1.1 200 OK\r\nDate:{time}\r\n' \
               f'Content-Length:{len(req.encode("utf-8"))}\r\n' \
               f'Content-Type:text/html;charset=utf8\r\n\r\n{req}'.encode(
                'utf-8')
