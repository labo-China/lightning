from src.lightning import Request, Response, Server, Folder


# author: 程鹏博 景炎 2010
# date 2021-4-9
a = Server(('', 80), max_instance = 8)
print(f'You can visit this server at {a.addr}')


@a.bind('/test')
def hello_world(request: Request) -> Response:
    s = f'hello world! from {request.addr}'
    return Response(content = s)  # this response is sended by Respose class


@a.bind('/raw_test', ['get', 'post'])
def raw_hello(request: Request):
    s = f'hello world! from {request.addr}'
    request.conn.sendall(Response(content = s).generate())  # this response is sended by base socket function


a.bind('/dl', Folder('C:/'))
if __name__ == '__main__':
    a.run()
