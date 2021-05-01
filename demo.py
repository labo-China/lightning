import Server
import Structs
import time

# author: 程鹏博 景炎 2010
# date 2021-4-9

a = Server.Server(('', 80), timeout = 30)
print(f'You can visit this server at {a.addr}')


def hello_world(request: Structs.Request):
    s = f'hello world! from {request.addr}'
    return s


f = Structs.Interface(hello_world, Structs.Request)
# 访问 127.0.0.1/test 来激活这个Interface
a.bind('/test', f)
a.run()
