"""Real loopback contracts for the non-retrying keyboard tap client."""
import contextlib
import http.server
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import input as keyboard_input


def completed(**changes):
    return {"schema": 1, "request_id": 7, "state": "completed",
            "down_dispatched": True, "up_dispatched": True, **changes}


@contextlib.contextmanager
def server(*, capability=None, result=None, status=200, mime="application/json",
           post_delay=0, ping_delay=0, trickle=False, length=None, disconnect=False,
           slow_header=False, host="127.0.0.1"):
    requests = []
    ping = {"ok": True, "capabilities": {"input": [1]}} if capability is None else capability
    payload = completed() if result is None else result

    class Handler(http.server.BaseHTTPRequestHandler):
        def respond(self, body, response_status, delay=0):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            try:
                if delay:
                    time.sleep(delay)
                self.send_response(response_status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data) if length is None else length))
                if slow_header:
                    self.flush_headers()
                    for byte in b"X-Slow: abcdefghijklmnopqrstuvwxyz\r\n":
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(.02)
                self.end_headers()
                for start in range(0, len(data), 1 if trickle else len(data) or 1):
                    self.wfile.write(data[start:start + (1 if trickle else len(data))])
                    self.wfile.flush()
                    if trickle:
                        time.sleep(.02)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            requests.append(("GET", self.path, None))
            self.respond(ping, 200, ping_delay)

        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(("POST", self.path, json.loads(data)))
            if disconnect:
                self.close_connection = True
                return
            self.respond(payload, status, post_delay)

        def log_message(self, *_args):
            pass

    class Server(http.server.ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        daemon_threads = True

    httpd = Server((host, 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        authority = f"[{host}]" if ":" in host else host
        yield f"http://{authority}:{httpd.server_port}", requests
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


class InputTests(unittest.TestCase):
    def test_completed_sequence_and_effect_semantics(self):
        with server() as (url, requests):
            result = keyboard_input.tap(url, 18)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["effect"], "unknown")
        self.assertEqual(result["request_id"], 7)
        self.assertEqual(requests, [("GET", "/ping", None), ("POST", "/input",
                         {"schema": 1, "scan_code": 18, "action": "tap"})])

    def test_unsupported_capability_never_posts(self):
        for capability in ({"ok": True}, {"ok": True, "capabilities": {"input": [True]}},
                           {"ok": True, "capabilities": {"input": [2]}},
                           {"ok": True, "capabilities": {"input": "1"}}):
            with self.subTest(capability=capability), server(capability=capability) as (url, requests):
                result = keyboard_input.tap(url, 18)
                self.assertEqual(result["state"], "unsupported", result)
                self.assertEqual(len(requests), 1)

    def test_invalid_request_values_do_not_connect(self):
        with patch.object(keyboard_input.http.client.HTTPConnection, "connect") as connect:
            for scan in (0, 255, 256, -1, True, 18.0, "18", None):
                with self.subTest(scan=scan):
                    self.assertEqual(keyboard_input.tap("http://127.0.0.1:1", scan)["state"], "rejected")
            for timeout in (0, -1, True, float("nan"), float("inf"), "1", None):
                with self.subTest(timeout=timeout):
                    self.assertEqual(keyboard_input.tap("http://127.0.0.1:1", 18, timeout=timeout)["state"], "rejected")
            connect.assert_not_called()

    def test_remote_or_invalid_origins_do_not_connect(self):
        with patch.object(keyboard_input.http.client.HTTPConnection, "connect") as connect:
            for url in ("http://example.com", "http://192.0.2.1", "http://0.0.0.0",
                        "http://[::]", "http://127.0.0.1/path", "https://127.0.0.1",
                        "http://user@127.0.0.1", "http://127.0.0.1?x=1"):
                with self.subTest(url=url):
                    self.assertEqual(keyboard_input.tap(url, 18)["state"], "rejected")
            connect.assert_not_called()

    def test_statuses_and_noncompletion_results(self):
        cases = [(409, "rejected", False, False), (503, "cancelled", False, False),
                 (503, "cancelled", True, True), (503, "unknown", True, False),
                 (503, "unknown", True, True)]
        for status, state, down, up in cases:
            body = completed(state=state, down_dispatched=down, up_dispatched=up)
            with self.subTest(body=body), server(result=body, status=status) as (url, requests):
                result = keyboard_input.tap(url, 18)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["state"], state)
                self.assertEqual(result["request_id"], 7)
                self.assertEqual(len(requests), 2)

    def test_invalid_response_schema_is_unknown_without_retry(self):
        cases = [completed(schema=True), completed(schema=2), completed(request_id=True),
                 completed(request_id=0), completed(request_id=2**64), completed(state="accepted"),
                 completed(down_dispatched=1), completed(up_dispatched=False),
                 completed(down_dispatched=False), completed(state="rejected"),
                 completed(state="cancelled", up_dispatched=False), completed(error=4),
                 [], b"{not json"]
        for body in cases:
            with self.subTest(body=body), server(result=body) as (url, requests):
                result = keyboard_input.tap(url, 18)
                self.assertEqual(result["state"], "unknown", result)
                self.assertIsNone(result["request_id"])
                self.assertEqual(sum(method == "POST" for method, *_ in requests), 1)

    def test_inconsistent_http_status_is_unknown(self):
        for status, body in ((503, completed()), (200, completed(state="rejected", down_dispatched=False, up_dispatched=False)),
                             (500, completed())):
            with self.subTest(status=status), server(status=status, result=body) as (url, _):
                self.assertEqual(keyboard_input.tap(url, 18)["state"], "unknown")

    def test_lost_response_never_retries(self):
        with server(disconnect=True) as (url, requests):
            result = keyboard_input.tap(url, 18)
        self.assertEqual(result["state"], "unknown", result)
        self.assertEqual(sum(method == "POST" for method, *_ in requests), 1)

    def test_post_deadline_is_unknown_and_bounded(self):
        with server(post_delay=.3) as (url, requests):
            started = time.monotonic()
            result = keyboard_input.tap(url, 18, timeout=.1)
            elapsed = time.monotonic() - started
        self.assertEqual(result["state"], "unknown", result)
        self.assertIn("deadline", result["error"])
        self.assertLess(elapsed, .5)
        self.assertEqual(sum(method == "POST" for method, *_ in requests), 1)

    def test_total_deadline_includes_capability_check(self):
        with server(ping_delay=.06, post_delay=.08) as (url, requests):
            started = time.monotonic()
            result = keyboard_input.tap(url, 18, timeout=.1)
            elapsed = time.monotonic() - started
        self.assertEqual(result["state"], "unknown", result)
        self.assertIn("deadline", result["error"])
        self.assertLess(elapsed, .15)
        self.assertEqual(len(requests), 2)

    def test_trickle_body_and_header_cannot_extend_deadline(self):
        for options in ({"trickle": True}, {"slow_header": True}):
            with self.subTest(options=options), server(**options) as (url, requests):
                started = time.monotonic()
                result = keyboard_input.tap(url, 18, timeout=.1)
                self.assertLess(time.monotonic() - started, .5)
                self.assertIn("deadline", result["error"])
                self.assertEqual(result["state"], "rejected")
                self.assertEqual(len(requests), 1)

    def test_transport_contract_rejections(self):
        for options in ({"mime": "text/plain"}, {"length": keyboard_input.MAX_JSON_BYTES + 1},
                        {"length": -1}, {"length": 999}):
            with self.subTest(options=options), server(**options) as (url, requests):
                result = keyboard_input.tap(url, 18)
                self.assertEqual(result["state"], "rejected", result)
                self.assertEqual(len(requests), 1)

    def test_localhost_resolves_only_numeric_loopback(self):
        original = socket.getaddrinfo
        hosts = []
        def numeric_only(host, *args, **kwargs):
            hosts.append(host)
            self.assertEqual(host, "127.0.0.1")
            return original(host, *args, **kwargs)
        with server() as (url, _), patch("socket.getaddrinfo", side_effect=numeric_only):
            result = keyboard_input.tap(url.replace("127.0.0.1", "localhost"), 18)
        self.assertTrue(result["ok"], result)
        self.assertEqual(hosts, ["127.0.0.1", "127.0.0.1"])

    def test_ipv6_loopback(self):
        try:
            with server(host="::1") as (url, _):
                result = keyboard_input.tap(url, 18)
        except OSError as exc:
            self.skipTest(f"IPv6 loopback unavailable: {exc}")
        self.assertTrue(result["ok"], result)

    def test_cli_outputs_dispatch_semantics(self):
        with server() as (url, requests):
            result = subprocess.run([sys.executable, "-B", keyboard_input.__file__, "--scan-code", "18",
                                     "--base-url", url], capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["effect"], "unknown")
        self.assertEqual(len(requests), 2)


if __name__ == "__main__":
    unittest.main()
