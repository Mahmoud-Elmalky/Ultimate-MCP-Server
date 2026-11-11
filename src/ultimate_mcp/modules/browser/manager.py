"""Playwright browser lifecycle and isolated session management (Phase 03)."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.core.http import ResolverFunc, default_dns_resolver
from ultimate_mcp.core.http_transport import (
    SocketFactory,
    SSLWrapper,
    default_socket_factory,
    default_ssl_wrapper,
)
from ultimate_mcp.core.models import ErrorCode, UltimateMCPError
from ultimate_mcp.core.registry import is_package_available
from ultimate_mcp.modules.browser.egress import BrowserEgressPolicy
from ultimate_mcp.modules.browser.proxy import LoopbackValidatingProxy


@dataclass
class BrowserSession:
    """An isolated, ephemeral Playwright BrowserContext + bounded tab list."""

    session_id: str
    context: Any
    policy: BrowserEgressPolicy
    pages: list[Any] = field(default_factory=list)
    active_tab: int = 0
    created_at: float = field(default_factory=time.time)
    last_refs: dict[str, str] = field(default_factory=dict)

    @property
    def current_page(self) -> Any:
        if not self.pages:
            raise UltimateMCPError(ErrorCode.NOT_FOUND, "Session has no active tabs.")
        idx = max(0, min(self.active_tab, len(self.pages) - 1))
        self.active_tab = idx
        return self.pages[idx]


class BrowserSessionManager:
    """Manages ephemeral Chromium sessions with fail-closed egress enforcement."""

    def __init__(
        self,
        *,
        approval_broker: ApprovalBroker | None = None,
        max_sessions: int = 3,
        max_tabs: int = 5,
        headless: bool = True,
        disable_webrtc: bool = True,
        resolver: ResolverFunc = default_dns_resolver,
        socket_factory: SocketFactory = default_socket_factory,
        ssl_wrapper: SSLWrapper = default_ssl_wrapper,
    ) -> None:
        self.approval_broker = approval_broker
        self.max_sessions = max_sessions
        self.max_tabs = max_tabs
        self.headless = headless
        self.disable_webrtc = disable_webrtc
        self.resolver = resolver
        self.socket_factory = socket_factory
        self.ssl_wrapper = ssl_wrapper

        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self._proxy: LoopbackValidatingProxy | None = None
        self._sessions: dict[str, BrowserSession] = {}

    @property
    def proxy(self) -> LoopbackValidatingProxy | None:
        return self._proxy

    @property
    def active_session_ids(self) -> list[str]:
        return list(self._sessions.keys())

    async def _ensure_started(self) -> None:
        if self._browser is not None and self._proxy is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise UltimateMCPError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Playwright is not installed. Install optional extra 'browser'.",
            ) from exc

        if self._proxy is None:
            self._proxy = LoopbackValidatingProxy(
                resolver=self.resolver,
                socket_factory=self.socket_factory,
                ssl_wrapper=self.ssl_wrapper,
            )
            await self._proxy.start()

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        launch_policy = BrowserEgressPolicy(proxy=self._proxy, disable_webrtc=self.disable_webrtc)
        self._browser = await self._playwright.chromium.launch(
            **launch_policy.build_launch_options(headless=self.headless)
        )

    async def create_session(self, session_id: str | None = None) -> BrowserSession:
        """Create a fresh ephemeral BrowserContext with three-layer egress isolation."""
        async with self._lock:
            await self._ensure_started()
            if len(self._sessions) >= self.max_sessions:
                raise UltimateMCPError(
                    ErrorCode.RESOURCE_LIMIT_EXCEEDED,
                    f"Maximum active browser sessions ({self.max_sessions}) reached; "
                    "close a session first.",
                )
            sid = (session_id or "").strip() or f"brs_{uuid.uuid4().hex[:10]}"
            if sid in self._sessions:
                return self._sessions[sid]

            policy = BrowserEgressPolicy(
                approval_broker=self.approval_broker,
                proxy=self._proxy,
                session_id=sid,
                disable_webrtc=self.disable_webrtc,
                max_tabs=self.max_tabs,
                resolver=self.resolver,
                socket_factory=self.socket_factory,
                ssl_wrapper=self.ssl_wrapper,
            )
            ctx = await self._browser.new_context(**policy.build_context_options())
            await policy.attach_to_context(ctx)
            page = await ctx.new_page()
            session = BrowserSession(session_id=sid, context=ctx, policy=policy, pages=[page])
            self._bind_popup_guard(session)
            self._sessions[sid] = session
            return session

    def _bind_popup_guard(self, session: BrowserSession) -> None:
        def _on_page(new_page: Any) -> None:
            if new_page in session.pages:
                return
            if len(session.pages) >= self.max_tabs:
                asyncio.create_task(new_page.close())
                return
            session.pages.append(new_page)

        if hasattr(session.context, "on"):
            session.context.on("page", _on_page)

    async def get_or_create_session(self, session_id: str | None = None) -> BrowserSession:
        """Return an existing session or create a new isolated session."""
        if session_id and session_id.strip() in self._sessions:
            return self._sessions[session_id.strip()]
        if not session_id and self._sessions:
            first_id = next(iter(self._sessions))
            return self._sessions[first_id]
        return await self.create_session(session_id=session_id)

    def get_session(self, session_id: str | None = None) -> BrowserSession:
        """Return an existing session by ID (or single active session), else raise NOT_FOUND."""
        if session_id and session_id.strip() in self._sessions:
            return self._sessions[session_id.strip()]
        if not session_id and self._sessions:
            return next(iter(self._sessions.values()))
        raise UltimateMCPError(
            ErrorCode.NOT_FOUND,
            "No active browser session found; call browser_open first.",
        )

    async def new_tab(self, session_id: str | None = None) -> tuple[BrowserSession, int]:
        """Open a new tab inside ``session_id`` up to ``max_tabs``."""
        session = self.get_session(session_id)
        session.pages = [p for p in session.pages if not p.is_closed()]
        if len(session.pages) >= self.max_tabs:
            raise UltimateMCPError(
                ErrorCode.RESOURCE_LIMIT_EXCEEDED,
                f"Maximum tabs per session ({self.max_tabs}) reached.",
            )
        page = await session.context.new_page()
        if page not in session.pages:
            session.pages.append(page)
        session.active_tab = session.pages.index(page)
        return session, session.active_tab

    async def switch_tab(
        self, tab_index: int, session_id: str | None = None
    ) -> BrowserSession:
        """Switch active tab index inside ``session_id``."""
        session = self.get_session(session_id)
        session.pages = [p for p in session.pages if not p.is_closed()]
        if tab_index < 0 or tab_index >= len(session.pages):
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                f"Tab index {tab_index} out of range (0..{max(0, len(session.pages) - 1)}).",
            )
        session.active_tab = tab_index
        await session.current_page.bring_to_front()
        return session

    async def close_tab(
        self, tab_index: int, session_id: str | None = None
    ) -> BrowserSession:
        """Close a specific tab (recreating blank tab if closing the last tab is avoided)."""
        session = self.get_session(session_id)
        session.pages = [p for p in session.pages if not p.is_closed()]
        if len(session.pages) <= 1:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                "Cannot close the last remaining tab in a session; close the session instead.",
            )
        if tab_index < 0 or tab_index >= len(session.pages):
            raise UltimateMCPError(ErrorCode.INVALID_INPUT, f"Invalid tab index {tab_index}.")
        target = session.pages.pop(tab_index)
        await target.close()
        session.active_tab = min(session.active_tab, len(session.pages) - 1)
        return session

    async def close_session(self, session_id: str) -> bool:
        """Close and destroy an ephemeral browser session and its storage."""
        session = self._sessions.pop(session_id.strip(), None)
        if session is None:
            return False
        await session.context.close()
        return True

    async def close_all(self) -> None:
        """Shut down all sessions, Chromium browser, Playwright runtime, and proxy."""
        for sid in list(self._sessions.keys()):
            await self.close_session(sid)
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        if self._proxy is not None:
            await self._proxy.stop()
            self._proxy = None


def check_playwright_browser_binaries() -> dict[str, Any]:
    """Report Playwright package and Chromium binary availability without starting a browser."""
    pkg_installed = is_package_available("playwright")
    if not pkg_installed:
        return {"playwright_installed": False, "chromium_installed": False, "cache_dir": None}

    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    candidates = [Path(env_path)] if env_path and env_path != "0" else [
        Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright",
        Path.home() / "AppData" / "Local" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
        Path.home() / "Library" / "Caches" / "ms-playwright",
    ]
    for root in candidates:
        if not str(root) or not root.is_dir():
            continue
        chromium_dirs = [p for p in root.iterdir() if p.is_dir() and p.name.startswith("chromium")]
        if chromium_dirs:
            return {
                "playwright_installed": True,
                "chromium_installed": True,
                "cache_dir": str(root),
            }
    return {"playwright_installed": True, "chromium_installed": False, "cache_dir": None}
