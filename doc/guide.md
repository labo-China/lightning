
# Guide
The guide will teach you how to use Lightning.
***
## Server
The Server class is the base unit of the package.  
You can simply create a Server instance as following:
```python
import lightning
server = lightning.Server()
```
### Running a server
Then, run it like this:
```python
server.run()
```
The server will keep blocking until you entered Ctrl+C.  
To do something while the server is running, you can use it instead:
```python
server.run(block = False)
```
### Shutting down a server
Maybe you want to stop the server for a while. You can use **interrupt()** method and restart it by calling **run()** again.
```python
server.interrupt()
# do something
server.run()  # The server will run again
```
If you want to stop the server permanently, you can use **terminate()** method  
***Note:*** Once you called **terminate()**, the server will be no longer able to run again! 
```python
server.terminate()
# do something
server.run()  # OSError will be raised here
```

