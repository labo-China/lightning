# Lightning
一个基于socket的轻量化python服务器框架

***
选择你的语言: [English](../README.md)  简体中文(当前)
***
## 安装
使用pip来安装和更新这个包: (当前不可用)  
`$ pip install -U lightning`
***
## 创建一个例子
```python
# 把这段代码保存为example.py
from lightning import Server

server = Server(('', 80))

@server.bind('/')
def hello(request):
    return f'Hello World!\nThe request is received from {request.addr}'

server.run()
```
```shell
$ python example.py
Initraling request processor...
Listening request on ('', 80)
 
```
***

## 优势
- 更小的依赖性，仅依赖于内置的包
- 更高的扩展性，你可以通过继承类以从HTTP层到socket层自定义服务器
- 更简单的开发，只需要把你的函数"扔"进Interface类里就可以实现功能

## 关于作者
我是一个来自中国的学生。如果我在上学，这个项目的开发进度可能会变得**非常慢**  
你可以在 [B站](http://space.bilibili.com/439067826) 上关注我，欢迎关注！