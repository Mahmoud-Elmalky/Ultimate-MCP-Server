"""Unified bounded document dispatcher and read/table extraction services."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ultimate_mcp.core.models import (
    ErrorCode,
    ResultStatus,
    ToolResult,
    UltimateMCPError,
)
from ultimate_mcp.modules.documents.html_formats import extract_html
from ultimate_mcp.modules.documents.models import (
    DEFAULT_CHUNK_CHARS,
    DEFAULT_PDF_PAGE_BATCH,
    DEFAULT_ROW_LIMIT,
    DEFAULT_SLIDE_BATCH,
    MAX_DOC_FILE_BYTES,
    UNTRUSTED_DOC_WARNING,
    ExtractedDocument,
)
from ultimate_mcp.modules.documents.office_formats import extract_docx, extract_pdf
from ultimate_mcp.modules.documents.security import build_doc_provenance, validate_document_file
from ultimate_mcp.modules.documents.spreadsheet_slides import extract_pptx, extract_xlsx
from ultimate_mcp.modules.documents.text_formats import (
    extract_csv_or_tsv,
    extract_plain_or_json,
)

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".json",
        ".html",
        ".htm",
        ".csv",
        ".tsv",
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
    }
)


def extract_document_bounded(
    file_path: str | Path,
    allowed_roots: Sequence[Path],
    *,
    offset: int = 0,
    limit: int | None = None,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    sheet_name: str | None = None,
    max_bytes: int = MAX_DOC_FILE_BYTES,
) -> ExtractedDocument:
    """Validate path/size and extract bounded chunks and/or structured tables."""
    resolved, size_bytes = validate_document_file(file_path, allowed_roots, max_bytes=max_bytes)
    suffix = resolved.suffix.lower()
    if suffix in (".txt", ".md", ".markdown", ".json"):
        return extract_plain_or_json(resolved, size_bytes, offset=offset, max_chars=max_chars)
    if suffix in (".html", ".htm"):
        return extract_html(resolved, size_bytes, offset=offset, max_chars=max_chars)
    if suffix in (".csv", ".tsv"):
        row_limit = limit if limit is not None else DEFAULT_ROW_LIMIT
        return extract_csv_or_tsv(resolved, size_bytes, offset=offset, limit=row_limit)
    if suffix == ".pdf":
        page_limit = limit if limit is not None else DEFAULT_PDF_PAGE_BATCH
        return extract_pdf(
            resolved, size_bytes, offset=offset, limit=page_limit, max_chars=max_chars
        )
    if suffix == ".docx":
        para_limit = limit if limit is not None else 200
        return extract_docx(
            resolved, size_bytes, offset=offset, limit=para_limit, max_chars=max_chars
        )
    if suffix == ".xlsx":
        row_limit = limit if limit is not None else DEFAULT_ROW_LIMIT
        return extract_xlsx(
            resolved, size_bytes, sheet_name=sheet_name, offset=offset, limit=row_limit
        )
    if suffix == ".pptx":
        slide_limit = limit if limit is not None else DEFAULT_SLIDE_BATCH
        return extract_pptx(
            resolved, size_bytes, offset=offset, limit=slide_limit, max_chars=max_chars
        )
    raise UltimateMCPError(
        ErrorCode.UNSUPPORTED_FORMAT,
        f"Unsupported document extension '{suffix or '<none>'}'. "
        f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}.",
    )


def document_read_service(
    allowed_roots: Sequence[Path],
    *,
    path: str,
    offset: int = 0,
    limit: int | None = None,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    sheet_name: str | None = None,
) -> ToolResult:
    """Read a workspace document with pagination, structural provenance, and untrusted tagging."""
    doc = extract_document_bounded(
        path,
        allowed_roots,
        offset=offset,
        limit=limit,
        max_chars=max_chars,
        sheet_name=sheet_name,
    )
    resolved_path = Path(doc.path)
    summary = (
        f"Read {doc.doc_format.upper()} '{resolved_path.name}' "
        f"({doc.unit_name}s {doc.start_unit}-{doc.end_unit} of {doc.total_units}, "
        f"{len(doc.chunks)} chunks, {len(doc.tables)} tables)."
    )
    status = ResultStatus.PARTIAL if doc.truncated else ResultStatus.SUCCESS
    return ToolResult.ok(
        "document_read",
        summary,
        doc.to_dict(),
        warnings=doc.warnings,
        provenance=[build_doc_provenance(resolved_path, f"Read {doc.doc_format} document")],
        status=status,
    )


def document_extract_tables_service(
    allowed_roots: Sequence[Path],
    *,
    path: str,
    sheet_name: str | None = None,
    table_index: int | None = None,
    offset: int = 0,
    limit: int = DEFAULT_ROW_LIMIT,
) -> ToolResult:
    """Extract structured tables from CSV, TSV, XLSX, DOCX, PPTX, or HTML documents."""
    doc = extract_document_bounded(
        path,
        allowed_roots,
        offset=offset,
        limit=limit,
        sheet_name=sheet_name,
    )
    tables = doc.tables
    if table_index is not None:
        tables = [t for t in tables if t.table_index == table_index]
        if not tables:
            raise UltimateMCPError(
                ErrorCode.NOT_FOUND,
                f"Table index {table_index} not found in '{Path(doc.path).name}'.",
            )

    payload: dict[str, Any] = {
        "document_id": doc.document_id,
        "path": doc.path,
        "format": doc.doc_format,
        "table_count": len(tables),
        "tables": [t.to_dict() for t in tables],
        "metadata": doc.metadata,
        "truncated": doc.truncated,
        "next_offset": doc.next_offset,
        "content_trust": "untrusted_document_data",
    }
    resolved_path = Path(doc.path)
    return ToolResult.ok(
        "document_extract_tables",
        f"Extracted {len(tables)} structured table(s) from '{resolved_path.name}'.",
        payload,
        warnings=doc.warnings or [UNTRUSTED_DOC_WARNING],
        provenance=[build_doc_provenance(resolved_path, "Extracted structured tables")],
        status=ResultStatus.PARTIAL if doc.truncated else ResultStatus.SUCCESS,
    )
