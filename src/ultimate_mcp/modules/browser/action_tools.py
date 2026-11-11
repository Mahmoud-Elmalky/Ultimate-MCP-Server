"""MCP tool registration for browser interaction, form, transfer, and session tools (Phase 03)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from mcp.types import CallToolResult
from pydantic import Field

from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.core.config import Settings
from ultimate_mcp.core.execution import ExecutionManager, TimeoutCategory
from ultimate_mcp.core.models import ToolResult
from ultimate_mcp.core.permissions import PermissionLevel
from ultimate_mcp.core.registry import ToolRegistry
from ultimate_mcp.modules.browser.forms import browser_submit_form_service
from ultimate_mcp.modules.browser.interactions import (
    browser_click_service,
    browser_select_service,
    browser_type_service,
)
from ultimate_mcp.modules.browser.manager import BrowserSessionManager
from ultimate_mcp.modules.browser.transfers import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    browser_download_service,
    browser_upload_service,
)

ResultAdapter = Callable[[ToolResult, Settings], CallToolResult]
SessionIdArg = Annotated[str | None, Field(max_length=64, description="Optional session ID.")]
SelectorArg = Annotated[str, Field(min_length=1, max_length=300, description="Ref or CSS.")]
ApprovalIdArg = Annotated[str | None, Field(max_length=64, description="L3 approval ID.")]


async def browser_close_session_service(
    manager: BrowserSessionManager,
    *,
    session_id: str,
) -> ToolResult:
    """Close an isolated browser session and clear its ephemeral state."""
    closed = await manager.close_session(session_id)
    return ToolResult.ok(
        tool="browser_close_session",
        summary=(
            f"Closed browser session '{session_id}'."
            if closed
            else f"Browser session '{session_id}' was not active."
        ),
        data={"session_id": session_id, "closed": closed},
    )


def register_browser_action_tools(
    *,
    registry: ToolRegistry,
    settings: Settings,
    executor: ExecutionManager,
    broker: ApprovalBroker,
    manager: BrowserSessionManager,
    adapt_result: ResultAdapter,
    register_spec: Callable[..., None],
    timeout: float,
) -> None:
    """Register browser DOM interactions, L3 forms/downloads/uploads, and session cleanup."""
    _ = registry

    async def browser_click(
        selector: SelectorArg,
        session_id: SessionIdArg = None,
    ) -> CallToolResult:
        """Click a unique non-submit element on the active page."""
        res = await executor.execute(
            "browser_click",
            browser_click_service,
            {"manager": manager, "selector": selector, "session_id": session_id},
            permission=PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.NETWORK,
            timeout_seconds=timeout,
            idempotent=False,
        )
        return adapt_result(res, settings)

    register_spec(
        "browser_click",
        "Click a uniquely matched non-submit element (ref or CSS selector); fails if ambiguous.",
        browser_click_service,
        browser_click,
        permission=PermissionLevel.L1_PREPARE,
        idempotent=False,
    )

    async def browser_type(
        selector: SelectorArg,
        text: Annotated[str, Field(max_length=20000, description="Text to enter.")],
        clear_first: Annotated[bool, Field(description="Clear existing text first.")] = True,
        session_id: SessionIdArg = None,
    ) -> CallToolResult:
        """Enter text into a unique input or textarea element."""
        res = await executor.execute(
            "browser_type",
            browser_type_service,
            {
                "manager": manager,
                "selector": selector,
                "text": text,
                "clear_first": clear_first,
                "session_id": session_id,
            },
            permission=PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.NETWORK,
            timeout_seconds=timeout,
            idempotent=False,
        )
        return adapt_result(res, settings)

    register_spec(
        "browser_type",
        "Type or fill text into a unique text input/textarea element (blocks file inputs).",
        browser_type_service,
        browser_type,
        permission=PermissionLevel.L1_PREPARE,
        idempotent=False,
    )

    async def browser_select(
        selector: SelectorArg,
        value: Annotated[str, Field(min_length=1, max_length=300, description="Option value.")],
        session_id: SessionIdArg = None,
    ) -> CallToolResult:
        """Select an option on a unique ``<select>`` element."""
        res = await executor.execute(
            "browser_select",
            browser_select_service,
            {"manager": manager, "selector": selector, "value": value, "session_id": session_id},
            permission=PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.NETWORK,
            timeout_seconds=timeout,
            idempotent=False,
        )
        return adapt_result(res, settings)

    register_spec(
        "browser_select",
        "Select an option value on a uniquely identified <select> control.",
        browser_select_service,
        browser_select,
        permission=PermissionLevel.L1_PREPARE,
        idempotent=False,
    )

    async def browser_submit_form(
        form_selector: SelectorArg = "form",
        field_values: Annotated[
            dict[str, str] | None, Field(description="Optional form field map.")
        ] = None,
        session_id: SessionIdArg = None,
        approval_id: ApprovalIdArg = None,
    ) -> CallToolResult:
        """Prepare or execute an L3 out-of-band approved form submission."""
        has_approval = bool(approval_id and approval_id.strip())
        res = await executor.execute(
            "browser_submit_form",
            browser_submit_form_service,
            {
                "manager": manager,
                "broker": broker,
                "form_selector": form_selector,
                "field_values": field_values,
                "session_id": session_id,
                "approval_id": approval_id,
            },
            permission=(
                PermissionLevel.L3_SENSITIVE if has_approval else PermissionLevel.L1_PREPARE
            ),
            timeout_category=TimeoutCategory.NETWORK,
            timeout_seconds=timeout,
            idempotent=False,
            has_valid_approval=has_approval,
        )
        return adapt_result(res, settings)

    register_spec(
        "browser_submit_form",
        "Prepare or execute a browser HTML form submission with L3 out-of-band approval.",
        browser_submit_form_service,
        browser_submit_form,
        permission=PermissionLevel.L3_SENSITIVE,
        idempotent=False,
    )

    async def browser_download(
        url: Annotated[str, Field(min_length=1, max_length=4096, description="HTTP(S) URL.")],
        filename: Annotated[
            str | None, Field(max_length=160, description="Safe target filename.")
        ] = None,
        approval_id: ApprovalIdArg = None,
        max_bytes: Annotated[
            int, Field(ge=1, le=20_000_000, description="Max download bytes.")
        ] = DEFAULT_MAX_DOWNLOAD_BYTES,
    ) -> CallToolResult:
        """Prepare or execute an L3-approved pinned-IP download saved inside output_dir."""
        has_approval = bool(approval_id and approval_id.strip())
        res = await executor.execute(
            "browser_download",
            browser_download_service,
            {
                "manager": manager,
                "broker": broker,
                "settings": settings,
                "url": url,
                "filename": filename,
                "approval_id": approval_id,
                "max_bytes": max_bytes,
            },
            permission=(
                PermissionLevel.L3_SENSITIVE if has_approval else PermissionLevel.L1_PREPARE
            ),
            timeout_category=TimeoutCategory.NETWORK,
            timeout_seconds=timeout,
            idempotent=False,
            has_valid_approval=has_approval,
        )
        return adapt_result(res, settings)

    register_spec(
        "browser_download",
        "Prepare or execute pinned-IP download (L3 approved, no executables, output_dir only).",
        browser_download_service,
        browser_download,
        permission=PermissionLevel.L3_SENSITIVE,
        idempotent=False,
    )

    async def browser_upload(
        selector: SelectorArg,
        file_path: Annotated[
            str, Field(min_length=1, max_length=2000, description="Scoped read file path.")
        ],
        session_id: SessionIdArg = None,
        approval_id: ApprovalIdArg = None,
    ) -> CallToolResult:
        """Prepare or execute an L3-approved file attachment from workspace read roots."""
        has_approval = bool(approval_id and approval_id.strip())
        res = await executor.execute(
            "browser_upload",
            browser_upload_service,
            {
                "manager": manager,
                "broker": broker,
                "settings": settings,
                "selector": selector,
                "file_path": file_path,
                "session_id": session_id,
                "approval_id": approval_id,
            },
            permission=(
                PermissionLevel.L3_SENSITIVE if has_approval else PermissionLevel.L1_PREPARE
            ),
            timeout_category=TimeoutCategory.NETWORK,
            timeout_seconds=timeout,
            idempotent=False,
            has_valid_approval=has_approval,
        )
        return adapt_result(res, settings)

    register_spec(
        "browser_upload",
        "Prepare or execute L3-approved file upload attachment bound to path, SHA-256, origin.",
        browser_upload_service,
        browser_upload,
        permission=PermissionLevel.L3_SENSITIVE,
        idempotent=False,
    )

    async def browser_close_session(
        session_id: Annotated[str, Field(min_length=1, max_length=64, description="Session ID.")],
    ) -> CallToolResult:
        """Close an active ephemeral browser session."""
        res = await executor.execute(
            "browser_close_session",
            browser_close_session_service,
            {"manager": manager, "session_id": session_id},
            permission=PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.NETWORK,
            timeout_seconds=timeout,
            idempotent=False,
        )
        return adapt_result(res, settings)

    register_spec(
        "browser_close_session",
        "Close an active isolated browser session and release its context and tabs.",
        browser_close_session_service,
        browser_close_session,
        permission=PermissionLevel.L1_PREPARE,
        idempotent=False,
    )
