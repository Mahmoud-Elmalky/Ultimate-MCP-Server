"""Approval-gated document conversion, creation, and backup-preserving editing services."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.core.filesystem import atomic_write_text, resolve_scoped_path
from ultimate_mcp.core.models import ErrorCode, ToolResult, UltimateMCPError
from ultimate_mcp.core.permissions import PermissionLevel
from ultimate_mcp.modules.documents.security import validate_document_file
from ultimate_mcp.modules.documents.writers import (
    CREATABLE_FORMATS,
    apply_document_edit,
    create_timestamped_backup,
    write_docx_file,
    write_xlsx_file,
)


def approval_required_result(tool_name: str, approval: Any, extra: dict[str, Any]) -> ToolResult:
    res = ToolResult.pending_approval(
        tool=tool_name,
        approval_id=approval.approval_id,
        summary=approval.summary,
        digest=approval.payload_digest,
        expires_at=str(approval.expires_at),
        target=approval.target_path,
    )
    res.data.update(
        {
            "action_type": approval.action_type,
            "permission_level": approval.permission_level.value,
            **extra,
        }
    )
    res.warnings.append(
        "Explicit user approval required before writing or modifying workspace files."
    )
    return res


def write_format_to_disk(
    dest: Path,
    fmt: str,
    *,
    allowed_write_roots: Sequence[Path],
    text_body: str,
    title: str | None = None,
    headers: list[str] | None = None,
    rows: list[list[Any]] | None = None,
    overwrite: bool = False,
) -> int:
    if fmt in ("txt", "md", "json", "csv"):
        atomic_write_text(dest, text_body, allowed_write_roots, overwrite=overwrite)
        return dest.stat().st_size
    if dest.exists() and not overwrite:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT, f"Refusing to overwrite existing '{dest.name}'."
        )
    if fmt == "docx":
        paras = [line for line in text_body.splitlines() if line.strip()]
        tbl_rows = ([headers] if headers else []) + (rows or [])
        return write_docx_file(dest, title=title, paragraphs=paras, tables=tbl_rows)
    if fmt == "xlsx":
        return write_xlsx_file(
            dest, sheet_name=title or "Sheet1", headers=headers or [], rows=rows or []
        )
    raise UltimateMCPError(ErrorCode.UNSUPPORTED_FORMAT, f"Unsupported output format '{fmt}'.")


def document_create_service(
    allowed_write_roots: Sequence[Path],
    broker: ApprovalBroker,
    *,
    output_path: str,
    doc_format: str | None = None,
    content: str = "",
    title: str | None = None,
    headers: list[str] | None = None,
    rows: list[list[Any]] | None = None,
    overwrite: bool = False,
    approval_id: str | None = None,
) -> ToolResult:
    """Create a new structured or plain document inside workspace behind L2 approval."""
    dest = resolve_scoped_path(output_path, allowed_write_roots, must_exist=False)
    fmt = (doc_format or dest.suffix.lstrip(".") or "txt").lower()
    if fmt not in CREATABLE_FORMATS:
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT, f"Unsupported document format '{fmt}'."
        )
    if dest.exists() and not overwrite:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT, f"File '{dest.name}' already exists; overwrite is false."
        )

    text_body = content
    if fmt == "json" and content.strip():
        try:
            text_body = json.dumps(json.loads(content), indent=2, ensure_ascii=False)
        except json.JSONDecodeError as exc:
            raise UltimateMCPError(ErrorCode.INVALID_INPUT, f"Invalid JSON content: {exc}") from exc
    elif fmt == "csv" and not content.strip() and (headers or rows):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        if headers:
            w.writerow(headers)
        for r in rows or []:
            w.writerow(r)
        text_body = buf.getvalue()

    content_hash = hashlib.sha256(
        json.dumps({"text": text_body, "headers": headers, "rows": rows}, sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest()
    payload = {
        "output_path": str(dest),
        "format": fmt,
        "title": title,
        "content_sha256": content_hash,
        "overwrite": overwrite,
    }
    if not approval_id:
        rec = broker.prepare_action(
            action_type="document_create",
            permission_level=PermissionLevel.L2_WRITE,
            payload=payload,
            summary=f"Create {fmt.upper()} document '{dest.name}'",
            target_path=dest,
        )
        return approval_required_result("document_create", rec, {"format": fmt})

    broker.consume_approved(
        approval_id, expected_action_type="document_create", expected_payload=payload
    )
    backup_path = (
        str(create_timestamped_backup(dest, list(allowed_write_roots))) if dest.exists() else None
    )
    bytes_written = write_format_to_disk(
        dest,
        fmt,
        allowed_write_roots=allowed_write_roots,
        text_body=text_body,
        title=title,
        headers=headers,
        rows=rows,
        overwrite=overwrite,
    )
    return ToolResult.ok(
        tool="document_create",
        summary=f"Created '{dest.name}' ({fmt.upper()}, {bytes_written} bytes).",
        data={
            "path": str(dest),
            "format": fmt,
            "bytes_written": bytes_written,
            "backup_path": backup_path,
            "approval_id": approval_id,
        },
    )


def document_edit_service(
    allowed_read_roots: Sequence[Path],
    allowed_write_roots: Sequence[Path],
    broker: ApprovalBroker,
    *,
    path: str,
    mode: str,
    target_text: str | None = None,
    replacement_text: str | None = None,
    row_values: list[str] | None = None,
    output_path: str | None = None,
    approval_id: str | None = None,
) -> ToolResult:
    """Safely edit a text, Markdown, JSON, CSV, or DOCX document with L2 approval + .bak backup."""
    if mode not in ("replace_text", "append", "append_row"):
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, f"Unsupported edit mode '{mode}'.")
    src, _ = validate_document_file(path, allowed_read_roots)
    src_sha = hashlib.sha256(src.read_bytes()).hexdigest()
    dest = resolve_scoped_path(output_path or src, allowed_write_roots, must_exist=False)
    suffix = src.suffix.lower()
    if suffix not in (".txt", ".md", ".markdown", ".json", ".csv", ".docx"):
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT, f"Editing not supported for '{suffix}'."
        )

    payload = {
        "source_path": str(src),
        "source_sha256": src_sha,
        "dest_path": str(dest),
        "mode": mode,
        "target_text": target_text,
        "replacement_text": replacement_text,
        "row_values": row_values,
    }
    if not approval_id:
        rec = broker.prepare_action(
            action_type="document_edit",
            permission_level=PermissionLevel.L2_WRITE,
            payload=payload,
            summary=f"Edit '{src.name}' (mode={mode}) -> '{dest.name}'",
            target_path=dest,
        )
        return approval_required_result(
            "document_edit", rec, {"source_path": str(src), "mode": mode}
        )

    broker.consume_approved(
        approval_id, expected_action_type="document_edit", expected_payload=payload
    )
    backup_path = (
        str(create_timestamped_backup(dest, list(allowed_write_roots))) if dest.exists() else None
    )
    bytes_written = apply_document_edit(
        src,
        dest,
        suffix=suffix,
        allowed_write_roots=allowed_write_roots,
        mode=mode,
        target_text=target_text,
        replacement_text=replacement_text,
        row_values=row_values,
    )
    return ToolResult.ok(
        tool="document_edit",
        summary=f"Edited '{src.name}' -> '{dest.name}' (backup preserved).",
        data={
            "source_path": str(src),
            "output_path": str(dest),
            "backup_path": backup_path,
            "mode": mode,
            "bytes_written": bytes_written,
            "approval_id": approval_id,
        },
    )
