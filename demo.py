import logging
import os

from src.lightning import Request, Response, Server, StorageView, Echo

logging.basicConfig(level = 'INFO', format = '[%(levelname)s](%(funcName)s) %(message)s')
a = Server()
print(f'You can visit this server at {a.addr}')


@a.bind('/test')
def hello_world(request: Request) -> Response:
    s = f'Hello world! from {request.addr}⚡'
    return Response(content = s)


@a.bind('/byte_test')  # The interface sends data in the form of bytes
def binary_test(request: Request):
    b = bytes(f'Hello world! from {request.addr}⚡', encoding = 'utf-8')
    return Response(content = b)


@a.bind('/raw_test')  # The interface sends data by directly control socket object
def raw_test(request: Request):
    s = f'Hello world! from {request.addr}⚡'
    request.conn.sendall(Response(content = s).generate())  # this response is sended by base socket function


@a.bind('/method_test', ['POST'])  # The interface could only be accessed by using POST method
def method_test(request: Request):
    s = f'Hello world! from {request.addr}⚡'
    return Response(content = s)


@a.bind('/strict_test', strict = True)  # The interface doesn`t allow any extra path
def strict_test(request: Request):
    s = f'Hello world! from {request.addr}⚡'
    return Response(content = s)


@a.bind('/crash_test')  # The interface will raise an error while processing
def crash_test(request: Request):
    s = f'Hello world! from {request.addr}⚡' + str(1 / 0)
    return Response(content = s)


@a.bind('/fallback_test', fallback = hello_world)
# The interface will raise an error but return a normal response from fallback
def fallback_test(request: Request):
    s = f'Hello world! from {request.addr}⚡' + str(1 / 0)
    return Response(content = s)


a.bind('/storage', StorageView(os.path.expanduser('~')))
a.bind('/static_test', Response(204))
a.bind('/echo', Echo)
if __name__ == '__main__':
    a.run()
