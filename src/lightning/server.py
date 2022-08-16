import multiprocessing
import threading
import queue

import socket
import ipaddress
import select
from ssl import SSLContext

import time
import datetime
import traceback
import logging

from . import utility
from .structs import WorkerType, Node, Request, Response, worker_run


class Server:
    """The HTTP server class"""
    def __init__(self, server_addr: tuple[str, int] = ('', 80), max_listen: int = 100,
                 timeout: int = None, max_worker: int = 4, multi_status: str = 'thread', ssl_cert: str = None,
                 sock: socket.socket = None, *args, **kwargs):
        """
        :param server_addr: the address of server (host, port)
        :param max_listen: max size of listener queue
        :param timeout: timeout for interrupting server
        :param max_worker: max size of worker queue
        :param multi_status: specify if this server use single processing or mulit-thread processing
        :param ssl_cert: SSL certificate content
        :param conn_famliy: address format
        :param sock: a given socket
        """
        self.is_running = False
        self.listener = None
        self.addr = sock.getsockname() if sock else server_addr
        self.max_worker = max_worker
        self._worker_index = 1

        if multi_status == 'process':
            self.worker_type = multiprocessing.Process
            self.queue = multiprocessing.Queue()
        elif multi_status == 'thread':
            self.worker_type = threading.Thread
            self.queue = queue.Queue()
        else:
            raise ValueError(f'"{multi_status}" is not a valid multi_status flag. Use "process" or "thread" instead.')

        self.worker_pool: set[WorkerType] = set()
        self._is_child = self._check_process()
        self.root_node = Node(*args, **kwargs)
        self.bind = self.root_node.bind  # create an alias
        if self._is_child:
            return  # not to initialize socket

        if sock:
            self._sock = sock
        else:
            self._sock = socket.socket(self._get_socket_family())
            self._sock.settimeout(timeout)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(server_addr)
            self._sock.listen(max_listen)

        if ssl_cert:
            ssl_context = SSLContext()
            ssl_context.load_cert_chain(ssl_cert)
            self._sock = ssl_context.wrap_socket(self._sock, server_side = True)

    def _get_socket_family(self, default = socket.AF_INET):
        if not self.addr[0]:
            return default
        addr = ipaddress.ip_address(self.addr[0])
        return socket.AF_INET if isinstance(addr, ipaddress.IPv4Address) else socket.AF_INET6

    def _check_process(self):
        """Check whether the server is started as a child process"""
        tester = socket.socket(self._get_socket_family())
        try:
            tester.bind(self.addr)
        except OSError:
            tester.close()
            if issubclass(self.worker_type, multiprocessing.process.BaseProcess):
                logging.info(f'The server is seemed to be started as a child process. Ignoring all operations...')
                return True
            else:
                logging.error(f'The target address {self.addr} is unavailable')
        tester.close()
        return False

    def _create_worker(self):
        name = f'Worker[{self._worker_index}]'
        worker = self.worker_type(target = worker_run, name = name, daemon = True,
                                  kwargs = {'name': name, 'root_node': self.root_node, 'req_queue': self.queue})
        self._worker_index += 1
        return worker

    def run(self, block: bool = True):
        """
        start the server\n
        :param block: if it is True, this method will be blocked until the server shutdown or critical errors occoured
        """
        if self._is_child:
            logging.warning('The server is seemed to be started as a child process. The server will not run')
            return
        self.is_running = True
        logging.info('Creating request processors...')
        self.worker_pool = set(self._create_worker() for _ in range(self.max_worker))
        logging.info(f'Listening request on {self.addr}')
        self.listener = threading.Thread(target = self.accept_request)
        self.listener.daemon = True
        self.listener.start()
        # self.handler =
        print(f'Server running on {self._sock.getsockname()}. Press Ctrl+C to quit.')
        if block:
            while self.listener.is_alive():
                try:
                    time.sleep(1)
                except KeyboardInterrupt:
                    self.terminate()
                    return

    def accept_request(self):
        """Accept TCP requests from listening ports"""
        for p in self.worker_pool:
            p.start()
        while self.is_running:
            try:
                connection, address = self._sock.accept()
                logging.debug(f'received connection from {address}')
            except socket.timeout:
                continue
            except OSError:
                return  # Server has shut down
            self.handle_request(connection, address)
        logging.info('Request listening stopped.')  # This should not appear in the terminal

    def handle_request(self, connection, address):
        """
        Construct an HTTP request object and put it into the request queue\n
        :param connection: a socket object which is connected to HTTP Client
        :param address: address of client socket
        """
        try:
            request = Request(addr = address,
                              **utility.parse_req(utility.recv_request_head(connection)), conn = connection)
            self.queue.put(request)
        except ValueError:
            traceback.print_exc()
            try:
                connection.sendall(Response(code = 400).generate())
            except (ConnectionAbortedError, ConnectionResetError):
                pass
        except (socket.timeout, ConnectionResetError):
            return

    def interrupt(self, timeout: float = 30):
        """
        Stop the server temporarily. Call run() to start the server again.\n
        :param timeout: max time for waiting single active session
        """
        if self.is_running:
            self._worker_index = 1
            logging.info(f'Pausing {self}')
            self.is_running = False
            for t in self.worker_pool:
                logging.info(f'Waiting for active session {t.name}...')
                t.join(timeout)
            # self._sock.settimeout(0)
        else:
            logging.warning('The server has already stopped, pausing it will not take any effects.')
            return
        if self.listener:
            logging.info('Waiting for connection listener...')
            self.listener.join(timeout)
        logging.info(f'{self} paused successfully.')
        return

    def terminate(self):
        """
        Stop the server permanently. After running this method, the server cannot start again.
        """
        def _terminate(worker: WorkerType):
            worker.join(0)
            if self.worker_type == multiprocessing.Process:
                worker.close()

        if self.is_running:
            logging.info(f'Terminating {self}')
            self.is_running = False
            for t in self.worker_pool:
                logging.info(f'Terminating {t.name}...')
                _terminate(t)
            self._sock.close()
        else:
            logging.warning('The server has already stopped.')
            return
        if self.listener.is_alive():
            logging.info('Terminating connection listener...')
            _terminate(self.listener)
        logging.info(f'{self} closed successfully.')

    def __enter__(self):
        self.run(block = False)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.is_running:
            self.terminate()
        return True

    def __del__(self):
        if self.is_running:
            self.terminate()

    def __repr__(self) -> str:
        return f'Server[{"running" if self.is_running else "closed"} on {self.addr}]'


__all__ = ['Server']
