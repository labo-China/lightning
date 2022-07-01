from src.lightning import Request, Response, Server, StorageView, Debug

# author: 程鹏博 景炎 2010
# date 2021-4-9
a = Server()
print(f'You can visit this server at {a.addr}')


@a.bind('/test')
def hello_world(request: Request) -> Response:
    s = f'hello world! from {request.addr}'
    return Response(content = s)  # this response is sended by Respose class


@a.bind('/raw_test', ['GET', 'POST'])
def raw_hello(request: Request):
    s = f'hello world! from {request.addr}'
    request.conn.sendall(Response(content = s).generate())  # this response is sended by base socket function


@a.bind('/crash')
def crash(request: Request):
    return str(1 / 0) + request.path


a.bind('/storage', StorageView('C:/'))
a.bind('/debug', Debug)
if __name__ == '__main__':
    a.run()
