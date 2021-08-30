import logging
import socket
import time
from ssl import SSLContext
from threading import Thread

from . import utility, interfaces
from .structs import Interface, Worker, ThreadWorker, ProcessWorker, Session, Node, Request, Response

logging.basicConfig(level = 'INFO', format = '[%(levelname)s](%(funcName)s) %(message)s')


class Server:
    def __init__(self, server_addr: tuple[str, int] = ('', 80), max_listen: int = 100,
                 timeout: int = None, default: Interface = interfaces.DefaultInterface, max_instance: int = 4,
                 multi_status: str = 'thread', ssl_cert: str = None,
                 conn_famliy: socket.AddressFamily = socket.AF_INET):
        self.is_running = False
        self.listener = None
        self.addr = server_addr
        self.timeout = timeout
        self.max_instance = max_instance
        self.max_listen = max_listen

        self.worker_type = ThreadWorker if multi_status == 'thread' else ProcessWorker \
            if multi_status == 'process' else None  # DON`T use 'process'! It`s unavailable now.
        self.queue = self.worker_type.queue_type()
        self.processor_list: set[Worker] = set()

        self.root_node = Node(default_interface = default)
        self.bind = self.root_node.bind

        self._sock = socket.socket(conn_famliy)
        self._sock.settimeout(timeout)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if ssl_cert:
            ssl_context = SSLContext()
            ssl_context.load_cert_chain(ssl_cert)
            self._sock = ssl_context.wrap_socket(self._sock, server_side = True)
        self._sock.bind(server_addr)
        self._sock.listen(max_listen)

    def run(self, block: bool = True):
        """
        start the server\n
        :param block: if it is True, this method will be blocked until the server shutdown or critical errors occoured
        """
        self.is_running = True
        self._sock.settimeout(self.timeout)
        logging.info('Initraling request processor...')
        self.processor_list = [self.worker_type(self.queue) for _ in range(self.max_instance)]
        logging.info(f'Listening request on {self.addr}')
        self.listener = Thread(target = self.accept_request)
        self.listener.setDaemon(True)
        self.listener.start()
        print(f'Server running on {self._sock.getsockname()}. Press Ctrl+C to quit.')
        if block:
            while self.listener.is_alive():
                try:
                    time.sleep(1)
                except KeyboardInterrupt:
                    self.terminate()
                    return

    def accept_request(self):
        """Accept TCP requests from listening ports and forward it"""
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
        logging.info('Request listening stopped.')  # This should not appear in the terminal

    def handle_request(self, connection, address):
        """
        Construct an HTTP request object and put it into requests queue\n
        :param connection: a socket object which is connected to HTTP Client
        :param address: address of client socket
        """
        try:
            request = Request(addr = address,
                              **utility.parse_req(utility.recv_request_head(connection)), conn = connection)
            session = Session(self.root_node, request)
            self.queue.put(session)
        except ValueError:
            try:
                connection.sendall(Response(code = 400).generate())
            except (ConnectionAbortedError, ConnectionResetError):
                pass
        except (socket.timeout, ConnectionResetError):
            return

    def interrupt(self, timeout: float = None):
        """
        Stop the server temporarily. Use "run" method to start the server again.\n
        :param timeout: max time for waiting single active session
        """
        timeout = timeout or self.timeout or 30
        if self.is_running:
            self.is_running = False
            for t in self.processor_list:
                t.running_state = False
                t.timeout = 0
            for t in self.processor_list:
                logging.info(f'Waiting for active session {t.name}...')
                t.join(timeout)
            self._sock.settimeout(0)
        if self.listener:
            logging.info('Waiting for connection listener...')
            self.listener.join(timeout)
        logging.info('Server paused successful.')
        return

    def terminate(self):
        """
        Stop the server permanently. After running this method, the server cannot start again.
        """
        def _terminate(worker: Worker):
            if isinstance(worker, ProcessWorker):
                worker.terminate()
            else:
                worker.join(0)

        if self.is_running:
            self.is_running = False
            for t in self.processor_list:
                logging.info(f'Terminating {t.name}...')
                _terminate(t)
            self._sock.close()
        if self.listener.is_alive():
            logging.info('Terminating connection listener...')
            _terminate(self.listener)
        logging.info('Server closed successful.')

    def __enter__(self):
        self.run(block = False)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.terminate()
        return True

    def __repr__(self) -> str:
        return f'Server[{"running" if self.is_running else "closed"} on {self.addr}]'


__all__ = ['Server']
