"""Document conversion service supporting L0 previews and L2-approved file exports."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.core.filesystem import resolve_scoped_path
from ultimate_mcp.core.models import ErrorCode, ResultStatus, ToolResult, UltimateMCPError
from ultimate_mcp.core.permissions import PermissionLevel
from ultimate_mcp.modules.documents.analysis import collect_all_bounded
from ultimate_mcp.modules.documents.models import (
    MAX_RETURNED_CHARS,
    UNTRUSTED_DOC_WARNING,
    ExtractedDocument,
)
from ultimate_mcp.modules.documents.mutations import (
    approval_required_result,
    write_format_to_disk,
)
from ultimate_mcp.modules.documents.security import build_doc_provenance
from ultimate_mcp.modules.documents.writers import (
    CREATABLE_FORMATS,
    create_timestamped_backup,
    render_extracted_to_format,
)


def document_convert_service(
    allowed_read_roots: Sequence[Path],
    allowed_write_roots: Sequence[Path],
    broker: ApprovalBroker,
    *,
    path: str,
    target_format: str,
    output_path: str | None = None,
    overwrite: bool = False,
    approval_id: str | None = None,
) -> ToolResult:
    """Convert a scoped document to txt/md/json/csv (or L2-approved docx/xlsx file)."""
    fmt = target_format.lower().lstrip(".")
    if fmt not in CREATABLE_FORMATS:
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT, f"Unsupported target_format '{target_format}'."
        )

    first, chunks, tables, scan_trunc = collect_all_bounded(path, allowed_read_roots)
    merged = ExtractedDocument(
        document_id=first.document_id,
        path=first.path,
        doc_format=first.doc_format,
        size_bytes=first.size_bytes,
        total_units=first.total_units,
        unit_name=first.unit_name,
        start_unit=0,
        end_unit=first.total_units,
        chunks=chunks,
        tables=tables,
        warnings=first.warnings,
        truncated=scan_trunc,
        metadata=first.metadata,
    )
    src = Path(first.path)

    if output_path is None:
        if fmt in ("docx", "xlsx"):
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT, f"target_format '{fmt}' requires output_path."
            )
        rendered = render_extracted_to_format(merged, fmt)
        return ToolResult.ok(
            "document_convert",
            f"Converted '{src.name}' ({first.doc_format}) to {fmt.upper()} preview.",
            {
                "source_path": first.path,
                "source_format": first.doc_format,
                "target_format": fmt,
                "converted_text": rendered[:MAX_RETURNED_CHARS],
                "truncated": len(rendered) > MAX_RETURNED_CHARS or scan_trunc,
                "content_trust": "untrusted_document_data",
            },
            warnings=[UNTRUSTED_DOC_WARNING],
            provenance=[build_doc_provenance(src, f"Converted preview to {fmt}")],
            status=ResultStatus.PARTIAL
            if (scan_trunc or len(rendered) > MAX_RETURNED_CHARS)
            else ResultStatus.SUCCESS,
        )

    dest = resolve_scoped_path(output_path, allowed_write_roots, must_exist=False)
    if dest == src:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT, "Conversion output_path cannot overwrite source."
        )
    if dest.exists() and not overwrite:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT, f"Target '{dest.name}' exists; set overwrite=true."
        )

    payload = {
        "source_path": str(src),
        "output_path": str(dest),
        "target_format": fmt,
        "overwrite": overwrite,
    }
    if not approval_id:
        rec = broker.prepare_action(
            action_type="document_convert_write",
            permission_level=PermissionLevel.L2_WRITE,
            payload=payload,
            summary=f"Convert '{src.name}' -> '{dest.name}' ({fmt})",
            target_path=dest,
        )
        return approval_required_result(
            "document_convert", rec, {"source_path": str(src), "target_format": fmt}
        )

    broker.consume_approved(
        approval_id, expected_action_type="document_convert_write", expected_payload=payload
    )
    backup_path = (
        str(create_timestamped_backup(dest, list(allowed_write_roots))) if dest.exists() else None
    )
    text_body = render_extracted_to_format(merged, "md" if fmt in ("docx", "xlsx") else fmt)
    headers = tables[0].headers if tables else []
    rows = tables[0].rows if tables else [[c.chunk_id, c.text[:500]] for c in chunks]
    bytes_written = write_format_to_disk(
        dest,
        fmt,
        allowed_write_roots=allowed_write_roots,
        text_body=text_body,
        title=src.stem,
        headers=headers,
        rows=rows,
        overwrite=overwrite,
    )
    return ToolResult.ok(
        tool="document_convert",
        summary=f"Converted '{src.name}' to '{dest.name}' ({bytes_written} bytes).",
        data={
            "source_path": str(src),
            "output_path": str(dest),
            "target_format": fmt,
            "bytes_written": bytes_written,
            "backup_path": backup_path,
            "approval_id": approval_id,
        },
    )
