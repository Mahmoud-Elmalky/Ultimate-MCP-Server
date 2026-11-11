"""Policy constants, Chromium lockdown flags, and egress dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field

from ultimate_mcp.modules.browser.mutation_auth import (
    BROWSER_MUTATION_ACTION_TYPE,
    DEFAULT_BROWSER_SESSION_ID,
    STATE_CHANGING_METHODS,
    MutationApprovalGate,
    build_mutation_approval_payload,
    canonicalize_mutation_url,
    prepare_browser_mutation_approval,
)

__all__ = [
    "BLOCKED_BACKGROUND_HOSTS",
    "BLOCKED_RESOURCE_TYPES",
    "BROWSER_ALLOWED_CONTENT_TYPES",
    "BROWSER_MUTATION_ACTION_TYPE",
    "DEFAULT_BROWSER_SESSION_ID",
    "HOP_BY_HOP_HEADERS",
    "PROXY_SYNTHETIC_HOST",
    "STATE_CHANGING_METHODS",
    "STRICT_CHROMIUM_ARGS",
    "WEBRTC_DISABLE_INIT_SCRIPT",
    "WINDOWS_ISOLATION_LIMITATION",
    "EgressAuditEvent",
    "EgressDecision",
    "MutationApprovalGate",
    "build_mutation_approval_payload",
    "build_proxy_chromium_args",
    "build_windows_firewall_guidance",
    "canonicalize_mutation_url",
    "prepare_browser_mutation_approval",
]

PROXY_SYNTHETIC_HOST = "egress-proxy.invalid"

# Standalone flags: zero EXCLUDE exceptions.
# Pair with LoopbackValidatingProxy (--proxy-bypass-list=<-loopback>) to enforce TCP/TURN blocking.
STRICT_CHROMIUM_ARGS: tuple[str, ...] = (
    "--host-resolver-rules=MAP * ~NOTFOUND",
    "--disable-quic",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--no-pings",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--enforce-webrtc-ip-permission-check",
)

# Auxiliary JS-surface hardening when WebRTC is disabled (never claimed as sole security control).
WEBRTC_DISABLE_INIT_SCRIPT = """
(() => {
  const blocked = () => {
    throw new DOMException('WebRTC is disabled by browser security policy.', 'NotAllowedError');
  };
  for (const name of [
    'RTCPeerConnection', 'webkitRTCPeerConnection', 'mozRTCPeerConnection',
    'RTCDataChannel', 'RTCIceGatherer',
  ]) {
    try { Object.defineProperty(window, name, { value: blocked, writable: false }); } catch (_) {}
  }
})();
""".strip()

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
    "text/html", "text/plain", "text/css", "text/javascript",
    "application/javascript", "application/x-javascript", "application/xhtml+xml",
    "application/xml", "text/xml", "application/json", "image/png", "image/jpeg",
    "image/webp", "image/gif", "image/svg+xml", "image/x-icon",
    "image/vnd.microsoft.icon", "font/woff", "font/woff2", "font/ttf",
    "application/font-woff",
)

HOP_BY_HOP_HEADERS: frozenset[str] = frozenset(
    {
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailer", "transfer-encoding", "upgrade", "content-encoding",
        "content-length", "set-cookie",
    }
)

WINDOWS_ISOLATION_LIMITATION = (
    "On unprivileged Windows without Admin WFP drivers, kernel CLONE_NEWNET namespaces and "
    "seccomp-bpf are unavailable. Browser isolation enforces network rules via Chromium flags "
    "(--host-resolver-rules=MAP egress-proxy.invalid 127.0.0.1 , MAP * ~NOTFOUND, "
    "--proxy-server=http://egress-proxy.invalid:<port>, --proxy-bypass-list=<-loopback>, "
    "--disable-quic, --force-webrtc-ip-handling-policy=disable_non_proxied_udp), "
    "service_workers='block', context.route + context.route_web_socket, WebRTC init-script "
    "defense-in-depth, and the fail-closed LoopbackValidatingProxy. Full kernel-level enforcement "
    "blocking sandbox escapes or native plugins requires elevated Windows Firewall outbound rules."
)


def build_proxy_chromium_args(proxy_port: int) -> list[str]:
    """Return Chromium flags pinning all egress to the validating proxy without EXCLUDE."""
    if proxy_port <= 0 or proxy_port > 65535:
        raise ValueError(f"Invalid proxy port: {proxy_port}")
    return [
        f"--host-resolver-rules=MAP {PROXY_SYNTHETIC_HOST} 127.0.0.1 , MAP * ~NOTFOUND",
        f"--proxy-server=http://{PROXY_SYNTHETIC_HOST}:{proxy_port}",
        "--proxy-bypass-list=<-loopback>",
        *STRICT_CHROMIUM_ARGS[1:],
    ]


def build_windows_firewall_guidance(chromium_exe_path: str = "<chromium.exe>") -> str:
    """Return elevated Windows Firewall guidance to block direct Chromium WinSock egress."""
    return (
        "Run in elevated PowerShell to block direct outbound TCP/UDP from Chromium (allowing "
        f"only local proxy loopback): New-NetFirewallRule -DisplayName 'UltimateMCP-Chromium-Block'"
        f" -Direction Outbound -Program '{chromium_exe_path}' -Action Block -RemoteAddress Any"
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
