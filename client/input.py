"""Single keyboard tap transport. Completion describes dispatch, not game effects."""
from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import math
import socket
import sys
import threading
import time
import urllib.parse

MAX_JSON_BYTES = 65536


def _origin(base_url: str) -> tuple[str, int | None]:
    url = urllib.parse.urlsplit(base_url)
    if (url.scheme != "http" or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("base URL must be an HTTP origin")
    host = "127.0.0.1" if url.hostname.lower() == "localhost" else url.hostname
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("input host must be numeric loopback or localhost") from None
    if not address.is_loopback:
        raise ValueError("input host must be loopback")
    return str(address), url.port


def _exchange(origin: tuple[str, int | None], method: str, path: str,
              body: bytes | None, deadline: float) -> tuple[int, dict]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("input total deadline exceeded")
    conn = http.client.HTTPConnection(*origin, timeout=remaining)
    timer = None
    expired = threading.Event()
    try:
        conn.connect()
        transport = conn.sock

        def cancel():
            expired.set()
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("input total deadline exceeded")
        timer = threading.Timer(remaining, cancel)
        timer.daemon = True
        timer.start()
        conn.request(method, path, body=body, headers={
            "Accept": "application/json", "Content-Type": "application/json"})
        with conn.getresponse() as response:
            lengths = response.headers.get_all("Content-Length", [])
            if (len(lengths) != 1 or not lengths[0].isascii()
                    or not lengths[0].isdigit() or len(lengths[0]) > 10):
                raise ValueError("missing or invalid Content-Length")
            length = int(lengths[0])
            if length > MAX_JSON_BYTES:
                raise ValueError("input JSON response exceeds size cap")
            if response.getheader("Transfer-Encoding") is not None:
                raise ValueError("unexpected Transfer-Encoding")
            mime = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if mime != "application/json":
                raise ValueError("expected application/json response")
            data = bytearray()
            while len(data) <= MAX_JSON_BYTES:
                chunk = response.read1(min(65536, MAX_JSON_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if expired.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError("input total deadline exceeded")
            if expired.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("input total deadline exceeded")
            if len(data) != length:
                raise ValueError("response Content-Length mismatch")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("input response must be a JSON object")
            return response.status, result
    except (OSError, http.client.HTTPException) as exc:
        if expired.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("input total deadline exceeded") from exc
        raise
    finally:
        if timer is not None:
            timer.cancel()
        conn.close()


def _validate_result(result: dict) -> dict:
    if type(result.get("schema")) is not int or result["schema"] != 1:
        raise ValueError("unsupported input result schema")
    request_id = result.get("request_id")
    if type(request_id) is not int or not 1 <= request_id <= 2**64 - 1:
        raise ValueError("invalid input request_id")
    state = result.get("state")
    if state not in ("completed", "cancelled", "unknown", "rejected"):
        raise ValueError("invalid input result state")
    down, up = result.get("down_dispatched"), result.get("up_dispatched")
    if type(down) is not bool or type(up) is not bool or (up and not down):
        raise ValueError("invalid input dispatch flags")
    if state == "completed" and not (down and up):
        raise ValueError("completed input must have both dispatch flags")
    if state == "rejected" and (down or up):
        raise ValueError("rejected input cannot have dispatched events")
    if state == "cancelled" and down and not up:
        raise ValueError("cancelled input cannot retain an unreleased down")
    if "error" in result and not isinstance(result["error"], str):
        raise ValueError("input error must be a string")
    return {**result, "ok": state == "completed", "effect": "unknown"}


def tap(base_url: str, scan_code: int, *, timeout: float = 3.0) -> dict:
    """Negotiate v1 and submit exactly once, with one total connection/read deadline.

    A completed response only confirms the native down/up dispatch sequence.
    A lost response may follow a delivered key: callers must not retry it.
    """
    posted = False
    try:
        if type(scan_code) is not int or not 1 <= scan_code <= 254:
            raise ValueError("scan_code must be an integer from 1 through 254")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        origin = _origin(base_url)
        deadline = time.monotonic() + timeout
        status, ping = _exchange(origin, "GET", "/ping", None, deadline)
        if status != 200 or ping.get("ok") is not True:
            raise ValueError("input capability check failed")
        capabilities = ping.get("capabilities")
        versions = capabilities.get("input") if isinstance(capabilities, dict) else None
        if not isinstance(versions, list) or not any(type(v) is int and v == 1 for v in versions):
            return {"ok": False, "state": "unsupported", "request_id": None,
                    "effect": "unknown", "error": "input v1 is unsupported"}
        body = json.dumps({"schema": 1, "scan_code": scan_code, "action": "tap"},
                          separators=(",", ":")).encode("utf-8")
        # Once transmission starts the result is uncertain, even if the socket
        # fails before the client can prove whether the server read the request.
        posted = True
        status, result = _exchange(origin, "POST", "/input", body, deadline)
        if status not in (200, 400, 409, 503):
            raise ValueError(f"unexpected input HTTP status {status}")
        result = _validate_result(result)
        if (status == 200) != (result["state"] == "completed"):
            raise ValueError("input HTTP status disagrees with result state")
        return result
    except (OSError, ValueError, TypeError, OverflowError, http.client.HTTPException) as exc:
        return {"ok": False, "state": "unknown" if posted else "rejected",
                "request_id": None, "effect": "unknown", "error": str(exc)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dispatch one Skyrim keyboard tap (no retry)")
    parser.add_argument("--scan-code", type=int, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:5099")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args(argv)
    result = tap(args.base_url, args.scan_code, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
