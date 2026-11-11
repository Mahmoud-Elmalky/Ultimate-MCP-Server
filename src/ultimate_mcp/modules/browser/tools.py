"""MCP tool registration for navigation and read-only browser tools (Phase 03)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from mcp.types import CallToolResult
from pydantic import Field

from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.core.config import ProfileName, Settings
from ultimate_mcp.core.execution import ExecutionManager, TimeoutCategory
from ultimate_mcp.core.models import ToolResult
from ultimate_mcp.core.permissions import PermissionLevel
from ultimate_mcp.core.registry import ToolRegistry, ToolSpec
from ultimate_mcp.modules.browser.action_tools import register_browser_action_tools
from ultimate_mcp.modules.browser.manager import BrowserSessionManager
from ultimate_mcp.modules.browser.navigation import (
    browser_back_service,
    browser_open_service,
    browser_screenshot_service,
    browser_scroll_service,
    browser_snapshot_service,
    browser_tabs_service,
    browser_wait_service,
)

BROWSER_PROFILES: frozenset[ProfileName] = frozenset({ProfileName.BROWSER, ProfileName.FULL})
BROWSER_PACKAGES: tuple[str, ...] = ("playwright",)
ResultAdapter = Callable[[ToolResult, Settings], CallToolResult]
SessionIdArg = Annotated[str | None, Field(max_length=64, description="Optional session ID.")]


def register_browser_tools(
    *,
    registry: ToolRegistry,
    settings: Settings,
    executor: ExecutionManager,
    broker: ApprovalBroker,
    manager: BrowserSessionManager,
    adapt_result: ResultAdapter,
) -> None:
    """Register all SSRF-hardened browser tools under ``browser`` and ``full`` profiles."""
    timeout = max(settings.network_timeout_seconds, 30.0)

    def _register(
        name: str,
        description: str,
        handler: Any,
        wrapper: Any,
        *,
        permission: PermissionLevel,
        idempotent: bool,
        produces_provenance: bool = False,
    ) -> None:
        registry.register(
            ToolSpec(
                name=name,
                module="browser",
                description=description,
                handler=handler,
                mcp_wrapper=wrapper,
                permission=permission,
                profiles=BROWSER_PROFILES,
                timeout_category=TimeoutCategory.NETWORK,
                timeout_seconds=timeout,
                idempotent=idempotent,
                optional_packages=BROWSER_PACKAGES,
                produces_provenance=produces_provenance,
            )
        )

    async def browser_open(
        url: Annotated[str, Field(min_length=1, max_length=4096, description="HTTP(S) URL.")],
        session_id: SessionIdArg = None,
        wait_until: Annotated[
            str, Field(description="domcontentloaded, load, or commit.")
        ] = "domcontentloaded",
    ) -> CallToolResult:
        """Open a public HTTP(S) URL inside an isolated Chromium browser session."""
        res = await executor.execute(
            "browser_open",
            browser_open_service,
            {"manager": manager, "url": url, "session_id": session_id, "wait_until": wait_until},
            permission=PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.NETWORK,
            timeout_seconds=timeout,
            idempotent=False,
        )
        return adapt_result(res, settings)

    _register(
        "browser_open",
        "Navigate to a public HTTP(S) URL via pinned-IP egress in an isolated Chromium session.",
        browser_open_service,
        browser_open,
        permission=PermissionLevel.L1_PREPARE,
        idempotent=False,
        produces_provenance=True,
    )

    async def browser_back(session_id: SessionIdArg = None) -> CallToolResult:
        """Navigate back in active tab history."""
        res = await executor.execute(
            "browser_back",
            browser_back_service,
            {"manager": manager, "session_id": session_id},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            timeout_seconds=timeout,
            idempotent=False,
        )
        return adapt_result(res, settings)

    _register(
        "browser_back",
        "Navigate back in the active browser tab's history.",
        browser_back_service,
        browser_back,
        permission=PermissionLevel.L0_READ,
        idempotent=False,
    )

    async def browser_tabs(
        action: Annotated[str, Field(description="list, new, switch, or close.")] = "list",
        tab_index: Annotated[int | None, Field(ge=0, description="Zero-based tab index.")] = None,
        session_id: SessionIdArg = None,
    ) -> CallToolResult:
        """List, open, switch, or close bounded tabs inside a browser session."""
        res = await executor.execute(
            "browser_tabs",
            browser_tabs_service,
            {
                "manager": manager,
                "action": action,
                "tab_index": tab_index,
                "session_id": session_id,
            },
            permission=PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.NETWORK,
            timeout_seconds=timeout,
            idempotent=action == "list",
        )
        return adapt_result(res, settings)

    _register(
        "browser_tabs",
        "Manage tabs inside a browser session (list, new, switch, close; bounded max_tabs).",
        browser_tabs_service,
        browser_tabs,
        permission=PermissionLevel.L1_PREPARE,
        idempotent=False,
    )

    async def browser_snapshot(
        session_id: SessionIdArg = None,
        max_chars: Annotated[int, Field(ge=200, le=20000, description="Max text chars.")] = 8000,
        max_elements: Annotated[int, Field(ge=5, le=100, description="Max elements.")] = 50,
    ) -> CallToolResult:
        """Return bounded untrusted page text and indexed interactive element refs."""
        res = await executor.execute(
            "browser_snapshot",
            browser_snapshot_service,
            {
                "manager": manager,
                "session_id": session_id,
                "max_chars": max_chars,
                "max_elements": max_elements,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            timeout_seconds=timeout,
            idempotent=True,
        )
        return adapt_result(res, settings)

    _register(
        "browser_snapshot",
        "Capture bounded visible page text and numbered element refs from the active page.",
        browser_snapshot_service,
        browser_snapshot,
        permission=PermissionLevel.L0_READ,
        idempotent=True,
        produces_provenance=True,
    )

    async def browser_screenshot(
        filename: Annotated[
            str | None, Field(max_length=120, description="Optional PNG filename.")
        ] = None,
        full_page: Annotated[bool, Field(description="Capture full scrollable page.")] = False,
        session_id: SessionIdArg = None,
    ) -> CallToolResult:
        """Save a PNG screenshot under the configured output directory."""
        res = await executor.execute(
            "browser_screenshot",
            browser_screenshot_service,
            {
                "manager": manager,
                "settings": settings,
                "filename": filename,
                "full_page": full_page,
                "session_id": session_id,
            },
            permission=PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.NETWORK,
            timeout_seconds=timeout,
            idempotent=False,
        )
        return adapt_result(res, settings)

    _register(
        "browser_screenshot",
        "Capture PNG screenshot into output_dir/screenshots without returning huge base64.",
        browser_screenshot_service,
        browser_screenshot,
        permission=PermissionLevel.L1_PREPARE,
        idempotent=False,
    )

    async def browser_scroll(
        direction: Annotated[str, Field(description="down, up, top, or bottom.")] = "down",
        pixels: Annotated[int, Field(ge=50, le=4000, description="Scroll distance px.")] = 600,
        session_id: SessionIdArg = None,
    ) -> CallToolResult:
        """Scroll the active browser viewport."""
        res = await executor.execute(
            "browser_scroll",
            browser_scroll_service,
            {
                "manager": manager,
                "direction": direction,
                "pixels": pixels,
                "session_id": session_id,
            },
            permission=PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.NETWORK,
            timeout_seconds=timeout,
            idempotent=False,
        )
        return adapt_result(res, settings)

    _register(
        "browser_scroll",
        "Scroll the active browser page vertically (down, up, top, bottom).",
        browser_scroll_service,
        browser_scroll,
        permission=PermissionLevel.L1_PREPARE,
        idempotent=False,
    )

    async def browser_wait(
        selector: Annotated[str | None, Field(max_length=300, description="Ref or CSS.")] = None,
        text: Annotated[str | None, Field(max_length=300, description="Substring to wait.")] = None,
        timeout_ms: Annotated[int, Field(ge=100, le=15000, description="Timeout in ms.")] = 5000,
        session_id: SessionIdArg = None,
    ) -> CallToolResult:
        """Wait (bounded up to 15s) for an element or text substring to become visible."""
        res = await executor.execute(
            "browser_wait",
            browser_wait_service,
            {
                "manager": manager,
                "selector": selector,
                "text": text,
                "timeout_ms": timeout_ms,
                "session_id": session_id,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            timeout_seconds=timeout,
            idempotent=True,
        )
        return adapt_result(res, settings)

    _register(
        "browser_wait",
        "Wait (up to 15 seconds) for a selector or visible text substring on the active page.",
        browser_wait_service,
        browser_wait,
        permission=PermissionLevel.L0_READ,
        idempotent=True,
    )

    register_browser_action_tools(
        registry=registry,
        settings=settings,
        executor=executor,
        broker=broker,
        manager=manager,
        adapt_result=adapt_result,
        register_spec=_register,
        timeout=timeout,
    )
