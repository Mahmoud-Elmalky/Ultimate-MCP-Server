"""Security tests for Phase 03 Browser Security & Network Egress Prototype."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import pytest

from ultimate_mcp.modules.browser.egress import (
    STRICT_CHROMIUM_ARGS,
    WINDOWS_ISOLATION_LIMITATION,
    ApprovedMethodRule,
    BrowserEgressPolicy,
)


class SpySocket:
    """Socket test double proving pinned-IP dialing and zero bytes sent on block."""

    def __init__(self, peer_ip: str, peer_port: int, response_bytes: bytes) -> None:
        self._peer = (peer_ip, peer_port)
        self._response = response_bytes
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
    """Playwright Route test double recording fulfill/abort/continue calls."""

    def __init__(self, request: SpyRequest) -> None:
        self.request = request
        self.fulfilled: list[dict[str, Any]] = []
        self.aborted: list[str] = []
        self.continued: int = 0

    async def fulfill(self, **kwargs: Any) -> None:
        self.fulfilled.append(kwargs)

    async def abort(self, error_code: str = "failed") -> None:
        self.aborted.append(error_code)

    async def continue_(self, **kwargs: Any) -> None:
        self.continued += 1
        raise AssertionError("route.continue_() must NEVER be called for external requests!")


class SpyContext:
    def __init__(self) -> None:
        self.handlers: list[tuple[str, Any]] = []

    async def route(self, pattern: str, handler: Any) -> None:
        self.handlers.append((pattern, handler))


def test_browser_launch_and_context_hardening_options() -> None:
    policy = BrowserEgressPolicy()
    launch_opts = policy.build_launch_options(headless=True)
    ctx_opts = policy.build_context_options()

    assert "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1" in STRICT_CHROMIUM_ARGS
    assert "--disable-background-networking" in launch_opts["args"]
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in launch_opts["args"]
    assert ctx_opts["service_workers"] == "block"
    assert ctx_opts["accept_downloads"] is False
    assert ctx_opts["ignore_https_errors"] is False
    assert "user_data_dir" not in ctx_opts
    assert "CLONE_NEWNET" in WINDOWS_ISOLATION_LIMITATION


async def test_browser_blocks_private_loopback_metadata_and_subresources() -> None:
    dialed: list[tuple[str, int]] = []
    ssl_sni: list[str] = []

    def resolver(host: str, port: int) -> list[str]:
        return ["10.20.30.40"] if host == "rebind.corp" else ["93.184.216.34"]

    def socket_factory(addr: tuple[str, int], timeout: float) -> Any:
        dialed.append(addr)
        return SpySocket(
            addr[0],
            addr[1],
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 9\r\n\r\n<p>ok</p>",
        )

    def ssl_wrapper(sock: Any, hostname: str) -> Any:
        ssl_sni.append(hostname)
        return sock

    policy = BrowserEgressPolicy(
        resolver=resolver, socket_factory=socket_factory, ssl_wrapper=ssl_wrapper
    )
    ctx = SpyContext()
    await policy.attach_to_context(ctx)
    assert len(ctx.handlers) == 1 and ctx.handlers[0][0] == "**/*"

    pub_route = SpyRoute(SpyRequest("https://public.example.com/page", resource_type="document"))
    await policy.handle_playwright_route(pub_route)
    assert len(pub_route.fulfilled) == 1
    assert pub_route.fulfilled[0]["body"] == b"<p>ok</p>"
    assert pub_route.continued == 0
    assert dialed == [("93.184.216.34", 443)]
    assert ssl_sni == ["public.example.com"]

    # Hostile targets across main navigation, subresources, fetch/xhr, and frames
    hostile_cases = [
        ("http://127.0.0.1/admin", "document"),
        ("http://localhost:8080/api", "xhr"),
        ("http://169.254.169.254/latest/meta-data/iam/security-credentials/", "fetch"),
        ("http://metadata.google.internal/computeMetadata/v1/", "xhr"),
        ("http://10.0.0.5/logo.png", "image"),
        ("http://192.168.1.1/app.js", "script"),
        ("http://[::1]/style.css", "stylesheet"),
        ("http://rebind.corp/embed", "iframe"),
        ("file:///etc/passwd", "document"),
        ("ws://echo.example.com/socket", "websocket"),
        ("wss://127.0.0.1/ws", "websocket"),
        ("https://public.example.com/telemetry", "ping"),
        ("https://update.googleapis.com/service/update2", "xhr"),
    ]
    for target_url, rtype in hostile_cases:
        r = SpyRoute(SpyRequest(target_url, resource_type=rtype))
        await policy.handle_playwright_route(r)
        assert len(r.aborted) == 1, f"Expected abort for {rtype} {target_url}"
        assert r.fulfilled == []
        assert r.continued == 0

    # Confirm NONE of the hostile targets ever triggered a socket connection
    assert dialed == [("93.184.216.34", 443)]


@pytest.mark.parametrize("resource_type", ["document", "image", "script", "fetch", "iframe"])
async def test_browser_blocks_redirects_to_private_and_metadata_across_resource_types(
    resource_type: str,
) -> None:
    """Prove 302 redirect from public URL to metadata/loopback is blocked before Hop 2 socket."""
    dialed: list[tuple[str, int]] = []

    def resolver(host: str, port: int) -> list[str]:
        if host == "redirector.example.com":
            return ["93.184.216.34"]
        raise AssertionError(f"DNS lookup should not reach private host: {host}")

    redirect_payload = (
        b"HTTP/1.1 302 Found\r\n"
        b"Location: http://169.254.169.254/latest/meta-data/\r\n"
        b"Content-Length: 0\r\n\r\n"
    )

    def socket_factory(addr: tuple[str, int], timeout: float) -> Any:
        dialed.append(addr)
        return SpySocket(addr[0], addr[1], redirect_payload)

    policy = BrowserEgressPolicy(resolver=resolver, socket_factory=socket_factory)
    route = SpyRoute(SpyRequest("http://redirector.example.com/asset", resource_type=resource_type))
    await policy.handle_playwright_route(route)

    assert len(route.aborted) == 1
    assert route.fulfilled == []
    assert route.continued == 0
    # Only Hop 1 public IP was dialed; Hop 2 metadata IP was blocked before connecting
    assert dialed == [("93.184.216.34", 80)]


async def test_browser_blocks_socket_peer_rebinding_with_zero_http_bytes() -> None:
    """If peer IP mismatches pre-validated public IP, abort route and send zero HTTP bytes."""
    sockets: list[SpySocket] = []

    def rebonded_socket_factory(addr: tuple[str, int], timeout: float) -> Any:
        s = SpySocket("127.0.0.1", addr[1], b"")
        sockets.append(s)
        return s

    policy = BrowserEgressPolicy(
        resolver=lambda h, p: ["93.184.216.34"],
        socket_factory=rebonded_socket_factory,
    )
    route = SpyRoute(SpyRequest("https://rebind.example.com/", resource_type="document"))
    await policy.handle_playwright_route(route)

    assert len(route.aborted) == 1
    assert sockets[0].closed is True
    assert sockets[0].sent_payloads == []


async def test_browser_safe_post_and_download_policy() -> None:
    """Verify state-changing POST requires approval and STILL obeys SSRF + redirect guards."""
    sockets: list[SpySocket] = []
    ssl_sni: list[str] = []

    def resolver(host: str, port: int) -> list[str]:
        if host == "api.example.com":
            return ["93.184.216.34"]
        return ["1.1.1.1"] if host == "thirdparty.example.org" else ["127.0.0.1"]

    def post_socket_factory(addr: tuple[str, int], timeout: float) -> Any:
        body = (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b'Content-Length: 11\r\n\r\n{"ok":true}'
            if addr == ("93.184.216.34", 443)
            else (
                b"HTTP/1.1 307 Temporary Redirect\r\n"
                b"Location: https://thirdparty.example.org/collect\r\nContent-Length: 0\r\n\r\n"
            )
        )
        sock = SpySocket(addr[0], addr[1], body)
        sockets.append(sock)
        return sock

    def ssl_wrapper(sock: Any, hostname: str) -> Any:
        ssl_sni.append(hostname)
        return sock

    default_policy = BrowserEgressPolicy(
        resolver=resolver, socket_factory=post_socket_factory, ssl_wrapper=ssl_wrapper
    )
    r_unapproved = SpyRoute(
        SpyRequest(
            "https://api.example.com/search",
            method="POST",
            resource_type="fetch",
            post_data_buffer=b'{"q":"test"}',
        )
    )
    await default_policy.handle_playwright_route(r_unapproved)
    assert len(r_unapproved.aborted) == 1 and sockets == []

    rule = ApprovedMethodRule(
        method="POST", origin="https://api.example.com", path_prefix="/search"
    )
    approved_policy = BrowserEgressPolicy(
        approved_method_rules=(rule,),
        resolver=resolver,
        socket_factory=post_socket_factory,
        ssl_wrapper=ssl_wrapper,
    )
    r_ok = SpyRoute(
        SpyRequest(
            "https://api.example.com/search",
            method="POST",
            resource_type="fetch",
            post_data_buffer=b'{"q":"test"}',
        )
    )
    await approved_policy.handle_playwright_route(r_ok)
    assert len(r_ok.fulfilled) == 1 and r_ok.fulfilled[0]["body"] == b'{"ok":true}'
    assert r_ok.continued == 0 and ssl_sni == ["api.example.com"]

    rule_redir = ApprovedMethodRule(
        method="POST", origin="https://redir.example.com", path_prefix="/"
    )
    redir_policy = BrowserEgressPolicy(
        approved_method_rules=(rule_redir,),
        resolver=lambda h, p: ["1.1.1.1"],
        socket_factory=post_socket_factory,
        ssl_wrapper=ssl_wrapper,
    )
    r_307 = SpyRoute(
        SpyRequest(
            "https://redir.example.com/submit",
            method="POST",
            resource_type="fetch",
            post_data_buffer=b"secret-body",
        )
    )
    await redir_policy.handle_playwright_route(r_307)
    assert len(r_307.aborted) == 1

    def dl_socket(addr: tuple[str, int], timeout: float) -> Any:
        return SpySocket(
            addr[0],
            addr[1],
            (
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                b'Content-Disposition: attachment; filename="payload.txt"\r\n'
                b"Content-Length: 4\r\n\r\ntest"
            ),
        )

    dl_policy = BrowserEgressPolicy(
        resolver=resolver, socket_factory=dl_socket, ssl_wrapper=ssl_wrapper
    )
    r_dl = SpyRoute(SpyRequest("https://api.example.com/export", resource_type="document"))
    await dl_policy.handle_playwright_route(r_dl)
    assert len(r_dl.aborted) == 1
