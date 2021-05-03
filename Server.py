import Utility
from Structs import Request, Interface, RAWRequest, Processer, RAWResponse
import Interfaces
from typing import Tuple, List, Union
import socket
from urllib.parse import unquote
import threading
import traceback
from re import Pattern, compile


class Server:
    def __init__(self, server_addr: Tuple[str, int], recv_buffer_size: int = 1024, listen_limit: int = 10,
                 timeout: int = 60, default: Interface = Interfaces.DefaultInterface, thread_limit: int = 10,
                 conn_famliy: socket.AddressFamily = socket.AF_INET):
        self.is_running = False
        self.default = default
        self.recv_buffer_size = recv_buffer_size
        self._map: List[Tuple[Pattern, Interface]] = []
        self._request_pool = []
        self.thread_limit = thread_limit
        self.timeout = timeout
        self.addr = server_addr

        self._sock = socket.socket(conn_famliy)
        self._sock.settimeout(timeout)
        self._sock.bind(server_addr)
        self._sock.listen(listen_limit)

    def bind(self, pattern: Union[Pattern, str], interface: Interface):
        pattern = pattern if isinstance(pattern, Pattern) else compile(f'^{pattern}$')
        self._map.append((pattern, interface))

    def run(self, block: bool = True):
        self.is_running = True
        t = threading.Thread(target = self._accept_request)
        t.setDaemon(True)
        t.start()
        if block:
            t.join()
        else:
            return t

    def _accept_request(self):
        while self.is_running:
            try:
                connection, address = self._sock.accept()
            except socket.timeout:
                continue
            print(f'Received request from {address}')
            t = threading.Thread(target = self._handle_request, kwargs = {'connection': connection, 'address': address})
            self._request_pool.append(t)
            t.start()
        print('Request listening stopped.')

    def _handle_request(self, connection, address):
        request_line = Utility.parse_request_line(Utility.recv_request_line(connection))
        interface = [i[1] for i in self._map if i[0].match(request_line['url'])]
        interface = interface[0] if len(interface) else self.default
        if interface == RAWRequest:
            request = RawRequest(addr = address, conn = connection, **request_line)
        else:
            recv_content = Utility.recv_all(connection)
            content = Utility.parse_content(recv_content)
            request = Request(addr = address, **request_line, **content)
        with interface as i:
            i.process(request)
        if i.resp.generate():
            connection.sendall(i.resp.generate())
            connection.close()
            i.resp = b''
        print(f'Request {address} processed.')

    def interrupt(self):
        if self.is_running:
            self.is_running = False
            for t in self._request_pool:
                print(f'Waiting for request {t.name}')
                t.join(self.timeout)
        print('Server closed successfully.')
        self._sock.close()
