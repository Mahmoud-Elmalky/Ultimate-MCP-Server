"""Bounded two-document diff comparison service with dual provenance."""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from pathlib import Path

from ultimate_mcp.core.models import ResultStatus, ToolResult
from ultimate_mcp.modules.documents.analysis import collect_all_bounded
from ultimate_mcp.modules.documents.models import (
    UNTRUSTED_DOC_WARNING,
    DocumentChunk,
    ExtractedTable,
)
from ultimate_mcp.modules.documents.security import build_doc_provenance


def _doc_text_lines(chunks: list[DocumentChunk], tables: list[ExtractedTable]) -> list[str]:
    lines: list[str] = []
    for chunk in chunks:
        lines.extend(chunk.text.splitlines())
    for tbl in tables:
        lines.append(" | ".join(tbl.headers))
        for row in tbl.rows:
            lines.append(" | ".join(str(c) for c in row))
    return lines


def document_compare_service(
    allowed_roots: Sequence[Path],
    *,
    path_a: str,
    path_b: str,
    max_diff_lines: int = 80,
) -> ToolResult:
    """Compare two workspace documents cleanly with bounded unified diff and structural delta."""
    doc_a, chunks_a, tables_a, trunc_a = collect_all_bounded(path_a, allowed_roots)
    doc_b, chunks_b, tables_b, trunc_b = collect_all_bounded(path_b, allowed_roots)
    lines_a = _doc_text_lines(chunks_a, tables_a)
    lines_b = _doc_text_lines(chunks_b, tables_b)

    cap = max(10, min(max_diff_lines, 200))
    diff_iter = difflib.unified_diff(
        lines_a, lines_b, fromfile=Path(doc_a.path).name, tofile=Path(doc_b.path).name, lineterm=""
    )
    diff_lines = list(diff_iter)
    diff_truncated = len(diff_lines) > cap
    identical = not diff_lines
    return ToolResult.ok(
        "document_compare",
        (
            f"Documents '{Path(doc_a.path).name}' and '{Path(doc_b.path).name}' are "
            + ("identical" if identical else f"different ({len(diff_lines)} diff lines).")
        ),
        {
            "identical": identical,
            "document_a": {
                "path": doc_a.path,
                "format": doc_a.doc_format,
                "size_bytes": doc_a.size_bytes,
                "total_units": doc_a.total_units,
                "table_count": len(tables_a),
            },
            "document_b": {
                "path": doc_b.path,
                "format": doc_b.doc_format,
                "size_bytes": doc_b.size_bytes,
                "total_units": doc_b.total_units,
                "table_count": len(tables_b),
            },
            "total_diff_lines": len(diff_lines),
            "diff_truncated": diff_truncated,
            "diff": diff_lines[:cap],
            "content_trust": "untrusted_document_data",
        },
        warnings=[UNTRUSTED_DOC_WARNING],
        provenance=[
            build_doc_provenance(Path(doc_a.path), "Comparison baseline document A"),
            build_doc_provenance(Path(doc_b.path), "Comparison target document B"),
        ],
        status=ResultStatus.PARTIAL
        if (trunc_a or trunc_b or diff_truncated)
        else ResultStatus.SUCCESS,
    )
