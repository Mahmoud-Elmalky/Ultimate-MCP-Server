"""Local loopback HTTP and WebSocket fixture servers for real Chromium security tests."""

from __future__ import annotations

import asyncio
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class RecordedHTTPServer:
    """Thread-safe local HTTP server recording requests and serving programmable responses."""

    handler_fn: Callable[[str, str, bytes], tuple[int, dict[str, str], bytes]]
    hits: list[tuple[str, str, bytes]] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None
    port: int = 0

    def start(self) -> int:
        parent = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return None

            def _handle(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or "0")
                body = self.rfile.read(length) if length > 0 else b""
                parent.hits.append((method, self.path, body))
                status, headers, resp_body = parent.handler_fn(method, self.path, body)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)

            def do_GET(self) -> None:
                self._handle("GET")

            def do_POST(self) -> None:
                self._handle("POST")

            def do_PUT(self) -> None:
                self._handle("PUT")

            def do_PATCH(self) -> None:
                self._handle("PATCH")

            def do_DELETE(self) -> None:
                self._handle("DELETE")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = int(self._server.server_port)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


class RecordedWSServer:
    """Async TCP listener recording any incoming WebSocket connection attempt."""

    def __init__(self) -> None:
        self.hits: list[bytes] = []
        self._srv: asyncio.AbstractServer | None = None
        self.port: int = 0

    async def start(self) -> int:
        async def _on_conn(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            data = await r.read(1024)
            self.hits.append(data)
            w.close()

        self._srv = await asyncio.start_server(_on_conn, "127.0.0.1", 0)
        self.port = int(self._srv.sockets[0].getsockname()[1])
        return self.port

    async def stop(self) -> None:
        if self._srv is not None:
            self._srv.close()
            await self._srv.wait_closed()
            self._srv = None


class _MappedPublicSocket:
    """Socket proxy connecting to local ``public_sim`` while reporting public peer IP."""

    def __init__(self, target_port: int, simulated_peer_ip: str, timeout: float) -> None:
        self._sock = socket.create_connection(("127.0.0.1", target_port), timeout=timeout)
        self._peer_ip = simulated_peer_ip

    def settimeout(self, value: float | None) -> None:
        self._sock.settimeout(value)

    def getpeername(self) -> tuple[str, int]:
        return (self._peer_ip, 80)

    def sendall(self, data: bytes) -> None:
        self._sock.sendall(data)

    def makefile(self, mode: str = "rb", buffering: int = -1) -> Any:
        return self._sock.makefile(mode, buffering=buffering)

    def close(self) -> None:
        self._sock.close()


def make_public_sim_socket_factory(
    public_sim_port: int, *, expected_public_ip: str = "93.184.216.34"
) -> Callable[[tuple[str, int], float], Any]:
    """Return a socket factory bridging ONLY validated public IP to the local test server."""

    def _factory(addr: tuple[str, int], timeout: float) -> Any:
        if addr == (expected_public_ip, 80):
            return _MappedPublicSocket(public_sim_port, expected_public_ip, timeout)
        raise AssertionError(f"Unexpected non-public TCP dial attempted: {addr}")

    return _factory


def build_public_sim_handler(victim_port: int) -> Any:
    """Return request handler simulating an external site that also embeds hostile links."""

    def _handle(method: str, path: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        if path == "/page":
            return (200, {"Content-Type": "text/html"}, b"<h1>Safe Public Page</h1>")
        if path == "/redirect-loopback":
            return (302, {"Location": f"http://127.0.0.1:{victim_port}/secret"}, b"")
        if path == "/redirect-metadata":
            return (302, {"Location": "http://169.254.169.254/latest/meta-data/"}, b"")
        if path == "/hostile-embeds":
            html = (
                f"<html><body>"
                f"<img id='im' src='http://127.0.0.1:{victim_port}/pixel.png'/>"
                f"<script src='http://[::1]:{victim_port}/evil.js'></script>"
                f"<iframe id='fr' src='http://127.0.0.1:{victim_port}/frame'></iframe>"
                f"<script>"
                f"window._f1 = fetch('http://127.0.0.1:{victim_port}/api').then(()=>'OK',()=>'BLK');"
                f"window._f2 = fetch('http://169.254.169.254/latest/').then(()=>'OK',()=>'BLK');"
                f"window._x = new Promise(r => {{ const x = new XMLHttpRequest();"
                f" x.open('GET','http://localhost:{victim_port}/x'); x.onload=()=>r('OK');"
                f" x.onerror=()=>r('BLK'); x.send(); }});"
                f"</script></body></html>"
            ).encode()
            return (200, {"Content-Type": "text/html"}, html)
        if path == "/sw-page":
            return (200, {"Content-Type": "text/html"}, b"<p>sw test</p>")
        if path == "/sw.js":
            return (
                200,
                {"Content-Type": "text/javascript"},
                b"self.addEventListener('fetch',()=>{});",
            )
        if path == "/download":
            hdr = {
                "Content-Type": "text/plain",
                "Content-Disposition": 'attachment; filename="a.txt"',
            }
            return (200, hdr, b"secret-download")
        if path == "/submit":
            return (200, {"Content-Type": "application/json"}, b'{"submitted":true}')
        if path == "/submit-redir":
            return (307, {"Location": "/submit"}, b"")
        return (404, {"Content-Type": "text/plain"}, b"not found")

    return _handle


def sim_resolver(host: str, port: int) -> list[str]:
    """Resolve only ``public.example.test`` to test public IP; fail on unexpected DNS."""
    if host == "public.example.test":
        return ["93.184.216.34"]
    raise AssertionError(f"Unvalidated DNS lookup attempted for {host}")


class RecordedUDPListener(asyncio.DatagramProtocol):
    """UDP datagram listener capturing any direct STUN/TURN UDP packet."""

    def __init__(self) -> None:
        self.packets: list[tuple[Any, bytes]] = []
        self._transport: asyncio.DatagramTransport | None = None
        self.port: int = 0

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self.packets.append((addr, data))

    async def start(self, host: str = "127.0.0.1") -> int:
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: self, local_addr=(host, 0)
        )
        self.port = int(self._transport.get_extra_info("sockname")[1])
        return self.port

    def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None


class RecordedTCPListener:
    """TCP listener capturing any direct ICE/TURN TCP connection attempt."""

    def __init__(self) -> None:
        self.connections: list[bytes] = []
        self._srv: asyncio.AbstractServer | None = None
        self.port: int = 0

    async def start(self, host: str = "127.0.0.1") -> int:
        async def _on_conn(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            data = await r.read(1024)
            self.connections.append(data)
            w.close()

        self._srv = await asyncio.start_server(_on_conn, host, 0)
        self.port = int(self._srv.sockets[0].getsockname()[1])
        return self.port

    async def stop(self) -> None:
        if self._srv is not None:
            self._srv.close()
            await self._srv.wait_closed()
            self._srv = None
