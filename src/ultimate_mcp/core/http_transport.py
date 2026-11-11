"""Pinned-IP HTTP/HTTPS transport for SSRF and DNS-rebinding prevention.

Ensures that:
1. No second unvalidated DNS lookup occurs after ``validate_and_resolve_url``.
2. The TCP socket connects directly to a validated ``pinned_ip`` from the approved IP set.
3. The connected socket's actual peer IP (``sock.getpeername()[0]``) is verified against the
   approved public IP set BEFORE TLS handshake or HTTP request transmission.
4. TLS SNI and certificate hostname verification use the original ``expected_host``.
5. Environment proxy variables (``HTTP_PROXY``, ``HTTPS_PROXY``, ``ALL_PROXY``) are ignored
   and every request uses an isolated connection (``Connection: close``, no pool reuse).
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.parse
import zlib
from collections.abc import Callable
from dataclasses import dataclass

from ultimate_mcp.core.models import ErrorCode, UltimateMCPError

SocketFactory = Callable[[tuple[str, int], float], socket.socket]
SSLWrapper = Callable[[socket.socket, str], socket.socket]


@dataclass(frozen=True)
class RawHTTPResponse:
    """Low-level HTTP response returned by a single non-redirecting request."""

    status_code: int
    headers: dict[str, str]
    body: bytes
    url: str
    peer_ip: str | None = None


def normalize_socket_ip(raw_ip: str) -> str:
    """Strip IPv6 zone/scope suffix and normalize IP string representation."""
    cleaned = raw_ip.strip()
    if "%" in cleaned:
        cleaned = cleaned.split("%", 1)[0]
    try:
        addr = ipaddress.ip_address(cleaned)
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(addr)
    except ValueError:
        return cleaned


def default_socket_factory(address: tuple[str, int], timeout: float) -> socket.socket:
    """Open a direct TCP socket to an IP literal without consulting proxy env vars."""
    ip_str, port = address
    addr_obj = ipaddress.ip_address(ip_str)
    family = socket.AF_INET6 if isinstance(addr_obj, ipaddress.IPv6Address) else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip_str, port))
    except Exception:
        sock.close()
        raise
    return sock


def default_ssl_wrapper(sock: socket.socket, server_hostname: str) -> socket.socket:
    """Wrap a connected TCP socket with TLS SNI and strict certificate verification."""
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context.wrap_socket(sock, server_hostname=server_hostname)


def verify_connected_peer_ip(
    sock: socket.socket,
    *,
    allowed_ips: frozenset[str],
    public_ip_checker: Callable[[str], bool],
) -> str:
    """Verify the connected socket's peer IP before transmitting any TLS/HTTP bytes."""
    try:
        peer_info = sock.getpeername()
        peer_ip = normalize_socket_ip(str(peer_info[0]))
    except Exception as exc:
        sock.close()
        raise UltimateMCPError(
            ErrorCode.SSRF_BLOCKED,
            f"Unable to verify connected socket peer IP: {exc}",
        ) from exc

    if not public_ip_checker(peer_ip) or peer_ip not in allowed_ips:
        sock.close()
        raise UltimateMCPError(
            ErrorCode.SSRF_BLOCKED,
            f"Connected socket peer IP '{peer_ip}' is not in the approved public IP set "
            f"{sorted(allowed_ips)}.",
        )
    return peer_ip


def _decompress_bounded(
    raw_bytes: bytes,
    content_encoding: str,
    max_response_bytes: int,
) -> bytes:
    """Safely decompress gzip/deflate payloads while enforcing ``max_response_bytes``."""
    enc = content_encoding.strip().lower()
    if enc not in ("gzip", "x-gzip", "deflate"):
        return raw_bytes

    wbits = (16 + zlib.MAX_WBITS) if enc in ("gzip", "x-gzip") else -zlib.MAX_WBITS
    decompressor = zlib.decompressobj(wbits)
    try:
        data = decompressor.decompress(raw_bytes, max_response_bytes + 1)
        if not decompressor.unconsumed_tail:
            data += decompressor.flush(max_response_bytes + 1 - len(data))
    except zlib.error as exc:
        if enc == "deflate":
            try:
                decomp2 = zlib.decompressobj(zlib.MAX_WBITS)
                data = decomp2.decompress(raw_bytes, max_response_bytes + 1)
            except zlib.error as inner_exc:
                raise UltimateMCPError(
                    ErrorCode.UPSTREAM_INVALID_RESPONSE,
                    f"Failed to decompress response stream: {inner_exc}",
                ) from inner_exc
        else:
            raise UltimateMCPError(
                ErrorCode.UPSTREAM_INVALID_RESPONSE,
                f"Failed to decompress response stream: {exc}",
            ) from exc

    if len(data) > max_response_bytes or decompressor.unconsumed_tail:
        raise UltimateMCPError(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"Decompressed response body exceeded limit ({max_response_bytes} bytes).",
        )
    return data


def _read_bounded_response(
    resp: http.client.HTTPResponse,
    max_response_bytes: int,
) -> tuple[dict[str, str], bytes]:
    """Read HTTP response headers and body stream up to ``max_response_bytes``."""
    resp_headers = {k.lower(): v for k, v in resp.getheaders()}
    cl_header = resp_headers.get("content-length")
    if cl_header and cl_header.isdigit() and int(cl_header) > max_response_bytes:
        raise UltimateMCPError(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"Response Content-Length ({cl_header} bytes) exceeds limit "
            f"({max_response_bytes} bytes).",
        )

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(16384)
        if not chunk:
            break
        total += len(chunk)
        if total > max_response_bytes:
            raise UltimateMCPError(
                ErrorCode.RESOURCE_LIMIT_EXCEEDED,
                f"Response stream exceeded maximum allowed size ({max_response_bytes} bytes).",
            )
        chunks.append(chunk)

    raw_body = b"".join(chunks)
    encoding = resp_headers.get("content-encoding", "")
    body = _decompress_bounded(raw_body, encoding, max_response_bytes) if encoding else raw_body
    return resp_headers, body


def send_pinned_http_request(
    url: str,
    headers: dict[str, str],
    connect_timeout: float,
    read_timeout: float,
    max_response_bytes: int,
    *,
    pinned_ips: tuple[str, ...],
    public_ip_checker: Callable[[str], bool],
    socket_factory: SocketFactory = default_socket_factory,
    ssl_wrapper: SSLWrapper = default_ssl_wrapper,
) -> RawHTTPResponse:
    """Execute a single non-redirecting HTTP(S) GET pinned to validated IP addresses."""
    if not pinned_ips:
        raise UltimateMCPError(
            ErrorCode.SSRF_BLOCKED,
            "Cannot open outbound connection without at least one validated pinned IP.",
        )
    allowed_set = frozenset(normalize_socket_ip(ip) for ip in pinned_ips)
    for ip_str in allowed_set:
        if not public_ip_checker(ip_str):
            raise UltimateMCPError(
                ErrorCode.SSRF_BLOCKED,
                f"Pinned IP '{ip_str}' failed public IP verification prior to socket connect.",
            )

    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    expected_host = (parsed.hostname or "").strip().rstrip(".")
    effective_port = parsed.port or (443 if scheme == "https" else 80)
    target_path = parsed.path or "/"
    if parsed.query:
        target_path = f"{target_path}?{parsed.query}"

    host_header = expected_host
    if ":" in expected_host and not expected_host.startswith("["):
        host_header = f"[{expected_host}]"
    if (scheme == "http" and effective_port != 80) or (scheme == "https" and effective_port != 443):
        host_header = f"{host_header}:{effective_port}"

    pinned_ip = normalize_socket_ip(pinned_ips[0])
    sock: socket.socket | None = None
    conn: http.client.HTTPConnection | None = None
    try:
        sock = socket_factory((pinned_ip, effective_port), connect_timeout)
        peer_ip = verify_connected_peer_ip(
            sock,
            allowed_ips=allowed_set,
            public_ip_checker=public_ip_checker,
        )
        sock.settimeout(read_timeout)
        if scheme == "https":
            sock = ssl_wrapper(sock, expected_host)
            sock.settimeout(read_timeout)

        conn = http.client.HTTPConnection(expected_host, effective_port, timeout=read_timeout)
        conn.sock = sock
        req_headers = {
            **headers,
            "Host": host_header,
            "Connection": "close",
            "Accept-Encoding": "gzip, deflate",
        }
        conn.request("GET", target_path, headers=req_headers)
        resp = conn.getresponse()
        resp_headers, body = _read_bounded_response(resp, max_response_bytes)
        return RawHTTPResponse(
            status_code=resp.status,
            headers=resp_headers,
            body=body,
            url=url,
            peer_ip=peer_ip,
        )
    except UltimateMCPError:
        raise
    except TimeoutError as exc:
        raise UltimateMCPError(
            ErrorCode.TIMEOUT,
            f"HTTP request timed out: {exc}",
            retryable=True,
        ) from exc
    except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            f"HTTP transport error: {exc}",
            retryable=True,
        ) from exc
    finally:
        if conn is not None:
            conn.close()
        elif sock is not None:
            sock.close()
