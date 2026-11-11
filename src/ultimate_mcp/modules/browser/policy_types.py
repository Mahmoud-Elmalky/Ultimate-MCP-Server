"""Policy data structures and constants for Playwright browser egress control."""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field

STRICT_CHROMIUM_ARGS: tuple[str, ...] = (
    "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--no-pings",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
)

BLOCKED_RESOURCE_TYPES: frozenset[str] = frozenset(
    {"websocket", "ping", "cspviolationreport"}
)

BLOCKED_BACKGROUND_HOSTS: frozenset[str] = frozenset(
    {
        "clients2.google.com",
        "update.googleapis.com",
        "safebrowsing.googleapis.com",
        "optimizationguide-pa.googleapis.com",
    }
)

BROWSER_ALLOWED_CONTENT_TYPES: tuple[str, ...] = (
    "text/html",
    "text/plain",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/x-javascript",
    "application/xhtml+xml",
    "application/xml",
    "text/xml",
    "application/json",
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/svg+xml",
    "image/x-icon",
    "image/vnd.microsoft.icon",
    "font/woff",
    "font/woff2",
    "font/ttf",
    "application/font-woff",
)

HOP_BY_HOP_HEADERS: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-encoding",
        "content-length",
        "set-cookie",
    }
)

WINDOWS_ISOLATION_LIMITATION = (
    "On Windows without Admin WFP drivers, OS-level network namespaces (CLONE_NEWNET) are "
    "unavailable; browser isolation relies on Chromium --host-resolver-rules=MAP * ~NOTFOUND, "
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp, service_workers='block', "
    "and context.route fulfillment exclusively via pinned-IP fetch_safe_url."
)


@dataclass(frozen=True)
class ApprovedMethodRule:
    """Explicit authorization for a non-GET HTTP method against an exact origin and path."""

    method: str
    origin: str
    path_prefix: str = "/"
    max_body_bytes: int = 65_536

    def matches(self, method: str, url: str, body_len: int) -> bool:
        """Return True only if method, origin, path prefix, and body size match this rule."""
        if method.upper() != self.method.upper() or body_len > self.max_body_bytes:
            return False
        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").strip().rstrip(".").lower()
        port = parsed.port or (443 if scheme == "https" else 80)
        default_port = 443 if scheme == "https" else 80
        req_origin = (
            f"{scheme}://{host}" if port == default_port else f"{scheme}://{host}:{port}"
        )
        path = parsed.path or "/"
        return req_origin == self.origin.rstrip("/").lower() and path.startswith(
            self.path_prefix
        )


@dataclass(frozen=True)
class EgressDecision:
    """Result of evaluating and executing an intercepted browser network request."""

    allowed: bool
    action: str  # "fulfill" | "abort"
    reason: str
    status_code: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    resolved_ips: tuple[str, ...] = ()
    redirect_count: int = 0


@dataclass(frozen=True)
class EgressAuditEvent:
    """Audit trail entry recorded for every intercepted browser request."""

    url: str
    method: str
    resource_type: str
    action: str
    reason: str
    resolved_ips: tuple[str, ...] = ()
