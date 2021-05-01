import re

import Structs
import Utility
from Structs import Request, Response, Handler, Interface
import Interfaces
from typing import Tuple, List, Union
import socket
from urllib.parse import unquote
import threading
import logging
import traceback
from re import Pattern, compile


class Server:
    def __init__(self, server_addr: Tuple[str, int], recv_buffer_size: int = 1024, listen_limit: int = 10,
                 timeout: int = 60, default: Handler = Interfaces.DefaultInterface,
                 conn_famliy: socket.AddressFamily = socket.AF_INET):
        self.is_running = False
        self.default = default
        self.recv_buffer_size = recv_buffer_size
        self._map: List[Tuple[Pattern, Interface]] = []
        self._request_pool = []
        self.timeout = timeout
        self.addr = server_addr

        self._sock = socket.socket(conn_famliy)
        self._sock.settimeout(timeout)
        self._sock.bind(server_addr)
        self._sock.listen(listen_limit)

    @staticmethod
    def parser(raw_content: bytes, address: tuple) -> Request:
        import Utility
        request = Request(addr = address, **Utility.parse_request(raw_content))
        return request

    def bind(self, pattern: Union[Pattern, str], interface: Structs.Interface):
        pattern = pattern if isinstance(pattern, Pattern) else re.compile(f'^{pattern}$')
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
        interface = [i for i in self._map if i[0].match(request_line['url'])]
        interface = interface[0] if len(interface) else self.default
        if interface == Structs.RAWRequest:
            request = RawRequest(addr = address, conn = connection, **request_line)
        else:
            recv_content = Utility.recv_all(connection)
            content = Utility.parse_content(recv_content)
            request = Request(**request_line, **content)
        with interface[1] as i:
            ret = i.process(request)
            if ret:
                connection.sendall(ret if type(ret) is bytes else bytes(repr(ret), 'utf-8'))
            connection.close()
        print(f'Request {address} processed.')

    def _handle_request_(self, connection, address):
        recv_content = b''
        content_length = self.recv_buffer_size
        while content_length == self.recv_buffer_size:
            c = connection.recv(self.recv_buffer_size)
            recv_content += c
            content_length = len(c)

        request = self.parser(raw_content = recv_content, address = address)
        hited_interface = self._urlmap.get(request.url) or self.default
        try:
            if type(hited_interface) == BaseInterface:
                hited_interface.execute(connection, address)
            else:
                if type(hited_interface) == BytesInterface:
                    ret_content = hited_interface.execute(request)
                else:
                    ret_content = hited_interface.execute(request)
                connection.sendall(ret_content)
        except Exception:
            traceback.print_exc()
            connection.sendall(b'HTTP/1.1 500 Internal Server Error/r/n/r/n')
        finally:
            connection.close()
        print(f'Request {address} processed.')

    def interrupt(self):
        if self.is_running:
            self.is_running = False
            for t in self._request_pool:
                print(f'Waiting for request {t.name}')
                t.join(self.timeout)
        print('Server closed successfully.')
        self._sock.close()
