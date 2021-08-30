# Lightning
A socket-based lightly python server framework
***
Choose your language: English(current)  [简体中文](doc/zh-cn.md)
***
## Install
Install and update with pip:  
`$ pip install -U lightning-server`
***
## Create an Example
```python
# save this as example.py
import lightning

server = lightning.Server()

@server.bind('/')
def hello(request):
    return f'Hello World!\nThe request is received from {request.addr}'

server.run()
```
```shell
$ python example.py
Server running on ('0.0.0.0', 80). Press Ctrl+C to quit. 
```
***

## Advantage
- Fewer dependencies, only depends on built-in modules
- High extensibility, you can control the server from http-layer to socket-layer by inheriting classes
- Easily developing, just drop your functions into Interface class to implement features

## About Author
I am a middle student from China. If I am at school, the developing progress may be **very slow**. Sorry :(  
You can subscribe me on [bilibili](http://space.bilibili.com/439067826). Welcome!