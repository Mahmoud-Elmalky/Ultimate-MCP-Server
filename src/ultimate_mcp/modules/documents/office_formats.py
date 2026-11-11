"""Lazy-loaded extractors for PDF and DOCX documents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ultimate_mcp.core.models import ErrorCode, UltimateMCPError
from ultimate_mcp.modules.documents.models import (
    DEFAULT_CHUNK_CHARS,
    DEFAULT_PDF_PAGE_BATCH,
    DEFAULT_ROW_LIMIT,
    MAX_CSV_COLS,
    MAX_DOCX_PARAGRAPHS,
    MAX_DOCX_TABLES,
    MAX_PDF_PAGES,
    MAX_RETURNED_CHARS,
    UNTRUSTED_DOC_WARNING,
    DocumentChunk,
    ExtractedDocument,
    ExtractedTable,
    SourceLocation,
)
from ultimate_mcp.modules.documents.security import (
    compute_document_id,
    inspect_zip_archive_safety,
)


def _missing_extra(pkg: str, extra: str = "documents") -> UltimateMCPError:
    return UltimateMCPError(
        ErrorCode.PROVIDER_UNAVAILABLE,
        f"Optional dependency '{pkg}' is required for this document format. "
        f"Install with: uv sync --extra {extra}",
    )


def extract_pdf(
    path: Path,
    size_bytes: int,
    *,
    offset: int = 0,
    limit: int = DEFAULT_PDF_PAGE_BATCH,
    max_chars: int = DEFAULT_CHUNK_CHARS,
) -> ExtractedDocument:
    """Extract paginated PDF pages with page-level provenance."""
    try:
        import pypdf
    except ImportError as exc:
        raise _missing_extra("pypdf") from exc

    try:
        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            raise UltimateMCPError(
                ErrorCode.UNSUPPORTED_FORMAT,
                "Encrypted or password-protected PDF documents are not supported.",
            )
        total_pages = len(reader.pages)
    except UltimateMCPError:
        raise
    except Exception as exc:
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT, f"Malformed PDF '{path.name}': {exc}"
        ) from exc

    if offset < 0 or (offset > 0 and offset >= total_pages):
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT, f"Page offset {offset} out of range ({total_pages})."
        )

    batch = max(1, min(limit, MAX_PDF_PAGES))
    char_cap = max(500, min(max_chars, MAX_RETURNED_CHARS))
    chunks: list[DocumentChunk] = []
    used_chars = 0
    end_idx = offset
    for idx in range(offset, min(total_pages, offset + batch)):
        try:
            page_text = (reader.pages[idx].extract_text() or "").strip()
        except Exception as exc:
            raise UltimateMCPError(
                ErrorCode.UNSUPPORTED_FORMAT, f"PDF page {idx + 1} parse error: {exc}"
            ) from exc
        if chunks and used_chars + len(page_text) > char_cap:
            break
        slice_text = page_text[:char_cap] if len(page_text) > char_cap else page_text
        chunks.append(
            DocumentChunk(
                chunk_id=f"page_{idx + 1}",
                text=slice_text,
                location=SourceLocation(source_path=str(path), page=idx + 1),
            )
        )
        used_chars += len(slice_text)
        end_idx = idx + 1
        if used_chars >= char_cap:
            break

    return ExtractedDocument(
        document_id=compute_document_id(path),
        path=str(path),
        doc_format="pdf",
        size_bytes=size_bytes,
        total_units=total_pages,
        unit_name="page",
        start_unit=offset,
        end_unit=end_idx,
        chunks=chunks,
        warnings=[UNTRUSTED_DOC_WARNING],
        truncated=end_idx < total_pages,
        next_offset=end_idx if end_idx < total_pages else None,
        metadata={"page_count": total_pages, "capped_page_limit": MAX_PDF_PAGES},
    )


def _extract_docx_tables(doc: Any, path: Path) -> list[ExtractedTable]:
    tables: list[ExtractedTable] = []
    for t_idx, tbl in enumerate(doc.tables[:MAX_DOCX_TABLES]):
        raw_rows = [[c.text.strip() for c in row.cells[:MAX_CSV_COLS]] for row in tbl.rows]
        if not raw_rows:
            continue
        headers = raw_rows[0]
        data_rows = raw_rows[1 : 1 + DEFAULT_ROW_LIMIT]
        tables.append(
            ExtractedTable(
                table_index=t_idx,
                headers=headers,
                rows=data_rows,
                total_rows=max(0, len(raw_rows) - 1),
                truncated=max(0, len(raw_rows) - 1) > DEFAULT_ROW_LIMIT,
                location=SourceLocation(source_path=str(path), table_index=t_idx, row_start=1),
            )
        )
    return tables


def extract_docx(
    path: Path,
    size_bytes: int,
    *,
    offset: int = 0,
    limit: int = 200,
    max_chars: int = DEFAULT_CHUNK_CHARS,
) -> ExtractedDocument:
    """Extract paginated DOCX paragraphs and preserved tables."""
    inspect_zip_archive_safety(path)
    try:
        import docx
    except ImportError as exc:
        raise _missing_extra("python-docx") from exc

    try:
        doc = docx.Document(str(path))
    except Exception as exc:
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT, f"Malformed DOCX '{path.name}': {exc}"
        ) from exc

    paras = [
        (i, p.text.strip())
        for i, p in enumerate(doc.paragraphs[:MAX_DOCX_PARAGRAPHS])
        if p.text.strip()
    ]
    total = len(paras)
    if offset < 0 or (offset > 0 and offset >= total):
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT, f"Paragraph offset {offset} out of range ({total})."
        )

    cap = max(500, min(max_chars, MAX_RETURNED_CHARS))
    end_idx = offset
    used = 0
    chunks: list[DocumentChunk] = []
    for idx in range(offset, min(total, offset + max(1, limit))):
        p_idx, text = paras[idx]
        if chunks and used + len(text) > cap:
            break
        chunks.append(
            DocumentChunk(
                chunk_id=f"para_{p_idx}",
                text=text[:cap],
                location=SourceLocation(source_path=str(path), paragraph_index=p_idx),
            )
        )
        used += len(text[:cap])
        end_idx = idx + 1
        if used >= cap:
            break

    tables = _extract_docx_tables(doc, path)
    return ExtractedDocument(
        document_id=compute_document_id(path),
        path=str(path),
        doc_format="docx",
        size_bytes=size_bytes,
        total_units=total,
        unit_name="paragraph",
        start_unit=offset,
        end_unit=end_idx,
        chunks=chunks,
        tables=tables,
        warnings=[UNTRUSTED_DOC_WARNING],
        truncated=end_idx < total,
        next_offset=end_idx if end_idx < total else None,
        metadata={"paragraph_count": total, "table_count": len(doc.tables)},
    )
