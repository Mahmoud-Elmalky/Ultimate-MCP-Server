"""Playwright Browser Security & Network Egress Control (Phase 03 Slice 3.1).

Security design (`phases/PHASE-03_BROWSER_DOCUMENTS.md` §A.1):
- Chromium launches with ``--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1`` and
  background-networking / WebRTC non-proxied UDP disabled so Chromium cannot bypass DNS
  validation or emit background/STUN probes.
- Contexts run with ``service_workers="block"``, ``accept_downloads=False``, and ephemeral
  storage (never attached to a personal browser profile).
- ``context.route("**/*", ...)`` intercepts ALL requests (navigation, redirects, subresources,
  fetch/XHR, WebSockets, ping) and NEVER calls ``route.continue_()`` for external URLs.
- Permitted requests are fulfilled exclusively via ``fetch_safe_url`` (pinned-IP TCP socket +
  pre-TLS peer-IP verification + per-hop redirect validation).
"""

from __future__ import annotations

import asyncio
import urllib.parse
from typing import Any

from ultimate_mcp.core.http import (
    SAFE_AUTONOMOUS_METHODS,
    ResolverFunc,
    TransportSender,
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
    BLOCKED_BACKGROUND_HOSTS,
    BLOCKED_RESOURCE_TYPES,
    BROWSER_ALLOWED_CONTENT_TYPES,
    HOP_BY_HOP_HEADERS,
    STRICT_CHROMIUM_ARGS,
    WINDOWS_ISOLATION_LIMITATION,
    ApprovedMethodRule,
    EgressAuditEvent,
    EgressDecision,
)

__all__ = [
    "BLOCKED_BACKGROUND_HOSTS",
    "BLOCKED_RESOURCE_TYPES",
    "BROWSER_ALLOWED_CONTENT_TYPES",
    "HOP_BY_HOP_HEADERS",
    "STRICT_CHROMIUM_ARGS",
    "WINDOWS_ISOLATION_LIMITATION",
    "ApprovedMethodRule",
    "BrowserEgressPolicy",
    "EgressAuditEvent",
    "EgressDecision",
]


class BrowserEgressPolicy:
    """Enforces pinned-IP network egress and isolation rules on Playwright contexts."""

    def __init__(
        self,
        *,
        approved_method_rules: tuple[ApprovedMethodRule, ...] = (),
        max_redirects: int = 4,
        max_response_bytes: int = 2 * 1024 * 1024,
        allow_downloads: bool = False,
        max_tabs: int = 5,
        resolver: ResolverFunc = default_dns_resolver,
        socket_factory: SocketFactory = default_socket_factory,
        ssl_wrapper: SSLWrapper = default_ssl_wrapper,
        sender: TransportSender | None = None,
    ) -> None:
        self.approved_method_rules = approved_method_rules
        self.max_redirects = max_redirects
        self.max_response_bytes = max_response_bytes
        self.allow_downloads = allow_downloads
        self.max_tabs = max_tabs
        self.resolver = resolver
        self.socket_factory = socket_factory
        self.ssl_wrapper = ssl_wrapper
        self.sender = sender
        self.audit_log: list[EgressAuditEvent] = []

    def build_launch_options(self, *, headless: bool = True) -> dict[str, Any]:
        """Return hardened Chromium launch options (no unvalidated DNS or background egress)."""
        return {"headless": headless, "args": list(STRICT_CHROMIUM_ARGS)}

    def build_context_options(self) -> dict[str, Any]:
        """Return isolated ephemeral BrowserContext options (no personal profile)."""
        return {
            "service_workers": "block",
            "accept_downloads": False,
            "ignore_https_errors": False,
        }

    def _record(self, event: EgressAuditEvent, decision: EgressDecision) -> EgressDecision:
        self.audit_log.append(event)
        return decision

    def _abort(self, url: str, method: str, resource_type: str, reason: str) -> EgressDecision:
        return self._record(
            EgressAuditEvent(url, method, resource_type, "abort", reason),
            EgressDecision(allowed=False, action="abort", reason=reason),
        )

    def _check_method_allowed(self, method: str, url: str, body_len: int) -> str | None:
        """Return None if method is permitted, otherwise an abort reason string."""
        if method in SAFE_AUTONOMOUS_METHODS:
            return None
        if any(rule.matches(method, url, body_len) for rule in self.approved_method_rules):
            return None
        return (
            f"HTTP method '{method}' blocked: state-changing methods require explicit L3 "
            "approval matching target origin, path, and body limit."
        )

    def _evaluate_special_scheme(
        self, url: str, scheme: str, method: str, resource_type: str
    ) -> EgressDecision | None:
        """Handle about:blank, bounded inline data: assets, and disallowed schemes."""
        if url == "about:blank" and resource_type == "document":
            return self._record(
                EgressAuditEvent(url, method, resource_type, "fulfill", "blank_page"),
                EgressDecision(
                    allowed=True,
                    action="fulfill",
                    reason="Initial about:blank frame.",
                    status_code=200,
                    headers={"content-type": "text/html"},
                ),
            )
        if scheme == "data":
            if resource_type != "document" and len(url) <= 262_144:
                return self._record(
                    EgressAuditEvent(url, method, resource_type, "fulfill", "data_asset"),
                    EgressDecision(
                        allowed=True, action="fulfill", reason="Bounded inline data URL."
                    ),
                )
            return self._abort(url, method, resource_type, "Disallowed data URL target.")
        if scheme not in ("http", "https"):
            return self._abort(url, method, resource_type, f"Blocked URL scheme '{scheme}'.")
        return None

    def _forward_via_pinned_transport(
        self,
        url: str,
        method: str,
        resource_type: str,
        headers: dict[str, str] | None,
        post_data: bytes | None,
    ) -> EgressDecision:
        stripped = {"host", "connection", "cookie", "authorization", "content-length"}
        clean_headers = {k: v for k, v in (headers or {}).items() if k.lower() not in stripped}
        try:
            resp = fetch_safe_url(
                url,
                method=method,
                request_body=post_data,
                headers=clean_headers,
                allowed_content_types=BROWSER_ALLOWED_CONTENT_TYPES,
                max_redirects=self.max_redirects,
                max_response_bytes=self.max_response_bytes,
                raise_for_status=False,
                resolver=self.resolver,
                sender=self.sender,
                socket_factory=self.socket_factory,
                ssl_wrapper=self.ssl_wrapper,
            )
        except UltimateMCPError as exc:
            return self._abort(url, method, resource_type, f"{exc.code.value}: {exc.message}")

        disp = resp.headers.get("content-disposition", "").lower()
        if disp.startswith("attachment") and not self.allow_downloads:
            return self._abort(
                url, method, resource_type, "Downloads disabled by browser policy."
            )
        fulfilled_headers = {
            k: v for k, v in resp.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS
        }
        return self._record(
            EgressAuditEvent(url, method, resource_type, "fulfill", "validated", resp.resolved_ips),
            EgressDecision(
                allowed=True,
                action="fulfill",
                reason="Fulfilled via pinned-IP validated transport.",
                status_code=resp.status_code,
                headers=fulfilled_headers,
                body=resp.body,
                resolved_ips=resp.resolved_ips,
                redirect_count=resp.redirect_count,
            ),
        )

    def evaluate_request(
        self,
        url: str,
        *,
        method: str = "GET",
        resource_type: str = "document",
        headers: dict[str, str] | None = None,
        post_data: bytes | None = None,
    ) -> EgressDecision:
        """Evaluate an intercepted browser request and return an abort or fulfill decision."""
        norm_method = method.strip().upper() or "GET"
        norm_type = resource_type.strip().lower() or "other"
        cleaned_url = url.strip()

        if norm_type in BLOCKED_RESOURCE_TYPES:
            return self._abort(
                cleaned_url, norm_method, norm_type, f"Resource type '{norm_type}' blocked."
            )

        parsed = urllib.parse.urlsplit(cleaned_url)
        scheme = parsed.scheme.lower()
        special = self._evaluate_special_scheme(cleaned_url, scheme, norm_method, norm_type)
        if special is not None:
            return special

        host_lower = (parsed.hostname or "").strip().rstrip(".").lower()
        if host_lower in BLOCKED_BACKGROUND_HOSTS:
            return self._abort(
                cleaned_url, norm_method, norm_type, f"Blocked background host '{host_lower}'."
            )

        method_err = self._check_method_allowed(norm_method, cleaned_url, len(post_data or b""))
        if method_err is not None:
            return self._abort(cleaned_url, norm_method, norm_type, method_err)

        return self._forward_via_pinned_transport(
            cleaned_url, norm_method, norm_type, headers, post_data
        )

    async def handle_playwright_route(self, route: Any) -> None:
        """Async Playwright route handler; ONLY calls ``fulfill`` or ``abort``, never continue."""
        req = route.request
        raw_post = getattr(req, "post_data_buffer", None)
        if callable(raw_post):
            raw_post = raw_post()
        elif raw_post is None and getattr(req, "post_data", None):
            raw_post = str(req.post_data).encode("utf-8", errors="replace")

        decision = await asyncio.to_thread(
            self.evaluate_request,
            req.url,
            method=req.method,
            resource_type=req.resource_type,
            headers=dict(req.headers) if getattr(req, "headers", None) else None,
            post_data=raw_post if isinstance(raw_post, bytes) else None,
        )
        if decision.allowed:
            await route.fulfill(
                status=decision.status_code or 200,
                headers=decision.headers,
                body=decision.body,
            )
            return
        await route.abort("blockedbyclient")

    async def attach_to_context(self, context: Any) -> None:
        """Register the strict wildcard route handler on a Playwright ``BrowserContext``."""
        await context.route("**/*", self.handle_playwright_route)
