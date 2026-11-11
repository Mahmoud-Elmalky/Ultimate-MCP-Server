"""Stdlib document extractors for text, Markdown, JSON, HTML, CSV, and TSV files."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ultimate_mcp.core.models import ErrorCode, UltimateMCPError
from ultimate_mcp.modules.documents.models import (
    DEFAULT_CHUNK_CHARS,
    DEFAULT_ROW_LIMIT,
    MAX_CSV_COLS,
    MAX_CSV_ROWS,
    MAX_RETURNED_CHARS,
    UNTRUSTED_DOC_WARNING,
    DocumentChunk,
    ExtractedDocument,
    ExtractedTable,
    SourceLocation,
)
from ultimate_mcp.modules.documents.security import compute_document_id

_FORMULA_PREFIXES = ("=", "+", "-", "@")


def read_utf8_text(path: Path) -> str:
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Binary null bytes detected in text document '{path.name}'.",
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Document '{path.name}' is not valid UTF-8 text: {exc}",
        ) from exc


def paginate_text_blocks(
    path: Path,
    doc_format: str,
    size_bytes: int,
    blocks: list[tuple[int, int, str]],
    *,
    offset: int,
    max_chars: int,
    tables: list[ExtractedTable] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ExtractedDocument:
    capped_chars = max(500, min(max_chars, MAX_RETURNED_CHARS))
    total = len(blocks)
    if offset < 0 or (offset > 0 and offset >= total):
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Offset {offset} out of bounds for {total} segments.",
        )

    chunks: list[DocumentChunk] = []
    used_chars = 0
    end_idx = offset
    for idx in range(offset, total):
        seg_idx, char_off, text = blocks[idx]
        if chunks and used_chars + len(text) > capped_chars:
            break
        slice_text = text[:capped_chars] if not chunks and len(text) > capped_chars else text
        chunks.append(
            DocumentChunk(
                chunk_id=f"chunk_{seg_idx}",
                text=slice_text,
                location=SourceLocation(
                    source_path=str(path),
                    paragraph_index=seg_idx,
                    char_offset=char_off,
                ),
            )
        )
        used_chars += len(slice_text)
        end_idx = idx + 1
        if used_chars >= capped_chars:
            break

    truncated = end_idx < total or any(len(b[2]) > capped_chars for b in blocks[offset:end_idx])
    return ExtractedDocument(
        document_id=compute_document_id(path),
        path=str(path),
        doc_format=doc_format,
        size_bytes=size_bytes,
        total_units=total,
        unit_name="segment",
        start_unit=offset,
        end_unit=end_idx,
        chunks=chunks,
        tables=tables or [],
        warnings=[UNTRUSTED_DOC_WARNING],
        truncated=truncated,
        next_offset=end_idx if end_idx < total else None,
        metadata=metadata or {},
    )


def _split_text_to_segments(
    text: str, segment_chars: int = DEFAULT_CHUNK_CHARS
) -> list[tuple[int, int, str]]:
    if not text.strip():
        return [(0, 0, "")]
    segments: list[tuple[int, int, str]] = []
    pos = 0
    seg_idx = 0
    step = max(500, min(segment_chars, DEFAULT_CHUNK_CHARS))
    while pos < len(text):
        end = min(len(text), pos + step)
        if end < len(text):
            nl = text.rfind("\n", pos, end)
            if nl > pos + step // 2:
                end = nl + 1
        segments.append((seg_idx, pos, text[pos:end]))
        pos = end
        seg_idx += 1
    return segments


def extract_plain_or_json(
    path: Path, size_bytes: int, *, offset: int = 0, max_chars: int = DEFAULT_CHUNK_CHARS
) -> ExtractedDocument:
    """Extract UTF-8 `.txt`, `.md`, or validated `.json` document into bounded segments."""
    suffix = path.suffix.lower()
    raw_text = read_utf8_text(path)
    meta: dict[str, Any] = {"char_count": len(raw_text), "line_count": raw_text.count("\n") + 1}
    doc_format = suffix.lstrip(".") or "txt"
    text_for_chunks = raw_text
    if suffix == ".json":
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise UltimateMCPError(
                ErrorCode.UNSUPPORTED_FORMAT,
                f"Malformed JSON in '{path.name}': {exc}",
            ) from exc
        meta["json_type"] = type(parsed).__name__
        text_for_chunks = json.dumps(parsed, indent=2, ensure_ascii=False)
    segments = _split_text_to_segments(text_for_chunks)
    return paginate_text_blocks(
        path, doc_format, size_bytes, segments, offset=offset, max_chars=max_chars, metadata=meta
    )


def extract_csv_or_tsv(
    path: Path, size_bytes: int, *, offset: int = 0, limit: int = DEFAULT_ROW_LIMIT
) -> ExtractedDocument:
    """Extract `.csv` or `.tsv` as a bounded structured table (never prose-flattened)."""
    raw_text = read_utf8_text(path)
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    try:
        rows = list(csv.reader(raw_text.splitlines(), delimiter=delimiter))
    except csv.Error as exc:
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT, f"Malformed CSV '{path.name}': {exc}"
        ) from exc

    if not rows:
        return ExtractedDocument(
            document_id=compute_document_id(path),
            path=str(path),
            doc_format=path.suffix.lower().lstrip("."),
            size_bytes=size_bytes,
            total_units=0,
            unit_name="row",
            start_unit=0,
            end_unit=0,
            warnings=[UNTRUSTED_DOC_WARNING],
        )

    headers = [str(h) for h in rows[0][:MAX_CSV_COLS]]
    data_rows = rows[1:]
    total_rows = min(len(data_rows), MAX_CSV_ROWS)
    capped_limit = max(1, min(limit, MAX_CSV_ROWS))
    if offset < 0 or (offset > 0 and offset >= total_rows):
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT, f"Row offset {offset} out of range ({total_rows})."
        )

    page_rows = [
        r[:MAX_CSV_COLS] for r in data_rows[offset : min(total_rows, offset + capped_limit)]
    ]
    formulas_present = any(
        isinstance(cell, str) and cell.startswith(_FORMULA_PREFIXES)
        for row in page_rows
        for cell in row
    )
    end_row = offset + len(page_rows)
    table = ExtractedTable(
        table_index=0,
        headers=headers,
        rows=page_rows,
        total_rows=len(data_rows),
        truncated=(end_row < len(data_rows)) or len(rows[0]) > MAX_CSV_COLS,
        location=SourceLocation(
            source_path=str(path), table_index=0, row_start=offset + 1, row_end=end_row
        ),
        formulas_present=formulas_present,
    )
    warnings = [UNTRUSTED_DOC_WARNING]
    if formulas_present:
        warnings.append(
            "CSV contains formula-prefixed cells (=, +, -, @); treated as untrusted text."
        )
    return ExtractedDocument(
        document_id=compute_document_id(path),
        path=str(path),
        doc_format=path.suffix.lower().lstrip("."),
        size_bytes=size_bytes,
        total_units=len(data_rows),
        unit_name="row",
        start_unit=offset,
        end_unit=end_row,
        tables=[table],
        warnings=warnings,
        truncated=end_row < len(data_rows),
        next_offset=end_row if end_row < len(data_rows) else None,
        metadata={"column_count": len(rows[0]), "formula_like_cells_present": formulas_present},
    )
