"""Real loopback screenshot contracts, including slow trickle deadlines."""
import contextlib
import http.server
from pathlib import Path
import struct
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge
import screenshot


def bmp():
    return (struct.pack('<2sIHHI', b'BM', 70, 0, 0, 54)
            + struct.pack('<IiiHHIIiiII', 40, 2, -2, 1, 32, 0, 16, 0, 0, 0, 0)
            + bytes(range(16)))


@contextlib.contextmanager
def server(body=None, status=200, mime='image/bmp', length=None, metadata=None,
           delay=0, slow_header=False, host='127.0.0.1'):
    payload = bmp() if body is None else body
    requests = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.rfile.read(int(self.headers['Content-Length']))))
            try:
                self.send_response(status)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(payload) if length is None else length))
                for key, value in ({'Request': '1', 'Frame': '42', 'Width': '2', 'Height': '2'}
                                   if metadata is None else metadata).items():
                    self.send_header('X-Screenshot-' + key, value)
                if slow_header:
                    self.flush_headers()
                    for byte in b'X-Slow: abcdefghijklmnopqrstuvwxyz\r\n':
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(.03)
                self.end_headers()
                for start in range(0, len(payload), 1 if delay else 65536):
                    self.wfile.write(payload[start:start + (1 if delay else 65536)])
                    self.wfile.flush()
                    if delay:
                        time.sleep(delay)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *_args):
            pass
    class Server(http.server.ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ':' in host else socket.AF_INET
    httpd = Server((host, 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        authority = f'[{host}]' if ':' in host else host
        yield f'http://{authority}:{httpd.server_port}', requests
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


class ScreenshotTests(unittest.TestCase):
    def test_capture_owned_pixels_metadata_and_post(self):
        with server() as (url, requests), patch.object(bridge, 'BASE_URL', url):
            result = bridge.screenshot()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['bmp'], bmp())
        self.assertIsInstance(result['bmp'], bytes)
        self.assertEqual((result['width'], result['height'], result['frame'], result['request_id']), (2, 2, 42, 1))
        self.assertEqual(requests, [('/screenshot', b'{}')])

    def test_http_json_error(self):
        with server(status=503, mime='application/json', body=b'{"ok":false,"error":"no frame"}') as (url, _):
            result = screenshot.capture(url)
        self.assertFalse(result['ok'])
        self.assertIn('503', result['error'])
        self.assertIn('no frame', result['error'])

    def test_transport_contract_failures(self):
        for options in [dict(length=screenshot.MAX_BYTES + 1), dict(length=100),
                        dict(mime='application/json'), dict(metadata={}),
                        dict(metadata={'Request':'1','Frame':'2','Width':'3','Height':'2'}),
                        dict(length='-1'), dict(body=b'bad BMP')]:
            with self.subTest(options=options), server(**options) as (url, _):
                self.assertFalse(screenshot.capture(url)['ok'])

    def test_body_and_header_trickle_total_deadline(self):
        for options in (dict(delay=.03), dict(slow_header=True)):
            with self.subTest(options=options), server(**options) as (url, _):
                started = time.monotonic()
                result = screenshot.capture(url, timeout=.15)
                self.assertFalse(result['ok'], result)
                self.assertIn('deadline', result['error'])
                self.assertLess(time.monotonic() - started, .7)

    def test_invalid_bmp_layouts(self):
        for offset, form, value in ((0,'2s',b'ZZ'), (2,'I',55), (10,'I',55), (14,'I',108),
                                   (18,'i',3841), (22,'i',2), (22,'i',-2161), (26,'H',2),
                                   (28,'H',24), (30,'I',3), (34,'I',0)):
            data = bytearray(bmp())
            struct.pack_into('<' + form, data, offset, value)
            with self.subTest(offset=offset,value=value), server(body=data) as (url, _):
                self.assertFalse(screenshot.capture(url)['ok'])

    def test_ipv6_numeric_loopback(self):
        try:
            with server(host='::1') as (url, _):
                result = screenshot.capture(url)
        except OSError as exc:
            self.skipTest(f'IPv6 loopback unavailable: {exc}')
        self.assertTrue(result['ok'], result)

    def test_localhost_maps_to_loopback_without_dns_hostname(self):
        import socket
        original = socket.getaddrinfo
        hosts = []
        def numeric_only(host, *args, **kwargs):
            hosts.append(host)
            self.assertEqual(host, '127.0.0.1')
            return original(host, *args, **kwargs)
        with server() as (url, _), patch('socket.getaddrinfo', side_effect=numeric_only):
            result = screenshot.capture(url.replace('127.0.0.1', 'localhost'))
        self.assertTrue(result['ok'], result)
        self.assertEqual(hosts, ['127.0.0.1'])

    def test_rejects_non_loopback_hosts_before_connection(self):
        with patch.object(screenshot.http.client.HTTPConnection, 'connect') as connect:
            for host in ('example.com', '192.0.2.1', '0.0.0.0', '[::]', '[2001:db8::1]'):
                with self.subTest(host=host):
                    result = screenshot.capture('http://' + host + ':5099')
                    self.assertFalse(result['ok'], result)
                    self.assertIn('loopback', result['error'])
            connect.assert_not_called()

    def test_invalid_timeout(self):
        for timeout in (0, -1, float('inf'), float('nan'), True):
            self.assertFalse(screenshot.capture('http://127.0.0.1:1', timeout=timeout)['ok'])

    def test_cli_saves_exact_bmp_and_protects_existing_file(self):
        with tempfile.TemporaryDirectory() as directory, server() as (url, _):
            output = Path(directory) / 'capture.bmp'
            command = [sys.executable, '-B', screenshot.__file__, str(output), '--base-url', url]
            result = subprocess.run(command, capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_bytes(), bmp())
            result = subprocess.run(command, capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(output.read_bytes(), bmp())

    def test_cli_error_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as directory, server(status=503) as (url, _):
            output = Path(directory) / 'capture.bmp'
            with patch('sys.stderr'):
                self.assertEqual(screenshot.main([str(output), '--base-url', url]), 1)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
