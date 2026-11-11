"""SSRF-hardened outbound HTTP fetching layer (``docs/SECURITY_MODEL.md`` §5).

Security invariants:
1. Permits ONLY ``http`` and ``https`` schemes; rejects empty/malformed URLs, userinfo
   credentials (`user:pass@host`), and non-standard ports (unless explicitly allowlisted).
2. Rejects localhost aliases (`localhost`, `*.localhost`, `*.local`), IP literals, and DNS
   resolutions that point to any loopback, private, link-local, multicast, unspecified,
   reserved, carrier-grade NAT (`100.64.0.0/10`), benchmarking, or cloud metadata address
   (both IPv4 and IPv6, including IPv4-mapped IPv6 addresses like `::ffff:127.0.0.1` and
   `::ffff:169.254.169.254`).
3. Disables automatic redirects (`follow_redirects=False`); manually follows up to
   ``max_redirects`` hops while re-validating the target URL and re-resolving DNS at every
   hop.
4. Defends against DNS rebinding (TOCTOU between pre-flight DNS resolution and socket
   connection) by re-resolving the hostname immediately after the connection completes and
   verifying that all resolved addresses remain public and overlap the pre-validated IP set.
5. Enforces connect, read, and total timeouts, content-type allowlists, and streaming
   response byte caps (`max_response_bytes`) so oversized responses are aborted before
   buffering in memory.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ultimate_mcp.core.models import ErrorCode, UltimateMCPError

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
DEFAULT_ALLOWED_PORTS: frozenset[int] = frozenset({80, 443})
DEFAULT_MAX_REDIRECTS = 4
DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MiB
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 10.0

DEFAULT_ALLOWED_CONTENT_TYPES: tuple[str, ...] = (
    "text/html",
    "text/plain",
    "application/xhtml+xml",
    "application/xml",
    "text/xml",
    "application/rss+xml",
    "application/atom+xml",
    "application/json",
)

FORBIDDEN_HOSTNAMES: frozenset[str] = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
)

FORBIDDEN_HOST_SUFFIXES: tuple[str, ...] = (
    ".localhost",
    ".local",
    ".internal",
    ".home.arpa",
)

CARRIER_GRADE_NAT_NET = ipaddress.IPv4Network("100.64.0.0/10")
BENCHMARK_NET = ipaddress.IPv4Network("198.18.0.0/15")
IPV6_DISCARD_NET = ipaddress.IPv6Network("100::/64")
IPV6_DOC_NET = ipaddress.IPv6Network("2001:db8::/32")

ResolverFunc = Callable[[str, int], list[str]]
TransportSender = Callable[
    [str, dict[str, str], float, float, int],
    "RawHTTPResponse",
]


@dataclass(frozen=True)
class RawHTTPResponse:
    """Low-level HTTP response returned by a single non-redirecting request."""

    status_code: int
    headers: dict[str, str]
    body: bytes
    url: str


@dataclass(frozen=True)
class SafeHttpResponse:
    """Validated, bounded response returned by ``fetch_safe_url``."""

    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    headers: dict[str, str]
    body: bytes
    text: str
    redirect_count: int
    resolved_ips: tuple[str, ...]
    retrieved_at: str


def is_public_ip_address(ip_str: str) -> bool:
    """Return True only if ``ip_str`` is a globally routable public IPv4 or IPv6 address."""
    try:
        addr = ipaddress.ip_address(ip_str.strip())
    except ValueError:
        return False

    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return is_public_ip_address(str(addr.ipv4_mapped))

    if (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
    ):
        return False

    if isinstance(addr, ipaddress.IPv4Address):
        if addr in CARRIER_GRADE_NAT_NET or addr in BENCHMARK_NET:
            return False
        if addr == ipaddress.IPv4Address("255.255.255.255"):
            return False
        if not addr.is_global:
            return False
    else:
        if addr.is_site_local or addr in IPV6_DISCARD_NET or addr in IPV6_DOC_NET:
            return False
        if not addr.is_global:
            return False

    return True


def default_dns_resolver(host: str, port: int) -> list[str]:
    """Resolve ``host`` via system ``socket.getaddrinfo`` into unique IP strings."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            f"DNS resolution failed for host '{host}': {exc}",
            retryable=True,
        ) from exc

    ips: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip_str = str(sockaddr[0])
        if "%" in ip_str:
            ip_str = ip_str.split("%", 1)[0]
        if ip_str not in ips:
            ips.append(ip_str)
    if not ips:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            f"DNS resolution returned no addresses for host '{host}'.",
        )
    return ips


def validate_and_resolve_url(
    url: str,
    *,
    allowed_ports: frozenset[int] = DEFAULT_ALLOWED_PORTS,
    resolver: ResolverFunc = default_dns_resolver,
) -> tuple[urllib.parse.ParseResult, tuple[str, ...]]:
    """Validate URL syntax, scheme, credentials, port, hostname, and resolved IP addresses."""
    cleaned = url.strip()
    if not cleaned or len(cleaned) > 4096:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "URL must be between 1 and 4096 chars.")
    if any(ch in cleaned for ch in ("\r", "\n", "\x00", " ")):
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            "URL contains illegal whitespace or control characters.",
        )

    parsed = urllib.parse.urlsplit(cleaned)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UltimateMCPError(
            ErrorCode.SSRF_BLOCKED,
            f"Disallowed URL scheme '{parsed.scheme}'. Only http and https are permitted.",
        )

    if parsed.username is not None or parsed.password is not None:
        raise UltimateMCPError(
            ErrorCode.SSRF_BLOCKED,
            "URLs containing embedded userinfo credentials are not permitted.",
        )

    hostname = parsed.hostname
    if not hostname:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "URL is missing a valid hostname.")

    host_lower = hostname.strip().rstrip(".").lower()
    if not host_lower:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "URL hostname is empty.")

    if host_lower in FORBIDDEN_HOSTNAMES or host_lower.endswith(FORBIDDEN_HOST_SUFFIXES):
        raise UltimateMCPError(
            ErrorCode.SSRF_BLOCKED,
            f"Access to internal or localhost hostname '{host_lower}' is blocked.",
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, f"Invalid URL port: {exc}") from exc

    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    if effective_port not in allowed_ports:
        raise UltimateMCPError(
            ErrorCode.SSRF_BLOCKED,
            f"Port {effective_port} is not in the allowed outbound port list.",
        )

    # Check if hostname is already an IP literal
    try:
        ip_obj = ipaddress.ip_address(host_lower)
    except ValueError:
        ip_obj = None

    if ip_obj is not None:
        literal_ip = str(ip_obj)
        if not is_public_ip_address(literal_ip):
            raise UltimateMCPError(
                ErrorCode.SSRF_BLOCKED,
                f"Target IP literal '{literal_ip}' is in a restricted/private range.",
            )
        resolved_ips = [literal_ip]
    else:
        resolved_ips = resolver(host_lower, effective_port)
        if not resolved_ips:
            raise UltimateMCPError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"No DNS records resolved for '{host_lower}'.",
            )
        for ip_str in resolved_ips:
            if not is_public_ip_address(ip_str):
                raise UltimateMCPError(
                    ErrorCode.SSRF_BLOCKED,
                    f"Host '{host_lower}' resolved to restricted address '{ip_str}'.",
                )

    normalized = urllib.parse.ParseResult(
        scheme=scheme,
        netloc=parsed.netloc,
        path=parsed.path or "/",
        params="",
        query=parsed.query,
        fragment="",
    )
    return normalized, tuple(resolved_ips)


def _extract_content_type(headers: dict[str, str]) -> str:
    for k, v in headers.items():
        if k.lower() == "content-type":
            return v.split(";", 1)[0].strip().lower()
    return ""


def _extract_charset(headers: dict[str, str]) -> str:
    for k, v in headers.items():
        if k.lower() == "content-type" and "charset=" in v.lower():
            part = v.lower().split("charset=", 1)[1].split(";", 1)[0].strip().strip("'\"")
            if part:
                return part
    return "utf-8"


def _default_httpx_sender(
    url: str,
    headers: dict[str, str],
    connect_timeout: float,
    read_timeout: float,
    max_response_bytes: int,
) -> RawHTTPResponse:
    """Perform a single non-redirecting streaming HTTP GET via ``httpx``."""
    try:
        import importlib

        httpx: Any = importlib.import_module("httpx")
    except ImportError as exc:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Optional package 'httpx' is required for web fetching (install `[research]`).",
        ) from exc

    timeout = httpx.Timeout(
        timeout=connect_timeout + read_timeout,
        connect=connect_timeout,
        read=read_timeout,
    )
    try:
        with (
            httpx.Client(
                follow_redirects=False,
                timeout=timeout,
                trust_env=False,
            ) as client,
            client.stream("GET", url, headers=headers) as response,
        ):
            resp_headers = {k.lower(): v for k, v in response.headers.items()}
            cl_header = resp_headers.get("content-length")
            if cl_header and cl_header.isdigit() and int(cl_header) > max_response_bytes:
                raise UltimateMCPError(
                    ErrorCode.RESOURCE_LIMIT_EXCEEDED,
                    f"Response Content-Length ({cl_header} bytes) exceeds limit "
                    f"({max_response_bytes} bytes).",
                )

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes(chunk_size=16384):
                total += len(chunk)
                if total > max_response_bytes:
                    raise UltimateMCPError(
                        ErrorCode.RESOURCE_LIMIT_EXCEEDED,
                        f"Response stream exceeded maximum allowed size "
                        f"({max_response_bytes} bytes).",
                    )
                chunks.append(chunk)

            return RawHTTPResponse(
                status_code=response.status_code,
                headers=resp_headers,
                body=b"".join(chunks),
                url=str(response.url),
            )
    except UltimateMCPError:
        raise
    except httpx.TimeoutException as exc:
        raise UltimateMCPError(
            ErrorCode.TIMEOUT,
            f"HTTP request timed out: {exc}",
            retryable=True,
        ) from exc
    except httpx.HTTPError as exc:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            f"HTTP transport error: {exc}",
            retryable=True,
        ) from exc


def fetch_safe_url(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    allowed_ports: frozenset[int] = DEFAULT_ALLOWED_PORTS,
    allowed_content_types: Sequence[str] | None = DEFAULT_ALLOWED_CONTENT_TYPES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    resolver: ResolverFunc = default_dns_resolver,
    sender: TransportSender | None = None,
) -> SafeHttpResponse:
    """Fetch a public HTTP(S) URL with SSRF, DNS-rebinding, redirect, and byte-budget guards."""
    if max_redirects < 0 or max_redirects > 10:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "max_redirects must be between 0 and 10.")
    if max_response_bytes <= 0:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "max_response_bytes must be > 0.")

    active_sender = sender or _default_httpx_sender
    req_headers: dict[str, str] = {
        "User-Agent": (
            "UltimateMCPServer/2.0 (+https://github.com/Mahmoud-Elmalky/Ultimate-MCP-Server)"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5"
        ),
        **(headers or {}),
    }

    current_url = url.strip()
    redirect_count = 0
    visited_urls: set[str] = set()

    while True:
        parsed, pre_ips = validate_and_resolve_url(
            current_url,
            allowed_ports=allowed_ports,
            resolver=resolver,
        )
        normalized_url = urllib.parse.urlunparse(parsed)
        if normalized_url in visited_urls:
            raise UltimateMCPError(
                ErrorCode.SSRF_BLOCKED,
                f"Redirect loop detected at '{normalized_url}'.",
            )
        visited_urls.add(normalized_url)

        raw = active_sender(
            normalized_url,
            req_headers,
            connect_timeout,
            read_timeout,
            max_response_bytes,
        )

        # Post-connection DNS rebinding check: re-resolve and ensure all returned IPs
        # remain public and overlap with pre-validated IPs.
        host_lower = (parsed.hostname or "").strip().rstrip(".").lower()
        effective_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            ip_literal = ipaddress.ip_address(host_lower)
        except ValueError:
            ip_literal = None

        if ip_literal is not None:
            post_ips = list(pre_ips)
        else:
            post_ips = resolver(host_lower, effective_port)
            for ip_str in post_ips:
                if not is_public_ip_address(ip_str):
                    raise UltimateMCPError(
                        ErrorCode.SSRF_BLOCKED,
                        f"DNS rebinding detected: '{host_lower}' resolved to restricted "
                        f"address '{ip_str}' during request.",
                    )
            if not set(pre_ips).intersection(post_ips):
                raise UltimateMCPError(
                    ErrorCode.SSRF_BLOCKED,
                    f"DNS rebinding suspected: '{host_lower}' changed IP set between "
                    "validation and response.",
                )

        if len(raw.body) > max_response_bytes:
            raise UltimateMCPError(
                ErrorCode.RESOURCE_LIMIT_EXCEEDED,
                f"Response body ({len(raw.body)} bytes) exceeds limit "
                f"({max_response_bytes} bytes).",
            )

        if raw.status_code in (301, 302, 303, 307, 308):
            if redirect_count >= max_redirects:
                raise UltimateMCPError(
                    ErrorCode.RESOURCE_LIMIT_EXCEEDED,
                    f"Exceeded maximum redirects ({max_redirects}).",
                )
            location = next(
                (v for k, v in raw.headers.items() if k.lower() == "location"),
                None,
            )
            if not location:
                raise UltimateMCPError(
                    ErrorCode.UPSTREAM_INVALID_RESPONSE,
                    f"Redirect response (HTTP {raw.status_code}) missing Location header.",
                )
            current_url = urllib.parse.urljoin(normalized_url, location.strip())
            redirect_count += 1
            continue

        if raw.status_code >= 400:
            retryable = raw.status_code in (429, 500, 502, 503, 504)
            code = (
                ErrorCode.RATE_LIMITED
                if raw.status_code == 429
                else (
                    ErrorCode.NOT_FOUND
                    if raw.status_code == 404
                    else ErrorCode.UPSTREAM_INVALID_RESPONSE
                )
            )
            details: dict[str, Any] = {
                "status_code": raw.status_code,
                "url": normalized_url,
            }
            retry_after = next(
                (v for k, v in raw.headers.items() if k.lower() == "retry-after"),
                None,
            )
            if retry_after:
                details["retry_after"] = retry_after
            raise UltimateMCPError(
                code,
                f"Upstream returned HTTP {raw.status_code} for '{normalized_url}'.",
                retryable=retryable,
                details=details,
            )

        content_type = _extract_content_type(raw.headers)
        if allowed_content_types is not None and content_type:
            allowed_norm = {ct.lower() for ct in allowed_content_types}
            if content_type not in allowed_norm:
                raise UltimateMCPError(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    f"Disallowed or unsupported Content-Type '{content_type}'.",
                    details={"content_type": content_type, "url": normalized_url},
                )

        charset = _extract_charset(raw.headers)
        try:
            decoded_text = raw.body.decode(charset, errors="replace")
        except LookupError:
            decoded_text = raw.body.decode("utf-8", errors="replace")

        return SafeHttpResponse(
            requested_url=url.strip(),
            final_url=normalized_url,
            status_code=raw.status_code,
            content_type=content_type or "application/octet-stream",
            headers={k.lower(): v for k, v in raw.headers.items()},
            body=raw.body,
            text=decoded_text,
            redirect_count=redirect_count,
            resolved_ips=tuple(post_ips),
            retrieved_at=datetime.now(UTC).isoformat(),
        )
