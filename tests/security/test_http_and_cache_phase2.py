"""Security and unit tests for Slice 2.1: SSRF-hardened HTTP client and per-class TTL cache."""

from __future__ import annotations

import pytest

from ultimate_mcp.core.cache import CacheDataClass, PerClassTTLCache, build_cache_key
from ultimate_mcp.core.http import (
    RawHTTPResponse,
    fetch_safe_url,
    is_public_ip_address,
    validate_and_resolve_url,
)
from ultimate_mcp.core.models import ErrorCode, UltimateMCPError


@pytest.mark.parametrize(
    "public_ip",
    [
        "93.184.216.34",
        "8.8.8.8",
        "1.1.1.1",
        "2606:2800:220:1:248:1893:25c8:1946",
        "2001:4860:4860::8888",
    ],
)
def test_is_public_ip_address_accepts_global_ips(public_ip: str) -> None:
    assert is_public_ip_address(public_ip) is True


@pytest.mark.parametrize(
    "restricted_ip",
    [
        "127.0.0.1",
        "127.0.1.1",
        "0.0.0.0",
        "10.0.0.1",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",  # AWS/GCP/Azure metadata
        "100.64.0.1",  # Carrier-grade NAT
        "198.18.0.1",  # Benchmarking
        "192.0.2.1",  # TEST-NET-1
        "224.0.0.1",  # Multicast
        "240.0.0.1",  # Reserved
        "255.255.255.255",
        "::1",
        "::",
        "fc00::1",
        "fd12:3456:789a::1",
        "fe80::1",
        "ff02::1",
        "2001:db8::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:169.254.169.254",  # IPv4-mapped metadata
        "::ffff:10.0.0.5",  # IPv4-mapped private
        "not-an-ip",
    ],
)
def test_is_public_ip_address_rejects_restricted_ips(restricted_ip: str) -> None:
    assert is_public_ip_address(restricted_ip) is False


@pytest.mark.parametrize(
    ("bad_url", "expected_code"),
    [
        ("file:///etc/passwd", ErrorCode.SSRF_BLOCKED),
        ("ftp://example.com/file.txt", ErrorCode.SSRF_BLOCKED),
        ("gopher://127.0.0.1:70/_", ErrorCode.SSRF_BLOCKED),
        ("javascript:alert(1)", ErrorCode.SSRF_BLOCKED),
        ("https://user:secret@example.com/path", ErrorCode.SSRF_BLOCKED),
        ("http://localhost/admin", ErrorCode.SSRF_BLOCKED),
        ("http://sub.localhost/admin", ErrorCode.SSRF_BLOCKED),
        ("http://printer.local/status", ErrorCode.SSRF_BLOCKED),
        ("http://metadata.google.internal/computeMetadata/v1/", ErrorCode.SSRF_BLOCKED),
        ("http://127.0.0.1/secret", ErrorCode.SSRF_BLOCKED),
        ("http://169.254.169.254/latest/meta-data/", ErrorCode.SSRF_BLOCKED),
        ("http://[::1]/secret", ErrorCode.SSRF_BLOCKED),
        ("http://[::ffff:169.254.169.254]/latest", ErrorCode.SSRF_BLOCKED),
        ("https://example.com:8080/internal", ErrorCode.SSRF_BLOCKED),
        ("", ErrorCode.INVALID_INPUT),
        ("https://example.com/bad\nheader", ErrorCode.INVALID_INPUT),
        ("https:///no-host", ErrorCode.INVALID_INPUT),
    ],
)
def test_validate_and_resolve_url_blocks_ssrf_vectors(
    bad_url: str, expected_code: ErrorCode
) -> None:
    def fake_resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    with pytest.raises(UltimateMCPError) as excinfo:
        validate_and_resolve_url(bad_url, resolver=fake_resolver)
    assert excinfo.value.code == expected_code


def test_validate_url_blocks_hostname_resolving_to_private_ip() -> None:
    def evil_resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "10.0.0.42"]

    with pytest.raises(UltimateMCPError) as excinfo:
        validate_and_resolve_url("https://evil.example.com/page", resolver=evil_resolver)
    assert excinfo.value.code == ErrorCode.SSRF_BLOCKED


def test_fetch_safe_url_follows_valid_redirect_and_blocks_redirect_to_private_ip() -> None:
    dns_map = {
        "public.example.com": ["93.184.216.34"],
        "cdn.example.com": ["93.184.216.35"],
        "internal.example.com": ["169.254.169.254"],
    }

    def fake_resolver(host: str, port: int) -> list[str]:
        return dns_map[host]

    def sender_ok(
        url: str, headers: dict[str, str], ct: float, rt: float, max_b: int
    ) -> RawHTTPResponse:
        if url == "https://public.example.com/start":
            return RawHTTPResponse(
                status_code=302,
                headers={"location": "https://cdn.example.com/final"},
                body=b"",
                url=url,
            )
        return RawHTTPResponse(
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            body=b"<html><p>Hello world</p></html>",
            url=url,
        )

    res = fetch_safe_url(
        "https://public.example.com/start",
        resolver=fake_resolver,
        sender=sender_ok,
    )
    assert res.final_url == "https://cdn.example.com/final"
    assert res.redirect_count == 1
    assert "Hello world" in res.text

    # Now test redirect to metadata IP (169.254.169.254) -> must be blocked before fetching hop 2
    calls: list[str] = []

    def sender_redirect_to_metadata(
        url: str, headers: dict[str, str], ct: float, rt: float, max_b: int
    ) -> RawHTTPResponse:
        calls.append(url)
        return RawHTTPResponse(
            status_code=301,
            headers={"location": "http://internal.example.com/latest/meta-data"},
            body=b"",
            url=url,
        )

    with pytest.raises(UltimateMCPError) as excinfo:
        fetch_safe_url(
            "https://public.example.com/start",
            resolver=fake_resolver,
            sender=sender_redirect_to_metadata,
        )
    assert excinfo.value.code == ErrorCode.SSRF_BLOCKED
    assert calls == ["https://public.example.com/start"]


def test_fetch_safe_url_detects_dns_rebinding_attack() -> None:
    """If DNS flips from a public IP to 127.0.0.1 during/after connection, reject."""
    resolutions = [["93.184.216.34"], ["127.0.0.1"]]

    def rebinding_resolver(host: str, port: int) -> list[str]:
        return resolutions.pop(0)

    def fake_sender(
        url: str, headers: dict[str, str], ct: float, rt: float, max_b: int
    ) -> RawHTTPResponse:
        return RawHTTPResponse(
            status_code=200,
            headers={"content-type": "text/plain"},
            body=b"stolen local data",
            url=url,
        )

    with pytest.raises(UltimateMCPError) as excinfo:
        fetch_safe_url(
            "https://rebind.example.com/data",
            resolver=rebinding_resolver,
            sender=fake_sender,
        )
    assert excinfo.value.code == ErrorCode.SSRF_BLOCKED
    assert "rebinding" in excinfo.value.message.lower()


def test_fetch_safe_url_enforces_max_bytes_content_type_and_status_codes() -> None:
    def resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    def oversized_sender(
        url: str, headers: dict[str, str], ct: float, rt: float, max_b: int
    ) -> RawHTTPResponse:
        return RawHTTPResponse(
            status_code=200,
            headers={"content-type": "text/plain"},
            body=b"x" * 2000,
            url=url,
        )

    with pytest.raises(UltimateMCPError) as excinfo:
        fetch_safe_url(
            "https://example.com/big",
            max_response_bytes=500,
            resolver=resolver,
            sender=oversized_sender,
        )
    assert excinfo.value.code == ErrorCode.RESOURCE_LIMIT_EXCEEDED

    def binary_sender(
        url: str, headers: dict[str, str], ct: float, rt: float, max_b: int
    ) -> RawHTTPResponse:
        return RawHTTPResponse(
            status_code=200,
            headers={"content-type": "application/x-msdownload"},
            body=b"MZ...",
            url=url,
        )

    with pytest.raises(UltimateMCPError) as excinfo:
        fetch_safe_url(
            "https://example.com/mal.exe",
            resolver=resolver,
            sender=binary_sender,
        )
    assert excinfo.value.code == ErrorCode.UNSUPPORTED_FORMAT

    def rate_limited_sender(
        url: str, headers: dict[str, str], ct: float, rt: float, max_b: int
    ) -> RawHTTPResponse:
        return RawHTTPResponse(
            status_code=429,
            headers={"retry-after": "30"},
            body=b"slow down",
            url=url,
        )

    with pytest.raises(UltimateMCPError) as excinfo:
        fetch_safe_url(
            "https://example.com/api",
            resolver=resolver,
            sender=rate_limited_sender,
        )
    assert excinfo.value.code == ErrorCode.RATE_LIMITED
    assert excinfo.value.retryable is True
    assert excinfo.value.details == {
        "status_code": 429,
        "url": "https://example.com/api",
        "retry_after": "30",
    }


def test_per_class_ttl_cache_freshness_expiry_and_lru() -> None:
    now = [1_000.0]
    cache: PerClassTTLCache[dict[str, str]] = PerClassTTLCache(
        max_entries=2,
        clock=lambda: now[0],
    )
    k1 = build_cache_key(CacheDataClass.MARKET_QUOTE, "yfinance", "AAPL")
    k2 = build_cache_key(CacheDataClass.WEATHER, "wttr", "Cairo")
    k3 = build_cache_key(CacheDataClass.WEB_SEARCH, "tavily", "python")

    cache.put(k1, CacheDataClass.MARKET_QUOTE, "yfinance", {"price": "200"})
    hit = cache.get(k1)
    assert hit is not None
    assert hit.is_stale is False
    assert hit.ttl_seconds == 60.0
    assert "Cached result from yfinance" in hit.freshness_note()

    # Advance past market quote 60s TTL, within stale window
    now[0] += 75.0
    assert cache.get(k1, allow_stale=False) is None

    # Re-insert and check allow_stale=True
    cache.put(k1, CacheDataClass.MARKET_QUOTE, "yfinance", {"price": "201"})
    now[0] += 90.0
    stale_hit = cache.get(k1, allow_stale=True, max_stale_seconds=120.0)
    assert stale_hit is not None
    assert stale_hit.is_stale is True
    assert "STALE cached result" in stale_hit.freshness_note()

    # Test LRU eviction when max_entries=2
    cache.clear()
    cache.put(k1, CacheDataClass.MARKET_QUOTE, "yfinance", {"price": "1"})
    cache.put(k2, CacheDataClass.WEATHER, "wttr", {"temp": "25"})
    cache.put(k3, CacheDataClass.WEB_SEARCH, "tavily", {"q": "py"})
    assert len(cache) == 2
    assert cache.get(k1) is None
    assert cache.get(k2) is not None
    assert cache.get(k3) is not None
