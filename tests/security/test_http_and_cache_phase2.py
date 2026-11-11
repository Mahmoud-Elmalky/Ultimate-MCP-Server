"""Adversarial security and unit tests for SSRF/pinned-IP HTTP client and TTL cache (Phase 02)."""

from __future__ import annotations

import gzip
import socket
import threading
from typing import Any

import pytest

from ultimate_mcp.core.cache import (
    CacheDataClass,
    PerClassTTLCache,
    build_cache_key,
)
from ultimate_mcp.core.http import (
    RawHTTPResponse,
    default_dns_resolver,
    fetch_safe_url,
    is_public_ip_address,
    validate_and_resolve_url,
)
from ultimate_mcp.core.http_transport import (
    normalize_socket_ip,
    send_pinned_http_request,
)
from ultimate_mcp.core.models import ErrorCode, UltimateMCPError


class FakeSocket:
    """In-memory socket test double that records every transmitted byte before response."""

    def __init__(
        self,
        peer_ip: str,
        peer_port: int = 80,
        response_bytes: bytes = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n"
            b"Content-Length: 15\r\n"
            b"\r\n"
            b"<h1>Pinned</h1>"
        ),
    ) -> None:
        self._peer = (peer_ip, peer_port)
        self._response_bytes = response_bytes
        self.sent_Payloads: list[bytes] = []
        self.closed = False
        self.timeout: float | None = None
        self.sni_hostname: str | None = None

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def getpeername(self) -> tuple[str, int]:
        return self._peer

    def sendall(self, data: bytes) -> None:
        self.sent_Payloads.append(bytes(data))

    def makefile(self, mode: str = "rb", buffering: int = -1) -> Any:
        import io

        return io.BytesIO(self._response_bytes)

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("ip_str", "expected"),
    [
        ("93.184.216.34", True),
        ("8.8.8.8", True),
        ("2606:2800:220:1:248:1893:25c8:1946", True),
        ("127.0.0.1", False),
        ("127.0.0.53", False),
        ("10.0.0.1", False),
        ("172.16.0.1", False),
        ("172.31.255.254", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("198.18.0.1", False),
        ("0.0.0.0", False),
        ("255.255.255.255", False),
        ("224.0.0.1", False),
        ("::1", False),
        ("::", False),
        ("fe80::1", False),
        ("fc00::1", False),
        ("fd12:3456:789a::1", False),
        ("::ffff:127.0.0.1", False),
        ("::ffff:169.254.169.254", False),
        ("2001:db8::1", False),
        ("100::1", False),
        ("not-an-ip", False),
    ],
)
def test_is_public_ip_address_matrix(ip_str: str, expected: bool) -> None:
    assert is_public_ip_address(ip_str) is expected


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",
        "ftp://example.com/data.txt",
        "gopher://example.com:70/1",
        "javascript:alert(1)",
        "http://user:pass@example.com/",
        "http://localhost/admin",
        "http://sub.localhost/test",
        "http://printer.local/status",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://127.0.0.1/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/secret",
        "http://[::ffff:127.0.0.1]/secret",
        "http://example.com:8080/internal",
    ],
)
def test_validate_and_resolve_url_blocks_ssrf_vectors(bad_url: str) -> None:
    def fake_resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    with pytest.raises(UltimateMCPError) as exc_info:
        validate_and_resolve_url(bad_url, resolver=fake_resolver)
    assert exc_info.value.code == ErrorCode.SSRF_BLOCKED


def test_validate_and_resolve_url_blocks_mixed_dns_records() -> None:
    def mixed_v4_resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "10.0.0.42"]

    with pytest.raises(UltimateMCPError) as exc_v4:
        validate_and_resolve_url("https://example.com/path", resolver=mixed_v4_resolver)
    assert exc_v4.value.code == ErrorCode.SSRF_BLOCKED

    def mixed_v6_resolver(host: str, port: int) -> list[str]:
        return ["2606:2800:220:1:248:1893:25c8:1946", "::1"]

    with pytest.raises(UltimateMCPError) as exc_v6:
        validate_and_resolve_url("https://example.com/path", resolver=mixed_v6_resolver)
    assert exc_v6.value.code == ErrorCode.SSRF_BLOCKED


def test_pinned_connection_never_transmits_http_when_peer_is_private_or_unapproved() -> None:
    """CRITICAL: Prove zero HTTP bytes are transmitted if socket peer IP is private/rebound."""
    created_sockets: list[FakeSocket] = []
    connected_targets: list[tuple[str, int]] = []

    def rebonded_socket_factory(address: tuple[str, int], timeout: float) -> Any:
        connected_targets.append(address)
        # Simulate an OS/network layer that connected to 127.0.0.1 despite public pre-check
        sock = FakeSocket(peer_ip="127.0.0.1", peer_port=address[1])
        created_sockets.append(sock)
        return sock

    with pytest.raises(UltimateMCPError) as exc_info:
        fetch_safe_url(
            "http://rebind.attacker.example/steal",
            resolver=lambda h, p: ["93.184.216.34"],
            socket_factory=rebonded_socket_factory,
        )

    assert exc_info.value.code == ErrorCode.SSRF_BLOCKED
    # Socket was dialed using the pinned public IP (not hostname)
    assert connected_targets == [("93.184.216.34", 80)]
    assert len(created_sockets) == 1
    # Socket was closed immediately and ZERO HTTP request bytes were ever sent!
    assert created_sockets[0].closed is True
    assert created_sockets[0].sent_Payloads == []

    # Also test when peer IP is public but NOT in the validated pre_ips set
    unapproved_sockets: list[FakeSocket] = []

    def unapproved_public_socket_factory(address: tuple[str, int], timeout: float) -> Any:
        sock = FakeSocket(peer_ip="198.51.100.99", peer_port=address[1])
        unapproved_sockets.append(sock)
        return sock

    with pytest.raises(UltimateMCPError) as exc_unapproved:
        fetch_safe_url(
            "http://rebind.attacker.example/steal",
            resolver=lambda h, p: ["93.184.216.34"],
            socket_factory=unapproved_public_socket_factory,
        )
    assert exc_unapproved.value.code == ErrorCode.SSRF_BLOCKED
    assert unapproved_sockets[0].closed is True
    assert unapproved_sockets[0].sent_Payloads == []


def test_pinned_connection_eliminates_independent_client_dns_lookup_and_preserves_host_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove no second DNS lookup occurs during socket connect, and Host/SNI use original host."""
    # Even if system socket.getaddrinfo would return 127.0.0.1 if called by an HTTP client,
    # our pinned socket transport connects directly to pre_ips[0] ("93.184.216.34").
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )

    dialed_addresses: list[tuple[str, int]] = []
    created_sockets: list[FakeSocket] = []

    def fake_socket_factory(address: tuple[str, int], timeout: float) -> Any:
        dialed_addresses.append(address)
        sock = FakeSocket(peer_ip=address[0], peer_port=address[1])
        created_sockets.append(sock)
        return sock

    def fake_ssl_wrapper(sock: Any, server_hostname: str) -> Any:
        sock.sni_hostname = server_hostname
        return sock

    res = fetch_safe_url(
        "https://example.com/secure-page?q=1",
        resolver=lambda h, p: ["93.184.216.34"],
        socket_factory=fake_socket_factory,
        ssl_wrapper=fake_ssl_wrapper,
    )
    assert res.status_code == 200
    assert res.text == "<h1>Pinned</h1>"
    assert dialed_addresses == [("93.184.216.34", 443)]
    assert created_sockets[0].sni_hostname == "example.com"
    transmitted = b"".join(created_sockets[0].sent_Payloads).decode("latin-1")
    assert "GET /secure-page?q=1 HTTP/1.1" in transmitted
    assert "Host: example.com\r\n" in transmitted
    assert "Connection: close\r\n" in transmitted


def test_pinned_ipv6_connection_and_gzip_decompression() -> None:
    ipv6_addr = "2606:2800:220:1:248:1893:25c8:1946"
    compressed = gzip.compress(b"<html><body>IPv6 Content</body></html>")
    raw_http = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/html\r\n"
        b"Content-Encoding: gzip\r\n"
        + f"Content-Length: {len(compressed)}\r\n\r\n".encode("ascii")
        + compressed
    )
    created_sockets: list[FakeSocket] = []

    def ipv6_socket_factory(address: tuple[str, int], timeout: float) -> Any:
        sock = FakeSocket(
            peer_ip=f"{address[0]}%eth0",
            peer_port=address[1],
            response_bytes=raw_http,
        )
        created_sockets.append(sock)
        return sock

    res = fetch_safe_url(
        "http://ipv6.example.com/",
        resolver=lambda h, p: [ipv6_addr],
        socket_factory=ipv6_socket_factory,
    )
    assert res.status_code == 200
    assert "IPv6 Content" in res.text
    assert res.resolved_ips == (ipv6_addr,)
    assert normalize_socket_ip(f"{ipv6_addr}%eth0") == ipv6_addr


def test_proxy_environment_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify HTTP_PROXY, HTTPS_PROXY, and ALL_PROXY cannot redirect traffic to a local proxy."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1080")

    dialed: list[tuple[str, int]] = []

    def tracking_socket_factory(address: tuple[str, int], timeout: float) -> Any:
        dialed.append(address)
        return FakeSocket(peer_ip=address[0], peer_port=address[1])

    res = fetch_safe_url(
        "http://example.com/test",
        resolver=lambda h, p: ["93.184.216.34"],
        socket_factory=tracking_socket_factory,
    )
    assert res.status_code == 200
    assert dialed == [("93.184.216.34", 80)]


def test_dns_changes_between_requests_and_post_request_rebinding() -> None:
    """Verify DNS changes between request 1 and request 2 or during request are blocked."""
    # Case A: DNS flips from public on pre-check to 127.0.0.1 on post-check
    calls = 0

    def flipping_resolver(host: str, port: int) -> list[str]:
        nonlocal calls
        calls += 1
        return ["93.184.216.34"] if calls == 1 else ["127.0.0.1"]

    with pytest.raises(UltimateMCPError) as exc_post:
        fetch_safe_url(
            "http://rebind.example.com/data",
            resolver=flipping_resolver,
            socket_factory=lambda addr, to: FakeSocket(peer_ip=addr[0], peer_port=addr[1]),
        )
    assert exc_post.value.code == ErrorCode.SSRF_BLOCKED

    # Case B: Request 1 succeeds with public IP, then DNS changes to 10.0.0.1 before Request 2
    current_ips = ["93.184.216.34"]
    dialed: list[tuple[str, int]] = []

    def dynamic_resolver(host: str, port: int) -> list[str]:
        return list(current_ips)

    def tracking_factory(addr: tuple[str, int], timeout: float) -> Any:
        dialed.append(addr)
        return FakeSocket(peer_ip=addr[0], peer_port=addr[1])

    res1 = fetch_safe_url(
        "http://service.example.com/v1",
        resolver=dynamic_resolver,
        socket_factory=tracking_factory,
    )
    assert res1.status_code == 200
    assert len(dialed) == 1

    # Now DNS changes to private IP before second request; no connection pool reuse is allowed
    current_ips = ["10.0.0.1"]
    with pytest.raises(UltimateMCPError) as exc_req2:
        fetch_safe_url(
            "http://service.example.com/v1",
            resolver=dynamic_resolver,
            socket_factory=tracking_factory,
        )
    assert exc_req2.value.code == ErrorCode.SSRF_BLOCKED
    # Confirm no second socket was ever opened!
    assert len(dialed) == 1


def test_fetch_safe_url_blocks_redirect_to_private_ip_before_connecting() -> None:
    dialed: list[tuple[str, int]] = []

    def resolver(host: str, port: int) -> list[str]:
        if host == "public.example.com":
            return ["93.184.216.34"]
        if host == "internal.corp":
            return ["10.0.0.99"]
        return ["93.184.216.34"]

    redirect_http = (
        b"HTTP/1.1 302 Found\r\n"
        b"Location: http://internal.corp/secret\r\n"
        b"Content-Length: 0\r\n\r\n"
    )

    def socket_factory(addr: tuple[str, int], timeout: float) -> Any:
        dialed.append(addr)
        return FakeSocket(peer_ip=addr[0], peer_port=addr[1], response_bytes=redirect_http)

    with pytest.raises(UltimateMCPError) as exc_info:
        fetch_safe_url(
            "http://public.example.com/start",
            resolver=resolver,
            socket_factory=socket_factory,
        )
    assert exc_info.value.code == ErrorCode.SSRF_BLOCKED
    # Hop 1 connected to 93.184.216.34, Hop 2 was blocked before any socket connection!
    assert dialed == [("93.184.216.34", 80)]


def test_fetch_safe_url_enforces_response_size_and_content_type_limits() -> None:
    def resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    def oversized_sender(
        url: str,
        headers: dict[str, str],
        connect_timeout: float,
        read_timeout: float,
        max_bytes: int,
    ) -> RawHTTPResponse:
        return RawHTTPResponse(
            status_code=200,
            headers={"content-type": "text/html"},
            body=b"x" * (max_bytes + 10),
            url=url,
            peer_ip="93.184.216.34",
        )

    with pytest.raises(UltimateMCPError) as exc_size:
        fetch_safe_url(
            "https://example.com/big",
            max_response_bytes=100,
            resolver=resolver,
            sender=oversized_sender,
        )
    assert exc_size.value.code == ErrorCode.RESOURCE_LIMIT_EXCEEDED

    def binary_sender(
        url: str,
        headers: dict[str, str],
        connect_timeout: float,
        read_timeout: float,
        max_bytes: int,
    ) -> RawHTTPResponse:
        return RawHTTPResponse(
            status_code=200,
            headers={"content-type": "application/octet-stream"},
            body=b"\x89PNG\r\n",
            url=url,
            peer_ip="93.184.216.34",
        )

    with pytest.raises(UltimateMCPError) as exc_ct:
        fetch_safe_url(
            "https://example.com/file.bin",
            resolver=resolver,
            sender=binary_sender,
        )
    assert exc_ct.value.code == ErrorCode.UNSUPPORTED_FORMAT


def test_per_class_ttl_cache_freshness_stale_and_eviction() -> None:
    fake_now = 1000.0

    def clock() -> float:
        return fake_now

    cache: PerClassTTLCache[dict[str, Any]] = PerClassTTLCache(
        max_entries=3,
        max_entries_per_class=2,
        ttl_overrides={CacheDataClass.WEATHER: 60.0, CacheDataClass.MARKET_QUOTE: 10.0},
        clock=clock,
    )

    key_w = build_cache_key(CacheDataClass.WEATHER, "wttr.in", "Cairo")
    cache.put(key_w, CacheDataClass.WEATHER, "wttr.in", {"temp_c": 28})

    fake_now = 1030.0
    hit = cache.get(key_w)
    assert hit is not None
    assert hit.is_stale is False
    assert hit.age_seconds == 30.0
    assert "Cached result from wttr.in" in hit.freshness_note()

    # Exact expiration boundary (now == expires_at is still fresh)
    fake_now = 1060.0
    boundary_hit = cache.get(key_w)
    assert boundary_hit is not None
    assert boundary_hit.is_stale is False

    # Past TTL (now > expires_at) -> weather allows bounded stale when explicitly requested
    fake_now = 1075.0
    stale_hit = cache.get(key_w, allow_stale=True, max_stale_seconds=30.0)
    assert stale_hit is not None
    assert stale_hit.is_stale is True
    assert "STALE cached result" in stale_hit.freshness_note()

    assert cache.get(key_w, allow_stale=False) is None
    assert len(cache) == 0


def test_ttl_cache_prohibits_stale_market_data_and_sensitive_keys() -> None:
    """Ensure MARKET_QUOTE and EXCHANGE_RATE never return stale data and sensitive keys fail."""
    fake_now = 5000.0
    cache: PerClassTTLCache[dict[str, Any]] = PerClassTTLCache(
        ttl_overrides={
            CacheDataClass.MARKET_QUOTE: 30.0,
            CacheDataClass.EXCHANGE_RATE: 60.0,
        },
        clock=lambda: fake_now,
    )
    mk_key = build_cache_key(CacheDataClass.MARKET_QUOTE, "yfinance", "AAPL")
    fx_key = build_cache_key(CacheDataClass.EXCHANGE_RATE, "yfinance", "USD/EGP")
    cache.put(mk_key, CacheDataClass.MARKET_QUOTE, "yfinance", {"price": 225.0})
    cache.put(fx_key, CacheDataClass.EXCHANGE_RATE, "yfinance", {"rate": 49.2})

    # Advance past TTL
    fake_now = 5065.0
    # Even with allow_stale=True, financial data MUST NOT be served stale
    assert cache.get(mk_key, allow_stale=True, max_stale_seconds=3600.0) is None
    assert cache.get(fx_key, allow_stale=True, max_stale_seconds=3600.0) is None

    # Sensitive parameter keys must be rejected by default
    for sensitive_param in ("api_key", "access_token", "password", "Authorization", "session_id"):
        with pytest.raises(ValueError, match="sensitive parameter"):
            build_cache_key(
                CacheDataClass.WEB_SEARCH,
                "tavily",
                "query",
                params={sensitive_param: "secret-val"},
            )


def test_ttl_cache_per_class_limits_key_isolation_and_thread_safety() -> None:
    """Verify per-class capacity limits, provider/query key isolation, and thread safety."""
    # Provider & query key isolation
    k_tavily = build_cache_key(CacheDataClass.WEB_SEARCH, "tavily", "python")
    k_ddg = build_cache_key(CacheDataClass.WEB_SEARCH, "duckduckgo", "python")
    k_news = build_cache_key(CacheDataClass.NEWS_SEARCH, "tavily", "python")
    assert len({k_tavily, k_ddg, k_news}) == 3

    fake_now = 100.0
    cache: PerClassTTLCache[int] = PerClassTTLCache(
        max_entries=10,
        max_entries_per_class=2,
        clock=lambda: fake_now,
    )
    cache.put("w1", CacheDataClass.WEATHER, "wttr", 1)
    cache.put("w2", CacheDataClass.WEATHER, "wttr", 2)
    cache.put("w3", CacheDataClass.WEATHER, "wttr", 3)
    # Per-class limit of 2 evicts oldest WEATHER entry ("w1") even though global max_entries is 10
    assert cache.count_by_class(CacheDataClass.WEATHER) == 2
    assert cache.get("w1") is None
    assert cache.get("w2") is not None
    assert cache.get("w3") is not None

    # Concurrent multi-threaded reads and writes
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        try:
            for i in range(40):
                key = build_cache_key(CacheDataClass.WEB_SEARCH, f"prov_{tid}", f"q_{i}")
                cache.put(key, CacheDataClass.WEB_SEARCH, f"prov_{tid}", i)
                _ = cache.get(key)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert cache.count_by_class(CacheDataClass.WEB_SEARCH) <= 2


def test_http_redirects_errors_and_default_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(
        host: str,
        port: int,
        type: int = 0,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        if host == "fail.example":
            raise socket.gaierror("NXDOMAIN")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34%eth0", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert default_dns_resolver("ok.example", 443) == ["93.184.216.34"]
    with pytest.raises(UltimateMCPError) as exc_dns:
        default_dns_resolver("fail.example", 443)
    assert exc_dns.value.code == ErrorCode.PROVIDER_UNAVAILABLE

    def pub_resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    def redirect_sender(
        url: str,
        headers: dict[str, str],
        connect_timeout: float,
        read_timeout: float,
        max_bytes: int,
    ) -> RawHTTPResponse:
        if url.endswith("/hop1"):
            return RawHTTPResponse(
                301,
                {"location": "/hop2"},
                b"",
                url,
                peer_ip="93.184.216.34",
            )
        return RawHTTPResponse(
            200,
            {"content-type": "text/plain; charset=latin-1"},
            b"final body",
            url,
            peer_ip="93.184.216.34",
        )

    resp = fetch_safe_url(
        "https://example.com/hop1",
        resolver=pub_resolver,
        sender=redirect_sender,
    )
    assert resp.final_url == "https://example.com/hop2"
    assert resp.redirect_count == 1
    assert resp.text == "final body"

    def loop_sender(
        url: str,
        headers: dict[str, str],
        connect_timeout: float,
        read_timeout: float,
        max_bytes: int,
    ) -> RawHTTPResponse:
        return RawHTTPResponse(
            302,
            {"location": "https://example.com/loop"},
            b"",
            url,
            peer_ip="93.184.216.34",
        )

    with pytest.raises(UltimateMCPError) as exc_loop:
        fetch_safe_url("https://example.com/loop", resolver=pub_resolver, sender=loop_sender)
    assert exc_loop.value.code == ErrorCode.SSRF_BLOCKED

    def rate_limit_sender(
        url: str,
        headers: dict[str, str],
        connect_timeout: float,
        read_timeout: float,
        max_bytes: int,
    ) -> RawHTTPResponse:
        return RawHTTPResponse(
            429,
            {"retry-after": "30"},
            b"slow down",
            url,
            peer_ip="93.184.216.34",
        )

    with pytest.raises(UltimateMCPError) as exc_429:
        fetch_safe_url("https://example.com/api", resolver=pub_resolver, sender=rate_limit_sender)
    assert exc_429.value.code == ErrorCode.RATE_LIMITED
    assert exc_429.value.details["retry_after"] == "30"


def test_send_pinned_http_request_Rejects_empty_or_private_pinned_ips() -> None:
    with pytest.raises(UltimateMCPError) as exc_empty:
        send_pinned_http_request(
            "http://example.com/",
            {},
            5.0,
            5.0,
            1024,
            pinned_ips=(),
            public_ip_checker=is_public_ip_address,
        )
    assert exc_empty.value.code == ErrorCode.SSRF_BLOCKED

    with pytest.raises(UltimateMCPError) as exc_priv:
        send_pinned_http_request(
            "http://example.com/",
            {},
            5.0,
            5.0,
            1024,
            pinned_ips=("127.0.0.1",),
            public_ip_checker=is_public_ip_address,
        )
    assert exc_priv.value.code == ErrorCode.SSRF_BLOCKED
