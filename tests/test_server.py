import socket
import unittest
import requests
import logging

from src import lightning


class Template(unittest.TestCase):
    server: lightning.Server = None

    def setUp(self) -> None:
        self.server = lightning.Server(('', 0))
        self.server.run(block = False, quiet = True)

    def tearDown(self) -> None:
        self.server.terminate()

    def get_addr(self):
        return f'localhost:{self.server.addr[1]}'

    def perform_test(self, path: str = None, content: bytes = b'test', code: int = 200):
        path = path or '/test'
        if '://' not in path:
            path = f'http://{self.get_addr()}{path}'
        resp = requests.get(path, proxies = {'http': None, 'https': None})  # Not to use proxies

        self.assertEqual(resp.status_code, code)
        self.assertEqual(resp.headers['Content-Type'], 'text/plain;charset=utf-8')
        self.assertEqual(resp.headers['Content-Length'], str(len(content)))
        self.assertEqual(resp.headers['Server'], 'Lightning')

    @staticmethod
    def interface(_):
        return lightning.Response(200, content = b'test')


class TestServer(Template):
    def test_alive(self):
        self.perform_test('', content = b'', code = 404)

    @unittest.skipUnless(socket.has_dualstack_ipv6() and socket.has_ipv6, 'Platform does not support IPV6 dualstack')
    def test_dualstack(self):
        self.perform_test(f'http://127.0.0.1:{self.server.addr[1]}/', code = 404, content = b'')
        self.perform_test(f'http://[::1]:{self.server.addr[1]}/', code = 404, content = b'')


class TestInterface(Template):
    def test_binary(self):
        self.server.bind('/test', lightning.Response(content = b'\x00'))
        self.perform_test(content = b'\x00')

    def test_raw(self):
        @self.server.bind('/test')
        def send(req: lightning.Request):
            req.conn.sendall(lightning.Response(200, content = b'test').generate())

        self.perform_test()

    def test_method_limit(self):
        self.server.bind('/test', ['GET'])(self.interface)
        self.perform_test()

        for method in ['POST', 'PUT', 'DELETE', 'PATCH', 'CONNECT']:
            if method != 'GET':
                resp = requests.request(method, f'http://{self.get_addr()}/test')
                self.assertEqual(resp.status_code, 405)

        with self.assertLogs(level = 'WARNING') as log:
            resp = requests.request('BAD', f'http://{self.get_addr()}/test')
        self.assertIn(log.output[0], 'WARNING:root:Request method BAD is invaild. Sending 400-Response instead.')
        self.assertEqual(resp.status_code, 400)

    def test_strict(self):
        self.server.bind('/test', strict = True)(self.interface)
        self.perform_test()
        self.perform_test('/test?query=test')
        for path in ('/test/', '/test/foo', '/test//foo', '/test/foo/bar'):
            with self.assertLogs(level = 'WARNING') as log:
                self.perform_test(path, code = 404, content = b'')
            for content in log.output:
                self.assertIn('is out of root directory.', content)

    def test_fallback(self):
        @self.server.bind('/test')
        @self.server.bind('/test_fallback', fallback = self.interface)
        def crasher(_):
            return str(1 / 0)  # crash here

        with self.assertLogs(level = 'WARNING') as log:
            self.perform_test(code = 500, content = b'')
            self.perform_test('/test_fallback')
        for msg in log.output:
            self.assertIn('WARNING:root:An Exception is detected. Sending fallback response.',
                          msg)
