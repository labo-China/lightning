import multiprocessing
import threading
import queue

import socket
import ipaddress
from typing import Union, Optional
from ssl import SSLContext

import time
import datetime
import traceback
import logging

from . import utility
from .structs import Request, Response
from .interfaces import Node

WorkerType = Union[threading.Thread, multiprocessing.Process]


class ConnectionPool:
    def __init__(self, timeout: int = 75, max_conn: int = float('inf'),
                 clean_threshold: int = None):
        self.timeout = timeout
        self.max_conn = max_conn
        if clean_threshold is None:
            self.clean_threshold = int((self.max_conn if self.max_conn != float('inf') else 100) * 0.8)

        self._pool: dict[socket.socket, list] = {}
        self.active_conn = set()
        self._lock = threading.Lock()

    def add(self, conn: socket.socket, addr: tuple):
        with self._lock:
            if len(self._pool) > self.clean_threshold:
                logging.debug(f'connections count ({len(self._pool)}) has reached threshold. performing cleaning')
                self._clean()
            self.active_conn.add(conn)
            self._pool[conn] = [time.time() + self.timeout, addr]
        logging.debug(f'{utility.format_socket(conn)} has been added to connection pool')

    def _is_expired(self, conn: socket.socket, timeout: int = None):
        timeout = timeout or self.timeout
        return (self._pool[conn][0] - self.timeout + timeout) < time.time() or getattr(conn, '_closed')

    def is_expired(self, conn: socket.socket, timeout: int = None):
        return conn not in self or self._is_expired(conn, timeout)

    def _remove(self, conn: socket.socket):
        if conn in self.active_conn:
            self.active_conn.remove(conn)
        self._pool.pop(conn)
        if not getattr(conn, '_closed'):
            logging.debug(f'{utility.format_socket(conn)} is removed from connection pool')

    def _clean(self):
        entries = set(self._pool.keys())
        logging.debug(f'performing cleaning in connection pool: {set(utility.format_socket(c) for c in entries)}')
        for conn in entries:
            if getattr(conn, '_closed') or self._is_expired(conn):
                self._remove(conn)

    def _test_readable(self, conn: socket.socket):
        prev_timeout = conn.gettimeout()
        conn.settimeout(0.01)
        try:
            data = conn.recv(4)
            if data:
                return conn, self._pool[conn][1], data
        except (TimeoutError, BlockingIOError):
            return None
        finally:
            conn.settimeout(prev_timeout)

    def _get(self) -> tuple[socket.socket, tuple, bytes]:
        while True:
            with self._lock:
                for conn in self.active_conn:
                    result = self._test_readable(conn)
                    if result:
                        return result
            time.sleep(0.01)

    def get(self) -> tuple[socket.socket, tuple, bytes]:
        ret = self._get()
        self.active_conn.remove(ret[0])  # to prevent a used socket being used by others again
        self._clean()
        return ret

    def __contains__(self, item):
        return item in self._pool


def run_worker(name: str, root_node: Node, req_queue: Union[queue.Queue, multiprocessing.Queue],
               conn_pool: ConnectionPool, timeout: float = 5):
    def process(req: Request):
        resp = root_node.process(req)

        if getattr(req.conn, '_closed'):
            if resp is not None:
                logging.warning(f'Connection from {req.addr} was closed before sending response')
            return

        rest = utility.recv_all(req.conn)  # receive all unused data to make keep-alive working
        if rest:
            logging.warning(f'Unused data found in {utility.format_socket(req.conn)}: {rest}')

        if resp is None:
            logging.info(f'{root_node} -> ... -> {utility.format_socket(req.conn)}')
            conn_pool.add(req.conn, req.addr)
            return

        close_conn = False
        if resp.header.get('Connection') == 'close' or req.header.get('Connection') == 'close':
            close_conn = True
        else:
            if not conn_pool.is_expired(req.conn):
                resp.header['Connection'] = 'keep-alive'
                resp.header['Keep-Alive'] = f'timeout={conn_pool.timeout}'
            else:
                close_conn = True
                resp.header['Connection'] = 'close'

        resp_data = resp.generate()
        req.conn.sendall(resp_data)
        logging.info(f'{root_node} -> {resp} -> {utility.format_socket(req.conn)}')

        if close_conn:
            req.conn.close()
        else:
            conn_pool.add(req.conn, req.addr)

    logging.info(f'{name} is running')
    while True:
        try:
            request = req_queue.get(timeout = timeout)
        except queue.Empty:
            continue
        else:
            try:
                process(request)
            except Exception:
                traceback.print_exc()
                logging.warning('It seems that the process has not completed yet. Sending fallback response.')
                fallback = root_node.select_target(request)[0].fallback
                request.conn.sendall(fallback(request).generate())


class Server:
    """The HTTP server class"""

    def __init__(self, server_addr: tuple[str, int] = ('', 80), *, max_listen: int = 0, timeout: int = None,
                 ssl_cert: str = None, max_worker: int = 4, multi_status: str = 'thread', reuse_port: bool = None,
                 reuse_addr: bool = True, dualstack: bool = None, keep_alive_timeout: int = 75,
                 sock: socket.socket = None, **kwargs):
        """
        :param server_addr: the address of server (host, port)
        :param max_listen: max size of listener queue (0 for default value)
        :param timeout: timeout for server socket
        :param ssl_cert: SSL certificate content
        :param max_worker: max size of worker queue
        :param multi_status: specify if this server use single processing or mulit-thread processing
        :param keep_alive_timeout: timeout for Keep-Alive in HTTP/1.1. Set it to 0 to disable it.
        :param reuse_port: whether server socket reuse port (set SO_REUSEPORT to 1)
        :param reuse_addr: whether server socket reuse address (set SO_REUSEADDR to 1)
        :param dualstack: whether server use IPv6 dualstack if possible
        :param sock: a given socket
        """
        self.is_running = False
        self.listener: Optional[threading.Thread] = None
        self.receiver: Optional[threading.Thread] = None
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

        self.connection_pool = ConnectionPool(timeout = keep_alive_timeout)
        self.worker_pool: set[WorkerType] = set()
        self._is_child = self._check_process()
        self.root_node = Node(desc = 'root_node', **kwargs)
        self.bind = self.root_node.bind  # create an alias
        if self._is_child:
            return  # not to initialize socket

        if sock:
            self._sock = sock
        else:
            if dualstack is None:
                dualstack = self._get_socket_family() == socket.AF_INET6 and socket.has_dualstack_ipv6()
            if reuse_port is None:
                reuse_port = hasattr(socket, 'SO_REUSEPORT')
            self._sock = socket.create_server(server_addr, family = self._get_socket_family(), backlog = max_listen,
                                              reuse_port = reuse_port, dualstack_ipv6 = dualstack)
            self._sock.settimeout(timeout)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, int(reuse_addr))

        if ssl_cert:
            ssl_context = SSLContext()
            ssl_context.load_cert_chain(ssl_cert)
            self._sock = ssl_context.wrap_socket(self._sock, server_side = True)

    def _get_socket_family(self):
        if self.addr[0] == '':
            return socket.AF_INET6 if socket.has_ipv6 else socket.AF_INET
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
        worker = self.worker_type(target = run_worker, name = name, daemon = True,
                                  kwargs = {'name': name, 'root_node': self.root_node, 'req_queue': self.queue,
                                            'conn_pool': self.connection_pool})
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
        for p in self.worker_pool:
            p.start()

        logging.info(f'Listening request on {self.addr}')
        self.listener = threading.Thread(target = self.accept_conn, daemon = True)
        self.listener.start()
        self.receiver = threading.Thread(target = self.recv_request, daemon = True)
        self.receiver.start()
        print(f'Server running on {self._sock.getsockname()}. Press Ctrl+C to quit.')

        if block:
            while self.listener.is_alive():
                try:
                    time.sleep(1)
                except KeyboardInterrupt:
                    self.terminate()
                    return

    def accept_conn(self):
        """Accept TCP requests from listening ports"""
        while self.is_running:
            try:
                connection, address = self._sock.accept()
                logging.debug(f'<-{utility.format_socket(connection)}')
            except socket.timeout:
                continue
            except OSError:
                return  # Server has shut down
            self.connection_pool.add(connection, address)
        logging.info('Request listening stopped.')  # This should not appear in the terminal

    def recv_request(self):
        while self.is_running:
            try:
                conn, addr, readed = self.connection_pool.get()
            except (ConnectionResetError, ConnectionAbortedError):
                continue
            self.handle_request(conn, addr, readed)

    def handle_request(self, connection, address, readed: bytes = b''):
        """
        Construct an HTTP request object and put it into the request queue\n
        :param readed: bytes that already readed from connection
        :param connection: a socket object which is connected to HTTP Client
        :param address: address of client socket
        """
        try:
            content = readed + utility.recv_request_head(connection)
            request = Request(addr = address, **utility.parse_req(content), conn = connection)
            self.queue.put(request)
            logging.debug(f'{request} <- {utility.format_socket(request.conn)}')
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
        if self.receiver.is_alive():
            logging.info('Terminating request receiver...')
            _terminate(self.receiver)
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
