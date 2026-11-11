"""Security unit tests for Browser Egress Control, Proxy, and Controlled Automation."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.modules.browser.egress import (
    STRICT_CHROMIUM_ARGS,
    WINDOWS_ISOLATION_LIMITATION,
    BrowserEgressPolicy,
    LoopbackValidatingProxy,
    prepare_browser_mutation_approval,
)
from ultimate_mcp.modules.browser.policy_types import (
    PROXY_SYNTHETIC_HOST,
    build_proxy_chromium_args,
)


class SpySocket:
    """Socket test double proving pinned-IP dialing and zero bytes sent on block."""

    def __init__(self, peer_ip: str, peer_port: int, response_bytes: bytes) -> None:
        self._peer, self._response = (peer_ip, peer_port), response_bytes
        self.sent_payloads: list[bytes] = []
        self.closed = False

    def settimeout(self, value: float | None) -> None:
        return None

    def getpeername(self) -> tuple[str, int]:
        return self._peer

    def sendall(self, data: bytes) -> None:
        self.sent_payloads.append(bytes(data))

    def makefile(self, mode: str = "rb", buffering: int = -1) -> Any:
        return io.BytesIO(self._response)

    def close(self) -> None:
        self.closed = True


@dataclass
class SpyRequest:
    url: str
    method: str = "GET"
    resource_type: str = "document"
    headers: dict[str, str] | None = None
    post_data_buffer: bytes | None = None


class SpyRoute:
    def __init__(self, request: SpyRequest) -> None:
        self.request = request
        self.fulfilled: list[dict[str, Any]] = []
        self.aborted: list[str] = []

    async def fulfill(self, **kwargs: Any) -> None:
        self.fulfilled.append(kwargs)

    async def abort(self, error_code: str = "failed") -> None:
        self.aborted.append(error_code)

    async def continue_(self, **kwargs: Any) -> None:
        raise AssertionError("route.continue_() must NEVER be called!")


class SpyWebSocketRoute:
    def __init__(self, url: str) -> None:
        self.url, self.closed = url, []

    async def close(self, *, code: int | None = None, reason: str | None = None) -> None:
        self.closed.append((code, reason))


class SpyContext:
    def __init__(self) -> None:
        self.route_handlers: list[tuple[str, Any]] = []
        self.ws_handlers: list[tuple[str, Any]] = []

    async def route(self, pattern: str, handler: Any) -> None:
        self.route_handlers.append((pattern, handler))

    async def route_web_socket(self, pattern: str, handler: Any) -> None:
        self.ws_handlers.append((pattern, handler))


def test_host_resolver_has_no_exclude_and_proxy_args_are_strict() -> None:
    """Ensure EXCLUDE 127.0.0.1 is removed and proxy mode uses exact synthetic mapping."""
    policy = BrowserEgressPolicy()
    standalone_args = policy.build_launch_options(headless=True)["args"]
    ctx_opts = policy.build_context_options()

    assert "--host-resolver-rules=MAP * ~NOTFOUND" in STRICT_CHROMIUM_ARGS
    assert not any("EXCLUDE" in arg for arg in standalone_args)
    assert "--disable-quic" in standalone_args
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in standalone_args
    assert ctx_opts["service_workers"] == "block"
    assert ctx_opts["accept_downloads"] is False
    assert "CLONE_NEWNET" in WINDOWS_ISOLATION_LIMITATION

    proxy_args = build_proxy_chromium_args(41234)
    assert f"MAP {PROXY_SYNTHETIC_HOST} 127.0.0.1 , MAP * ~NOTFOUND" in proxy_args[0]
    assert f"--proxy-server=http://{PROXY_SYNTHETIC_HOST}:41234" in proxy_args
    assert "--proxy-bypass-list=<-loopback>" in proxy_args
    assert not any("EXCLUDE" in arg for arg in proxy_args)


async def test_route_and_websocket_block_hostile_ipv4_ipv6_and_ws_schemes() -> None:
    """Block loopback/private/IPv6 literals via route() and ws/wss via route_web_socket()."""
    dialed: list[tuple[str, int]] = []
    ssl_sni: list[str] = []

    def resolver(host: str, port: int) -> list[str]:
        return ["10.20.30.40"] if host == "rebind.corp" else ["93.184.216.34"]

    def socket_factory(addr: tuple[str, int], timeout: float) -> Any:
        dialed.append(addr)
        resp = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 9\r\n\r\n<p>ok</p>"
        return SpySocket(addr[0], addr[1], resp)

    def ssl_wrapper(sock: Any, hostname: str) -> Any:
        ssl_sni.append(hostname)
        return sock

    policy = BrowserEgressPolicy(
        resolver=resolver, socket_factory=socket_factory, ssl_wrapper=ssl_wrapper
    )
    ctx = SpyContext()
    await policy.attach_to_context(ctx)
    assert len(ctx.route_handlers) == 1 and ctx.route_handlers[0][0] == "**/*"
    assert len(ctx.ws_handlers) == 1 and ctx.ws_handlers[0][0] == "**/*"

    pub_route = SpyRoute(SpyRequest("https://public.example.com/page", resource_type="document"))
    await policy.handle_playwright_route(pub_route)
    assert len(pub_route.fulfilled) == 1 and dialed == [("93.184.216.34", 443)]
    assert ssl_sni == ["public.example.com"]

    hostile_http = [
        ("http://127.0.0.1/admin", "document"), ("http://localhost:8080/api", "xhr"),
        ("http://169.254.169.254/latest/meta-data/", "fetch"), ("http://10.0.0.5/p.png", "image"),
        ("http://192.168.1.1/app.js", "script"), ("http://[::1]/style.css", "stylesheet"),
        ("http://[fe80::1]/meta", "fetch"), ("http://[::ffff:127.0.0.1]/admin", "document"),
        ("http://rebind.corp/embed", "iframe"), ("file:///etc/passwd", "document"),
        ("https://public.example.com/telemetry", "ping"),
        ("https://update.googleapis.com/service/update2", "xhr"),
    ]
    for url, rtype in hostile_http:
        r = SpyRoute(SpyRequest(url, resource_type=rtype))
        await policy.handle_playwright_route(r)
        assert len(r.aborted) == 1, f"Expected abort for {url}"

    ws_urls = (
        "ws://127.0.0.1:8080/ws", "wss://localhost/ws", "ws://10.0.0.1/ws",
        "wss://93.184.216.34/ws", "ws://public.example.com/ws",
    )
    for ws_url in ws_urls:
        ws = SpyWebSocketRoute(ws_url)
        await policy.handle_playwright_websocket(ws)
        assert ws.closed == [(1008, "WebSockets blocked by egress policy")]

    assert dialed == [("93.184.216.34", 443)]


@pytest.mark.parametrize("resource_type", ["document", "image", "script", "fetch", "iframe"])
async def test_browser_blocks_redirects_to_private_and_metadata(resource_type: str) -> None:
    """Prove 302 redirect to metadata IP is aborted before any Hop 2 socket."""
    dialed: list[tuple[str, int]] = []
    payload = (
        b"HTTP/1.1 302 Found\r\n"
        b"Location: http://169.254.169.254/latest/meta-data/\r\nContent-Length: 0\r\n\r\n"
    )

    def socket_factory(addr: tuple[str, int], timeout: float) -> Any:
        dialed.append(addr)
        return SpySocket(addr[0], addr[1], payload)

    policy = BrowserEgressPolicy(
        resolver=lambda h, p: ["93.184.216.34"], socket_factory=socket_factory
    )
    url = "http://redirector.example.com/asset"
    route = SpyRoute(SpyRequest(url, resource_type=resource_type))
    await policy.handle_playwright_route(route)
    assert len(route.aborted) == 1 and dialed == [("93.184.216.34", 80)]


async def test_browser_blocks_socket_peer_rebinding_with_zero_http_bytes() -> None:
    """Abort if peer IP mismatches pre-validated IP before any HTTP bytes leave."""
    sockets: list[SpySocket] = []

    def rebonded_socket_factory(addr: tuple[str, int], timeout: float) -> Any:
        s = SpySocket("127.0.0.1", addr[1], b"")
        sockets.append(s)
        return s

    policy = BrowserEgressPolicy(
        resolver=lambda h, p: ["93.184.216.34"], socket_factory=rebonded_socket_factory
    )
    route = SpyRoute(SpyRequest("https://rebind.example.com/", resource_type="document"))
    await policy.handle_playwright_route(route)
    assert len(route.aborted) == 1 and sockets[0].closed and sockets[0].sent_payloads == []


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_state_changing_methods_require_bound_single_use_out_of_band_approval(
    tmp_path: Path, method: str
) -> None:
    """Verify POST/PUT/PATCH/DELETE require single-use digest L3 approval & block redirects."""
    now_box = [1000.0]
    broker = ApprovalBroker(tmp_path / "approvals.sqlite3", clock=lambda: now_box[0])
    dialed: list[tuple[str, int]] = []
    redirect_mode = [False]

    def sock_factory(addr: tuple[str, int], timeout: float) -> Any:
        dialed.append(addr)
        if redirect_mode[0]:
            payload = (
                b"HTTP/1.1 307 Temporary Redirect\r\n"
                b"Location: https://api.example.com/v2/next\r\nContent-Length: 0\r\n\r\n"
            )
        else:
            payload = (
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b'Content-Length: 11\r\n\r\n{"ok":true}'
            )
        return SpySocket(addr[0], addr[1], payload)

    policy = BrowserEgressPolicy(
        approval_broker=broker,
        resolver=lambda h, p: ["93.184.216.34"],
        socket_factory=sock_factory,
        ssl_wrapper=lambda s, h: s,
    )
    target, body = "https://api.example.com/items", b'{"item":"x"}'

    def mk(raw_body: bytes = body) -> SpyRoute:
        return SpyRoute(
            SpyRequest(target, method=method, resource_type="fetch", post_data_buffer=raw_body)
        )

    def mk_apr(ttl: int = 30, *, approve: bool = True) -> str:
        rec = prepare_browser_mutation_approval(
            broker, method=method, url=target, body=body, ttl_seconds=ttl
        )
        if approve:
            broker.decide_out_of_band(rec.approval_id, approve=True)
        policy.authorize_mutation(rec.approval_id)
        return rec.approval_id

    await policy.handle_playwright_route(r0 := mk())
    assert len(r0.aborted) == 1 and dialed == []

    aid = mk_apr(ttl=30, approve=False)
    await policy.handle_playwright_route(r_pend := mk())
    assert len(r_pend.aborted) == 1 and dialed == []

    broker.decide_out_of_band(aid, approve=True)
    policy.authorize_mutation(aid)
    await policy.handle_playwright_route(r_tamper := mk(b'{"item":"EVIL"}'))
    assert len(r_tamper.aborted) == 1 and dialed == []

    await policy.handle_playwright_route(r_ok := mk())
    assert len(r_ok.fulfilled) == 1 and len(dialed) == 1

    policy.authorize_mutation(aid)
    await policy.handle_playwright_route(r_replay := mk())
    assert len(r_replay.aborted) == 1 and len(dialed) == 1

    exp_id = mk_apr(ttl=10)
    now_box[0] = broker.get_record(exp_id).expires_at
    await policy.handle_playwright_route(r_exp := mk())
    assert len(r_exp.aborted) == 1 and len(dialed) == 1

    redir_id = mk_apr(ttl=30)
    redirect_mode[0] = True
    await policy.handle_playwright_route(r_red := mk())
    assert len(r_red.aborted) == 1
    assert broker.get_record(redir_id).status.value == "consumed"


async def test_loopback_validating_proxy_fails_closed_on_connect_and_ssrf() -> None:
    """Verify LoopbackValidatingProxy rejects CONNECT, POST, and loopback/IPv6 with 403."""
    async with LoopbackValidatingProxy(resolver=lambda h, p: ["93.184.216.34"]) as proxy:
        for req_line in (
            b"CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1:443\r\n\r\n",
            b"GET http://127.0.0.1:8080/secret HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
            b"GET http://[::1]:8080/secret HTTP/1.1\r\nHost: [::1]\r\n\r\n",
            b"POST http://93.184.216.34/submit HTTP/1.1\r\nHost: 93.184.216.34\r\n\r\n",
        ):
            r, w = await asyncio.open_connection("127.0.0.1", proxy.port)
            w.write(req_line)
            await w.drain()
            resp = await r.read(1024)
            w.close()
            assert resp.startswith(b"HTTP/1.1 403 Forbidden")
