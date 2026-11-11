"""Playwright Browser Security & Network Egress Control (Phase 03 Slice 3.1.1).

Enforces multi-layer browser egress control:
1. Chromium launch lockdown: ``--host-resolver-rules=MAP * ~NOTFOUND`` (zero ``EXCLUDE
   127.0.0.1``) in standalone mode, or ``MAP egress-proxy.invalid 127.0.0.1 , MAP * ~NOTFOUND``
   with ``--proxy-server=http://egress-proxy.invalid:<port>`` and
   ``--proxy-bypass-list=<-loopback>`` plus ``--disable-quic`` and disabled WebRTC non-proxied
   UDP.
2. Context isolation: ephemeral context (``service_workers="block"``, ``accept_downloads=False``).
3. Playwright ``context.route_web_socket("**/*", ...)`` aborts all ``ws://`` and ``wss://`` by
   default before any page is created.
4. Playwright ``context.route("**/*", ...)`` intercepts all navigations, redirects, subresources,
   scripts, images, iframes, fetch/XHR, and pings, fulfilling solely via pinned-IP
   ``fetch_safe_url`` (never ``route.continue_()`` for external URLs).
5. All state-changing HTTP methods (``POST``, ``PUT``, ``PATCH``, ``DELETE``) block by default
   and require a valid single-use, non-expired, payload-digest-bound out-of-band L3
   ``ApprovalBroker`` record (and reject redirects with ``max_redirects=0``).
"""

from __future__ import annotations

import asyncio
import urllib.parse
from typing import Any

from ultimate_mcp.core.approvals import ApprovalBroker
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
    DEFAULT_BROWSER_SESSION_ID,
    HOP_BY_HOP_HEADERS,
    STATE_CHANGING_METHODS,
    STRICT_CHROMIUM_ARGS,
    WEBRTC_DISABLE_INIT_SCRIPT,
    WINDOWS_ISOLATION_LIMITATION,
    EgressAuditEvent,
    EgressDecision,
    MutationApprovalGate,
    build_proxy_chromium_args,
    build_windows_firewall_guidance,
    prepare_browser_mutation_approval,
)
from ultimate_mcp.modules.browser.proxy import LoopbackValidatingProxy

__all__ = [
    "BLOCKED_BACKGROUND_HOSTS", "BLOCKED_RESOURCE_TYPES", "BROWSER_ALLOWED_CONTENT_TYPES",
    "DEFAULT_BROWSER_SESSION_ID", "HOP_BY_HOP_HEADERS", "STATE_CHANGING_METHODS",
    "STRICT_CHROMIUM_ARGS", "WEBRTC_DISABLE_INIT_SCRIPT", "WINDOWS_ISOLATION_LIMITATION",
    "BrowserEgressPolicy", "EgressAuditEvent", "EgressDecision", "LoopbackValidatingProxy",
    "build_windows_firewall_guidance", "prepare_browser_mutation_approval",
]


class BrowserEgressPolicy:
    """Enforces pinned-IP network egress and isolation rules on Playwright contexts."""

    def __init__(
        self,
        *,
        approval_broker: ApprovalBroker | None = None,
        proxy: LoopbackValidatingProxy | None = None,
        session_id: str = DEFAULT_BROWSER_SESSION_ID,
        auth_context: str = "",
        cookie_header: str = "",
        disable_webrtc: bool = True,
        strict_firewall: bool = False,
        max_redirects: int = 4,
        max_response_bytes: int = 2 * 1024 * 1024,
        allow_downloads: bool = False,
        max_tabs: int = 5,
        resolver: ResolverFunc = default_dns_resolver,
        socket_factory: SocketFactory = default_socket_factory,
        ssl_wrapper: SSLWrapper = default_ssl_wrapper,
        sender: TransportSender | None = None,
    ) -> None:
        self.approval_broker = approval_broker
        self.proxy = proxy
        self.session_id = session_id
        self.auth_context = auth_context
        self.cookie_header = cookie_header
        self.disable_webrtc = disable_webrtc
        self.strict_firewall = strict_firewall
        self.max_redirects = max_redirects
        self.max_response_bytes = max_response_bytes
        self.allow_downloads = allow_downloads
        self.max_tabs = max_tabs
        self.resolver = resolver
        self.socket_factory = socket_factory
        self.ssl_wrapper = ssl_wrapper
        self.sender = sender
        self._mutation_gate = MutationApprovalGate(approval_broker)
        self.audit_log: list[EgressAuditEvent] = []

    def authorize_mutation(self, approval_id: str) -> None:
        """Register an out-of-band ``approval_id`` for one-time atomic verification on use."""
        self._mutation_gate.authorize(approval_id)

    def build_launch_options(self, *, headless: bool = True) -> dict[str, Any]:
        """Return hardened Chromium launch options (proxy-enforced when proxy is active)."""
        if self.proxy is not None and self.proxy.port > 0:
            return {"headless": headless, "args": build_proxy_chromium_args(self.proxy.port)}
        return {"headless": headless, "args": list(STRICT_CHROMIUM_ARGS)}

    def build_context_options(self) -> dict[str, Any]:
        """Return isolated ephemeral BrowserContext options (no personal profile)."""
        return {
            "service_workers": "block", "accept_downloads": False,
            "ignore_https_errors": False, "permissions": [],
        }

    def _record(self, event: EgressAuditEvent, decision: EgressDecision) -> EgressDecision:
        self.audit_log.append(event)
        return decision

    def _abort(self, url: str, method: str, resource_type: str, reason: str) -> EgressDecision:
        return self._record(
            EgressAuditEvent(url, method, resource_type, "abort", reason),
            EgressDecision(allowed=False, action="abort", reason=reason),
        )

    def _consume_mutation_approval(
        self, method: str, url: str, post_data: bytes | None, headers: dict[str, str] | None,
    ) -> str | None:
        return self._mutation_gate.consume_for_request(
            method, url, post_data, headers=headers,
            session_id=self.session_id, auth_context=self.auth_context,
            cookie_header=self.cookie_header,
        )

    def _evaluate_special_scheme(
        self, url: str, scheme: str, method: str, resource_type: str
    ) -> EgressDecision | None:
        """Handle about:blank, bounded inline data: assets, and disallowed schemes."""
        if url == "about:blank" and resource_type == "document":
            return self._record(
                EgressAuditEvent(url, method, resource_type, "fulfill", "blank_page"),
                EgressDecision(
                    allowed=True, action="fulfill", reason="Initial about:blank frame.",
                    status_code=200, headers={"content-type": "text/html"},
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
        effective_redirects = 0 if method not in SAFE_AUTONOMOUS_METHODS else self.max_redirects
        try:
            resp = fetch_safe_url(
                url, method=method, request_body=post_data, headers=clean_headers,
                allowed_content_types=BROWSER_ALLOWED_CONTENT_TYPES,
                max_redirects=effective_redirects, max_response_bytes=self.max_response_bytes,
                raise_for_status=False, resolver=self.resolver, sender=self.sender,
                socket_factory=self.socket_factory, ssl_wrapper=self.ssl_wrapper,
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
        special = self._evaluate_special_scheme(
            cleaned_url, parsed.scheme.lower(), norm_method, norm_type
        )
        if special is not None:
            return special

        host_lower = (parsed.hostname or "").strip().rstrip(".").lower()
        if host_lower in BLOCKED_BACKGROUND_HOSTS:
            return self._abort(
                cleaned_url, norm_method, norm_type, f"Blocked background host '{host_lower}'."
            )

        if norm_method not in SAFE_AUTONOMOUS_METHODS:
            deny_reason = self._consume_mutation_approval(
                norm_method, cleaned_url, post_data, headers
            )
            if deny_reason is not None:
                return self._abort(cleaned_url, norm_method, norm_type, deny_reason)

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

    async def handle_playwright_websocket(self, ws_route: Any) -> None:
        """Async Playwright WebSocketRoute handler; aborts ALL ws:// and wss:// by default."""
        ws_url = str(getattr(ws_route, "url", ""))
        self.audit_log.append(
            EgressAuditEvent(
                ws_url, "GET", "websocket", "abort", "WebSockets blocked by egress policy."
            )
        )
        await ws_route.close(code=1008, reason="WebSockets blocked by egress policy")

    async def attach_to_context(self, context: Any) -> None:
        """Register WebSocket, wildcard HTTP, and WebRTC hardening before opening pages."""
        if self.disable_webrtc and hasattr(context, "add_init_script"):
            await context.add_init_script(WEBRTC_DISABLE_INIT_SCRIPT)
        await context.route_web_socket("**/*", self.handle_playwright_websocket)
        await context.route("**/*", self.handle_playwright_route)
