import queue
import socket
import threading
from typing import Tuple

from . import utility, interfaces
from .structs import Interface, Processer, Session, Node, Request, Response


class Server:
    def __init__(self, server_addr: Tuple[str, int], listen_limit: int = 100,
                 timeout: int = 0, default: Interface = interfaces.DefaultInterface, thread_limit: int = 4,
                 conn_famliy: socket.AddressFamily = socket.AF_INET):
        self.is_running = False
        self.queue = queue.Queue()
        self.processer_list = [Processer(self.queue) for _ in range(thread_limit)]
        self.addr = server_addr

        self.root_node = Node(default_interface = default)
        self.bind = self.root_node.bind

        self._sock = socket.socket(conn_famliy)
        if timeout:
            self._sock.settimeout(timeout)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(server_addr)
        self._sock.listen(listen_limit)

    def run(self, block: bool = True):
        self.is_running = True
        t = threading.Thread(target = self.accept_request)
        t.setDaemon(True)
        t.start()
        if block:
            t.join()
        else:
            return t

    def accept_request(self):
        for p in self.processer_list:
            p.start()
        while self.is_running:
            try:
                connection, address = self._sock.accept()
            except socket.timeout:
                continue
            self.handle_request(connection, address)
        print('Request listening stopped.')

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

    def interrupt(self):
        if self.is_running:
            self.is_running = False
            for t in self.processer_list:
                t.running_state = False
                t.timeout = 0
            for t in self.processer_list:
                print(f'Waiting for active session {t.name}...')
                t.join(self.timeout)
        print('Server closed successfully.')
        self._sock.settimeout(0)
        self._sock.close()
        return
