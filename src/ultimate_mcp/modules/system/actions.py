"""System domain module: approval-gated file write and approved-action execution."""

from __future__ import annotations

from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.core.config import Settings
from ultimate_mcp.core.filesystem import atomic_write_text, resolve_scoped_path
from ultimate_mcp.core.models import ErrorCode, ToolResult, UltimateMCPError
from ultimate_mcp.core.permissions import PermissionLevel

ACTION_WRITE_TEXT_FILE = "write_text_file"


def prepare_write_file_service(
    settings: Settings,
    broker: ApprovalBroker,
    *,
    file_path: str,
    content: str,
    overwrite: bool = False,
) -> ToolResult:
    """Validate path and payload, then create a ``PENDING`` out-of-band approval record.

    Performs NO file modification.
    """
    target = resolve_scoped_path(
        file_path,
        settings.effective_write_roots,
        must_exist=False,
    )
    if target.exists() and not overwrite:
        raise UltimateMCPError(
            ErrorCode.PERMISSION_DENIED,
            f"Target file '{target.name}' already exists and overwrite=False.",
        )

    payload = {
        "file_path": str(target),
        "content": content,
        "overwrite": bool(overwrite),
    }
    byte_len = len(content.encode("utf-8"))
    summary = (
        f"Write {byte_len} bytes to '{target}' (overwrite={bool(overwrite)}) "
        "requires human approval."
    )
    record = broker.prepare_action(
        action_type=ACTION_WRITE_TEXT_FILE,
        permission_level=PermissionLevel.L2_WRITE,
        payload=payload,
        summary=summary,
        target_path=target,
    )
    return ToolResult.pending_approval(
        tool="prepare_write_file",
        approval_id=record.approval_id,
        summary=summary,
        digest=record.payload_digest,
        expires_at=record.expires_at_iso,
        target=str(target),
    )


def execute_approved_action_service(
    settings: Settings,
    broker: ApprovalBroker,
    *,
    approval_id: str,
) -> ToolResult:
    """Verify, atomically consume, and execute a previously approved action once."""
    record = broker.consume_approved(approval_id.strip())
    if record.action_type == ACTION_WRITE_TEXT_FILE:
        payload = record.payload
        written = atomic_write_text(
            payload["file_path"],
            str(payload["content"]),
            settings.effective_write_roots,
            overwrite=bool(payload.get("overwrite", False)),
        )
        return ToolResult.ok(
            tool="execute_approved_action",
            summary=f"Executed approved write to '{written}'.",
            data={
                "approval_id": record.approval_id,
                "action_type": record.action_type,
                "path": str(written),
                "bytes_written": len(str(payload["content"]).encode("utf-8")),
            },
        )

    raise UltimateMCPError(
        ErrorCode.UNSUPPORTED_FORMAT,
        f"Unsupported approved action_type '{record.action_type}'.",
    )
