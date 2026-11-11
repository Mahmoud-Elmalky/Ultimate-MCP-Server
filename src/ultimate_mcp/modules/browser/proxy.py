"""Fail-closed loopback validating HTTP proxy for Playwright Chromium egress defense-in-depth.

Design:
- Chromium launches with ``--proxy-server=http://egress-proxy.invalid:<port>`` and
  ``--proxy-bypass-list=<-loopback>`` plus ``--host-resolver-rules=MAP egress-proxy.invalid
  127.0.0.1 , MAP * ~NOTFOUND``.
- Any Chromium HTTP or CONNECT request that ever bypasses Playwright ``context.route`` hits
  this proxy strictly on ``127.0.0.1:<port>``.
- ``CONNECT`` tunnels (opaque HTTPS/WebSocket streams) and state-changing methods are denied
  unconditionally with ``403 Forbidden`` so no uninspected tunnel can open.
- Plain HTTP requests are executed ONLY via pinned-IP ``fetch_safe_url`` (never raw forwarding),
  blocking loopback, private, link-local, metadata, literal IPv4/IPv6 bypasses with ``403``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ultimate_mcp.core.http import (
    SAFE_AUTONOMOUS_METHODS,
    ResolverFunc,
    default_dns_resolver,
    fetch_safe_url,
)
from ultimate_mcp.core.http_transport import (
    SocketFactory,
    SSLWrapper,
    default_socket_factory,
    default_ssl_wrapper,
)
from ultimate_mcp.core.models import UltimateMCPError
from ultimate_mcp.modules.browser.policy_types import (
    BROWSER_ALLOWED_CONTENT_TYPES,
    HOP_BY_HOP_HEADERS,
)

MAX_PROXY_HEAD_BYTES = 32 * 1024


@dataclass(frozen=True)
class ProxyAuditEntry:
    """Observed request reaching the fallback loopback validating proxy."""

    method: str
    target: str
    status_code: int
    reason: str


class LoopbackValidatingProxy:
    """Fail-closed loopback HTTP proxy bound on ``127.0.0.1:0``."""

    def __init__(
        self,
        *,
        resolver: ResolverFunc = default_dns_resolver,
        socket_factory: SocketFactory = default_socket_factory,
        ssl_wrapper: SSLWrapper = default_ssl_wrapper,
        max_response_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self._resolver = resolver
        self._socket_factory = socket_factory
        self._ssl_wrapper = ssl_wrapper
        self._max_response_bytes = max_response_bytes
        self._server: asyncio.AbstractServer | None = None
        self.port: int = 0
        self.audit_log: list[ProxyAuditEntry] = []

    async def start(self) -> int:
        """Bind on ``127.0.0.1:0`` and return the assigned ephemeral port."""
        if self._server is not None:
            return self.port
        self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
        sockname = self._server.sockets[0].getsockname()
        self.port = int(sockname[1])
        return self.port

    async def stop(self) -> None:
        """Stop the proxy server cleanly."""
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def __aenter__(self) -> LoopbackValidatingProxy:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    async def _send_error(
        self, writer: asyncio.StreamWriter, status: int, reason: str, message: str
    ) -> None:
        body = message.encode("utf-8", errors="replace")
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        writer.write(head + body)
        await writer.drain()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw_head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError):
            writer.close()
            return
        if len(raw_head) > MAX_PROXY_HEAD_BYTES:
            await self._send_error(writer, 431, "Request Header Fields Too Large", "Header limit")
            writer.close()
            return
        first_line = raw_head.split(b"\r\n", 1)[0].decode("latin1", errors="replace")
        parts = first_line.split(" ", 2)
        if len(parts) < 2:
            await self._send_error(writer, 400, "Bad Request", "Malformed proxy request line")
            writer.close()
            return
        await self._dispatch_proxy_request(writer, parts[0].strip().upper(), parts[1].strip())

    async def _dispatch_proxy_request(
        self, writer: asyncio.StreamWriter, method: str, target: str
    ) -> None:
        if method == "CONNECT" or method not in SAFE_AUTONOMOUS_METHODS:
            reason = (
                "Raw CONNECT tunnels blocked by proxy egress policy."
                if method == "CONNECT"
                else f"Proxy rejects state-changing method '{method}'."
            )
            self.audit_log.append(ProxyAuditEntry(method, target, 403, reason))
            await self._send_error(writer, 403, "Forbidden", reason)
            writer.close()
            return

        try:
            resp = await asyncio.to_thread(
                fetch_safe_url,
                target,
                method=method,
                allowed_content_types=BROWSER_ALLOWED_CONTENT_TYPES,
                max_response_bytes=self._max_response_bytes,
                raise_for_status=False,
                resolver=self._resolver,
                socket_factory=self._socket_factory,
                ssl_wrapper=self._ssl_wrapper,
            )
        except UltimateMCPError as exc:
            msg = f"{exc.code.value}: {exc.message}"
            self.audit_log.append(ProxyAuditEntry(method, target, 403, msg))
            await self._send_error(writer, 403, "Forbidden", msg)
            writer.close()
            return

        self.audit_log.append(ProxyAuditEntry(method, target, resp.status_code, "validated_proxy"))
        header_lines = [f"HTTP/1.1 {resp.status_code} OK"]
        for k, v in resp.headers.items():
            if k.lower() not in HOP_BY_HOP_HEADERS:
                header_lines.append(f"{k}: {v}")
        header_lines.append(f"Content-Length: {len(resp.body)}")
        header_lines.append("Connection: close")
        writer.write(("\r\n".join(header_lines) + "\r\n\r\n").encode("latin1") + resp.body)
        await writer.drain()
        writer.close()
