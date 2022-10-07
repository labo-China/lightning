import abc
import logging
import socket
import threading
import select
import traceback
import time

from concurrent import futures
from . import utility
from .structs import Node, Request, Response


class ConnectionPool:
    def __init__(self, timeout: int = 75, max_conn: int = float('inf'),
                 clean_threshold: int = None):
        self.timeout = timeout
        self.max_conn = max_conn
        if clean_threshold is None:
            self.clean_threshold = int((self.max_conn if self.max_conn != float('inf') else 100) * 0.8)

        self.table: dict[socket.socket, float] = {}
        self.active_conn = set()

    def add(self, conn: socket.socket, forever: bool = False):
        if len(self.table) > self.clean_threshold:
            logging.debug(f'connections count ({len(self.table)}) has reached threshold. performing cleaning')
            self.clean()
        self.active_conn.add(conn)
        self.table[conn] = time.time() + self.timeout if not forever else None
        logging.debug(f'{utility.format_socket(conn)} has been added to connection pool')

    def remove(self, conn: socket.socket):
        if conn in self.active_conn:
            self.active_conn.remove(conn)
        self.table.pop(conn)
        if not getattr(conn, '_closed'):
            logging.debug(f'{utility.format_socket(conn)} is removed from connection pool')

    def is_expired(self, conn: socket.socket, timeout: int = None):
        timeout = timeout or self.timeout
        if self.table[conn] is None:  # permanent socket never expires
            return False
        elif getattr(conn, '_closed') or conn not in self:  # closed socket is treated as expired
            return True
        elif (self.table[conn] - self.timeout + timeout) < time.time():  # normally expired
            return True
        return False

    def clean(self):
        logging.debug(f'performing cleaning in connection pool: {set(utility.format_socket(c) for c in self.table)}')
        for conn in self.table:
            if self.is_expired(conn):
                self.remove(conn)

    def get(self) -> socket.socket:
        while True:
            if not self.active_conn:
                time.sleep(0.05)
                continue
            for rl in select.select(self.active_conn, [], [], 5):
                for r in rl:
                    if self.table[r] is not None:
                        self.active_conn.remove(r)
                    if self.is_expired(r):
                        self.table.pop(r)
                    return r

    def close(self):
        """Remove and close all connections in the pool"""
        self.active_conn.clear()
        self.table.clear()

    def __contains__(self, item):
        return item in self.table


class BaseBackend(abc.ABC):
    def __init__(self, sock: socket.socket, root_node: Node, conn_pool: ConnectionPool, *args, **kwargs):
        self.sock = sock
        self.root_node = root_node
        self.conn_pool = conn_pool
        self.conn_pool.add(sock, forever = True)
        self.is_running = False

    def run(self, *args, **kwargs):
        self.is_running = True
        try:
            self.start(*args, **kwargs)
        except (OSError, KeyboardInterrupt):
            self.terminate()

    @abc.abstractmethod
    def start(self, *args, **kwargs):
        raise NotImplemented

    @abc.abstractmethod
    def interrupt(self):
        """Interrupt the backend and stop receiving incoming requests.\n
        The backend can start again."""
        raise NotImplemented

    def terminate(self):
        """Terminate the backend and close the main connection."""
        self.interrupt()
        self.conn_pool.close()
        self.sock.close()

    def get_active_conn(self):
        while True:
            sock = self.conn_pool.get()
            if sock is None:
                return  # because pool is closed
            elif sock is self.sock:
                new_conn, _ = sock.accept()
                self.conn_pool.add(new_conn)
                continue

            buf = sock.recv(4)
            if buf == b'':
                self.conn_pool.remove(sock)  # remove inactive connections
            else:
                return sock, buf

    def _process_request(self, request: Request):
        """Process a request"""
        resp = self.root_node.process(request)

        if getattr(request.conn, '_closed'):
            if resp is not None:
                logging.warning(f'Connection from {request.addr} was closed before sending response')
            return

        rest = utility.recv_all(request.conn)  # receive all unused data to make keep-alive working
        if rest:
            logging.warning(f'Unused data found in {utility.format_socket(request.conn)}: {rest}')

        if resp is None:  # assume that response is already sent
            logging.info(f'{root_node} -> ... -> {utility.format_socket(request.conn)}')
            self.conn_pool.add(request.conn)
            return

        close_conn = False
        if resp.header.get('Connection') == 'close' or request.header.get('Connection') == 'close':
            close_conn = True
        else:
            if not self.conn_pool.is_expired(request.conn):
                resp.header['Connection'] = 'keep-alive'
                resp.header['Keep-Alive'] = f'timeout={self.conn_pool.timeout}'
            else:
                close_conn = True
                resp.header['Connection'] = 'close'

        resp_data = resp.generate()
        request.conn.sendall(resp_data)
        logging.info(f'{self.root_node} -> {resp} -> {utility.format_socket(request.conn)}')

        if close_conn:
            request.conn.close()
        else:
            self.conn_pool.add(request.conn)

    def process_request(self, request: Request):
        """Process a request and hold exceptions with fallback"""
        try:
            self._process_request(request)
        except Exception:
            logging.warning('It seems that the process has not completed yet. Sending fallback response.',
                            exc_info = True)
            fallback = self.root_node.select_target(request)[0].fallback
            request.conn.sendall(fallback(request).generate())

    @staticmethod
    def build_request(conn: socket.socket, readed: bytes = None):
        content = readed + utility.recv_request_head(conn)
        return Request(**utility.parse_req(content), conn = conn, addr = conn.getpeername())

    def __del__(self):
        self.terminate()


class SimpleBackend(BaseBackend):
    """A simple backend class. It includes a minimal single-threaded implementation"""

    def start(self):
        while self.is_running:
            request = self.build_request(*self.get_active_conn())
            self.process_request(request)

    def interrupt(self):
        self.is_running = False


class BasePoolBackend(BaseBackend):
    """A base backend class using Pool to handle requests"""

    def __init__(self, executor: futures.Executor, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executor = executor

    def start(self, *args, **kwargs):
        while self.is_running:
            result = self.get_active_conn()
            if result is None:
                break  # because the pool is closed
            request = self.build_request(*result)
            self.executor.submit(self.process_request, request)

    def interrupt(self):
        self.is_running = False

    def terminate(self, wait: bool = False):
        super().terminate()
        self.executor.shutdown(wait)


class ThreadPoolBackend(BasePoolBackend):
    def __init__(self, max_worker: int = None, *args, **kwargs):
        super().__init__(futures.ThreadPoolExecutor(max_workers = max_worker, thread_name_prefix = 'Worker'),
                         *args, **kwargs)


class ProcessPoolBackend(BasePoolBackend):
    def __init__(self, max_worker: int = None, *args, **kwargs):
        super().__init__(futures.ProcessPoolExecutor(max_workers = max_worker), *args, **kwargs)


__all__ = ['ConnectionPool', 'BaseBackend']
