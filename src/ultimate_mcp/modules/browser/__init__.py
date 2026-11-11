"""SSRF-hardened browser security, egress control, and Playwright browser tools (Phase 03)."""

from ultimate_mcp.modules.browser.action_tools import browser_close_session_service
from ultimate_mcp.modules.browser.egress import (
    BROWSER_ALLOWED_CONTENT_TYPES,
    STRICT_CHROMIUM_ARGS,
    BrowserEgressPolicy,
    EgressAuditEvent,
    EgressDecision,
    LoopbackValidatingProxy,
    prepare_browser_mutation_approval,
)
from ultimate_mcp.modules.browser.manager import BrowserSession, BrowserSessionManager
from ultimate_mcp.modules.browser.tools import register_browser_tools

__all__ = [
    "BROWSER_ALLOWED_CONTENT_TYPES",
    "STRICT_CHROMIUM_ARGS",
    "BrowserEgressPolicy",
    "BrowserSession",
    "BrowserSessionManager",
    "EgressAuditEvent",
    "EgressDecision",
    "LoopbackValidatingProxy",
    "browser_close_session_service",
    "prepare_browser_mutation_approval",
    "register_browser_tools",
]
