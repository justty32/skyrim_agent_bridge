"""Bounded binary screenshot transport and save CLI (Python stdlib only)."""
from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import math
from pathlib import Path
import socket
import struct
import sys
import threading
import time
import urllib.parse

MAX_BYTES = 34 * 1024 * 1024
MAX_WIDTH = 3840
MAX_HEIGHT = 2160


def validate_bmp(data: bytes) -> tuple[int, int]:
    """Accept only the bridge's 54-byte, top-down 32-bit BI_RGB layout."""
    if len(data) < 54 or len(data) > MAX_BYTES:
        raise ValueError("BMP length outside bounds")
    magic, size, reserved1, reserved2, offset = struct.unpack_from("<2sIHHI", data)
    dib, width, height, planes, bits, compression, pixels = struct.unpack_from(
        "<IiiHHII", data, 14)
    if (magic != b"BM" or size != len(data) or reserved1 or reserved2
            or offset != 54 or dib != 40 or planes != 1 or bits != 32
            or compression != 0):
        raise ValueError("invalid BMP header/layout")
    if not (1 <= width <= MAX_WIDTH and -MAX_HEIGHT <= height <= -1):
        raise ValueError("invalid BMP dimensions (expected bounded top-down image)")
    expected = width * -height * 4
    if pixels != expected or len(data) != 54 + expected:
        raise ValueError("BMP pixel length mismatch")
    return width, -height


def capture(base_url: str, *, timeout: float = 5.0) -> dict:
    """Capture from numeric loopback (or localhost); timeout is total seconds.

    localhost is mapped to 127.0.0.1 without DNS so name resolution cannot
    escape the connection/read deadline. Remote hosts are rejected.
    """
    conn = None
    timer = None
    expired = threading.Event()
    try:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        url = urllib.parse.urlsplit(base_url)
        if (url.scheme != "http" or not url.hostname or url.username or url.password
                or url.path not in ("", "/") or url.query or url.fragment):
            raise ValueError("base URL must be an HTTP origin")
        host = "127.0.0.1" if url.hostname.lower() == "localhost" else url.hostname
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError("screenshot host must be numeric loopback or localhost") from None
        if not address.is_loopback:
            raise ValueError("screenshot host must be loopback")
        deadline = time.monotonic() + timeout
        conn = http.client.HTTPConnection(str(address), url.port, timeout=timeout)
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
            raise TimeoutError("screenshot total deadline exceeded")
        timer = threading.Timer(remaining, cancel)
        timer.daemon = True
        timer.start()
        conn.request("POST", "/screenshot", body=b"{}",
                     headers={"Content-Type": "application/json", "Accept": "image/bmp"})
        with conn.getresponse() as response:
            lengths = response.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                raise ValueError("missing or invalid Content-Length")
            length = int(lengths[0])
            if length > MAX_BYTES:
                raise ValueError("response exceeds screenshot size cap")
            if response.getheader("Transfer-Encoding") is not None:
                raise ValueError("unexpected Transfer-Encoding")
            data = bytearray()
            while len(data) <= MAX_BYTES:
                chunk = response.read1(min(65536, MAX_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if expired.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError("screenshot total deadline exceeded")
            if expired.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("screenshot total deadline exceeded")
            if len(data) != length:
                raise ValueError("response Content-Length mismatch (short or oversized body)")
            mime = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if response.status != 200:
                detail = ""
                if mime == "application/json":
                    try:
                        body = json.loads(data)
                        if isinstance(body, dict):
                            detail = str(body.get("error", ""))[:1024]
                    except (ValueError, UnicodeError):
                        pass
                return {"ok": False, "error": f"HTTP {response.status}: {detail}".rstrip(": ")}
            if mime != "image/bmp":
                raise ValueError("expected image/bmp response")
            width, height = validate_bmp(data)
            metadata = {}
            for key, header in (("request_id", "Request"), ("frame", "Frame"),
                                ("width", "Width"), ("height", "Height")):
                values = response.headers.get_all("X-Screenshot-" + header, [])
                if (len(values) != 1 or not values[0].isascii()
                        or not values[0].isdigit() or len(values[0]) > 20
                        or not 1 <= int(values[0]) <= 2**64 - 1):
                    raise ValueError("invalid X-Screenshot-" + header)
                metadata[key] = int(values[0])
            if (metadata["width"], metadata["height"]) != (width, height):
                raise ValueError("screenshot metadata dimensions disagree with BMP")
            return {"ok": True, "bmp": bytes(data), **metadata}
    except (OSError, ValueError, TypeError, OverflowError, http.client.HTTPException) as exc:
        message = "screenshot total deadline exceeded" if expired.is_set() else str(exc)
        return {"ok": False, "error": message}
    finally:
        if timer is not None:
            timer.cancel()
        if conn is not None:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture the next Skyrim frame as BMP")
    parser.add_argument("output", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:5099")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    result = capture(args.base_url, timeout=args.timeout)
    if not result["ok"]:
        print(result["error"], file=sys.stderr)
        return 1
    try:
        # Exclusive create protects an existing capture from accidental overwrite.
        with args.output.open("xb") as output:
            output.write(result["bmp"])
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f'{args.output}: {result["width"]}x{result["height"]}, frame {result["frame"]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
