import abc
import logging
import socket
import threading
import time

from . import utility
from .structs import Node, Request, Response


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


class BaseBackend(abc.ABC):
    def __init__(self, sock: socket.socket, root_node: Node, conn_pool: ConnectionPool):
        self.sock = sock
        self.root_node = root_node
        self.conn_pool = conn_pool
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
        raise NotImplemented

    @abc.abstractmethod
    def terminate(self):
        raise NotImplemented

    def process_request(self, req: Request):
        resp = self.root_node.process(req)

        if getattr(req.conn, '_closed'):
            if resp is not None:
                logging.warning(f'Connection from {req.addr} was closed before sending response')
            return

        rest = utility.recv_all(req.conn)  # receive all unused data to make keep-alive working
        if rest:
            logging.warning(f'Unused data found in {utility.format_socket(req.conn)}: {rest}')

        if resp is None:  # assume that response is already sent
            logging.info(f'{root_node} -> ... -> {utility.format_socket(req.conn)}')
            self.conn_pool.add(req.conn, req.addr)
            return

        close_conn = False
        if resp.header.get('Connection') == 'close' or req.header.get('Connection') == 'close':
            close_conn = True
        else:
            if not self.conn_pool.is_expired(req.conn):
                resp.header['Connection'] = 'keep-alive'
                resp.header['Keep-Alive'] = f'timeout={self.conn_pool.timeout}'
            else:
                close_conn = True
                resp.header['Connection'] = 'close'

        resp_data = resp.generate()
        req.conn.sendall(resp_data)
        logging.info(f'{self.root_node} -> {resp} -> {utility.format_socket(req.conn)}')

        if close_conn:
            req.conn.close()
        else:
            self.conn_pool.add(req.conn, req.addr)

    @staticmethod
    def build_request(addr: tuple, conn: socket.socket, readed: bytes = None):
        content = readed + utility.recv_request_head(conn)
        return Request(addr = addr, **utility.parse_req(content), conn = conn)


class SimpleBackend(BaseBackend):
    """A simple backend class. It includes a minimal single-threaded implementation"""

    def start(self):
        while self.is_running:
            try:
                conn, addr = self.sock.accept()
                self.conn_pool.add(conn, addr)
            except ConnectionResetError:
                continue
            except socket.timeout:
                continue
            request = self.build_request(addr, conn)


__all__ = ['ConnectionPool', 'BaseBackend']
