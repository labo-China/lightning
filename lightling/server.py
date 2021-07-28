import queue
import socket
import threading
from typing import Tuple, List
from . import utility, interfaces
from .structs import Interface, Processer, Session, Node, Request, Response


class Server:
    def __init__(self, server_addr: Tuple[str, int], max_listen: int = 100,
                 timeout: int = None, default: Interface = interfaces.DefaultInterface, max_thread: int = 4,
                 conn_famliy: socket.AddressFamily = socket.AF_INET):
        self.is_running = False
        self.queue = queue.Queue()
        self.processor_list: List[Processer] = []
        self.listener = None
        self.addr = server_addr
        self.timeout = timeout
        self.max_thread = max_thread
        self.max_listen = max_listen

        self.root_node = Node(default_interface = default)
        self.bind = self.root_node.bind

        self._sock = socket.socket(conn_famliy)
        self._sock.settimeout(timeout)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(server_addr)
        self._sock.listen(max_listen)

    def run(self, block: bool = True):
        self.is_running = True
        self._sock.settimeout(self.timeout)
        print('Initraling request processor...')
        self.processor_list = [Processer(self.queue) for _ in range(self.max_thread)]
        print(f'Listening request on {self.addr}')
        self.listener = threading.Thread(target = self.accept_request)
        self.listener.setDaemon(True)
        self.listener.start()
        if block:
            self.listener.join()

    def accept_request(self):
        for p in self.processor_list:
            p.start()
        while self.is_running:
            try:
                connection, address = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return  # Server has shut down
            self.handle_request(connection, address)
        print('Request listening stopped.')  # This should not appear in the terminal

    def handle_request(self, connection, address):
        try:
            request = Request(addr = address,
                              **utility.parse_req(utility.recv_request_head(connection)), conn = connection)
            task = Session(self.root_node, request, connection)
            self.queue.put(task)
        except ValueError:
            try:
                connection.sendall(Response(code = 400).generate())
            except (ConnectionAbortedError, ConnectionResetError):
                pass
        except (socket.timeout, ConnectionResetError):
            return

    def interrupt(self, timeout: float = None):
        timeout = timeout or self.timeout or 30
        if self.is_running:
            self.is_running = False
            for t in self.processor_list:
                t.running_state = False
                t.timeout = 0
            for t in self.processor_list:
                print(f'Waiting for active session {t.name}...')
                t.join(timeout)
            self._sock.settimeout(0)
        if self.listener:
            print('Waiting for connection listener...')
            self.listener.join(timeout)
        print('Server paused successful.')
        return

    def terminate(self):
        if self.is_running:
            self.is_running = False
            for t in self.processor_list:
                print(f'Terminating {t.name}...')
                t.join(0)
            self._sock.close()
        if self.listener.is_alive():
            print('Terminating connection listener...')
            self.listener.join(0)
        print('Server closed successful.')

    def __repr__(self) -> str:
        return f'Server[{"running" if self.is_running else "closed"} on {self.addr}]'
