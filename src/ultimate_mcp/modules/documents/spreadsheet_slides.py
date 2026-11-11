"""Lazy-loaded extractors for XLSX spreadsheets (formulas + values) and PPTX decks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ultimate_mcp.core.models import ErrorCode, UltimateMCPError
from ultimate_mcp.modules.documents.models import (
    DEFAULT_CHUNK_CHARS,
    DEFAULT_ROW_LIMIT,
    DEFAULT_SLIDE_BATCH,
    MAX_CSV_COLS,
    MAX_DOCX_TABLES,
    MAX_PPTX_SLIDES,
    MAX_RETURNED_CHARS,
    MAX_SHEET_COLS,
    MAX_SHEET_ROWS,
    MAX_XLSX_SHEETS,
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


def _missing_extra(pkg: str) -> UltimateMCPError:
    return UltimateMCPError(
        ErrorCode.PROVIDER_UNAVAILABLE,
        f"Optional dependency '{pkg}' is required for this document format. "
        "Install with: uv sync --extra documents",
    )


def _safe_cell_value(val: Any) -> Any:
    return val if val is None or isinstance(val, (str, int, float, bool)) else str(val)


def _trim_empty_columns(rows: list[tuple[Any, ...]]) -> int:
    width = 0
    for row in rows:
        for idx in range(len(row) - 1, -1, -1):
            if row[idx] is not None:
                width = max(width, idx + 1)
                break
    return width


def _format_xlsx_row(
    f_row: tuple[Any, ...], v_row: tuple[Any, ...], width: int
) -> tuple[list[Any], bool]:
    out: list[Any] = []
    has_formula = False
    for idx in range(width):
        f_val = f_row[idx] if idx < len(f_row) else None
        v_val = v_row[idx] if idx < len(v_row) else None
        if isinstance(f_val, str) and f_val.startswith("="):
            has_formula = True
            out.append({"formula": f_val, "cached": _safe_cell_value(v_val)})
        else:
            out.append(_safe_cell_value(f_val))
    return out, has_formula


def extract_xlsx(
    path: Path,
    size_bytes: int,
    *,
    sheet_name: str | None = None,
    offset: int = 0,
    limit: int = DEFAULT_ROW_LIMIT,
) -> ExtractedDocument:
    """Extract XLSX sheet data preserving both formulas and cached values."""
    inspect_zip_archive_safety(path)
    try:
        import openpyxl  # type: ignore[import-untyped]
    except ImportError as exc:
        raise _missing_extra("openpyxl") from exc

    try:
        wb_f = openpyxl.load_workbook(path, read_only=True, data_only=False)
        wb_v = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT, f"Malformed XLSX '{path.name}': {exc}"
        ) from exc

    try:
        sheets = list(wb_f.sheetnames)[:MAX_XLSX_SHEETS]
        if not sheets:
            raise UltimateMCPError(ErrorCode.UNSUPPORTED_FORMAT, "Workbook contains no sheets.")
        target = sheet_name or sheets[0]
        if target not in wb_f.sheetnames:
            raise UltimateMCPError(ErrorCode.NOT_FOUND, f"Sheet '{target}' not found.")
        ws_f, ws_v = wb_f[target], wb_v[target]
        max_col = min(MAX_SHEET_COLS, ws_f.max_column or MAX_SHEET_COLS)
        all_f = list(
            ws_f.iter_rows(min_row=1, max_row=MAX_SHEET_ROWS + 1, max_col=max_col, values_only=True)
        )
        all_v = list(
            ws_v.iter_rows(min_row=1, max_row=MAX_SHEET_ROWS + 1, max_col=max_col, values_only=True)
        )
    finally:
        wb_f.close()
        wb_v.close()

    width = _trim_empty_columns(all_f)
    headers = [
        str(all_f[0][i]) if i < len(all_f[0]) and all_f[0][i] is not None else ""
        for i in range(width)
    ]
    data_f, data_v = all_f[1:], all_v[1:]
    total_rows = len(data_f)
    if offset < 0 or (offset > 0 and offset >= total_rows):
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT, f"Row offset {offset} out of range ({total_rows})."
        )

    end_idx = min(total_rows, offset + max(1, min(limit, MAX_SHEET_ROWS)))
    formatted_rows: list[list[Any]] = []
    formulas_present = False
    for r_f, r_v in zip(data_f[offset:end_idx], data_v[offset:end_idx], strict=False):
        row_out, row_formula = _format_xlsx_row(r_f, r_v, width)
        formulas_present = formulas_present or row_formula
        formatted_rows.append(row_out)

    table = ExtractedTable(
        table_index=0,
        headers=headers,
        rows=formatted_rows,
        total_rows=total_rows,
        truncated=end_idx < total_rows,
        formulas_present=formulas_present,
        location=SourceLocation(
            source_path=str(path),
            sheet=target,
            table_index=0,
            row_start=offset + 1,
            row_end=end_idx,
        ),
    )
    return ExtractedDocument(
        document_id=compute_document_id(path),
        path=str(path),
        doc_format="xlsx",
        size_bytes=size_bytes,
        total_units=total_rows,
        unit_name="row",
        start_unit=offset,
        end_unit=end_idx,
        tables=[table],
        warnings=[UNTRUSTED_DOC_WARNING],
        truncated=end_idx < total_rows,
        next_offset=end_idx if end_idx < total_rows else None,
        metadata={"sheets": sheets, "active_sheet": target, "formulas_present": formulas_present},
    )


def _extract_slide_content(
    slide: Any, slide_num: int, path: Path, tables_out: list[ExtractedTable]
) -> str:
    texts: list[str] = []
    for shape in slide.shapes:
        if getattr(shape, "has_text_frame", False) and shape.text_frame:
            txt = shape.text_frame.text.strip()
            if txt:
                texts.append(txt)
        if getattr(shape, "has_table", False) and len(tables_out) < MAX_DOCX_TABLES:
            raw = [
                [r.cells[i].text.strip() for i in range(min(len(r.cells), MAX_CSV_COLS))]
                for r in shape.table.rows
            ]
            if raw:
                tables_out.append(
                    ExtractedTable(
                        table_index=len(tables_out),
                        headers=raw[0],
                        rows=raw[1 : 1 + DEFAULT_ROW_LIMIT],
                        total_rows=max(0, len(raw) - 1),
                        truncated=len(raw) - 1 > DEFAULT_ROW_LIMIT,
                        location=SourceLocation(
                            source_path=str(path),
                            slide=slide_num,
                            table_index=len(tables_out),
                            row_start=1,
                        ),
                    )
                )
    if getattr(slide, "has_notes_slide", False) and slide.notes_slide.notes_text_frame:
        notes = slide.notes_slide.notes_text_frame.text.strip()
        if notes:
            texts.append(f"[Speaker Notes] {notes}")
    return "\n".join(texts)


def extract_pptx(
    path: Path,
    size_bytes: int,
    *,
    offset: int = 0,
    limit: int = DEFAULT_SLIDE_BATCH,
    max_chars: int = DEFAULT_CHUNK_CHARS,
) -> ExtractedDocument:
    """Extract PPTX slides, speaker notes, and tables with slide-number provenance."""
    inspect_zip_archive_safety(path)
    try:
        import pptx
    except ImportError as exc:
        raise _missing_extra("python-pptx") from exc

    try:
        prs = pptx.Presentation(str(path))
    except Exception as exc:
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT, f"Malformed PPTX '{path.name}': {exc}"
        ) from exc

    slides = list(prs.slides)[:MAX_PPTX_SLIDES]
    total = len(slides)
    if offset < 0 or (offset > 0 and offset >= total):
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT, f"Slide offset {offset} out of range ({total})."
        )

    cap = max(500, min(max_chars, MAX_RETURNED_CHARS))
    chunks: list[DocumentChunk] = []
    tables: list[ExtractedTable] = []
    used = 0
    end_idx = offset
    for idx in range(offset, min(total, offset + max(1, limit))):
        slide_text = _extract_slide_content(slides[idx], idx + 1, path, tables)
        if chunks and used + len(slide_text) > cap:
            break
        chunks.append(
            DocumentChunk(
                chunk_id=f"slide_{idx + 1}",
                text=slide_text[:cap],
                location=SourceLocation(source_path=str(path), slide=idx + 1),
            )
        )
        used += len(slide_text[:cap])
        end_idx = idx + 1
        if used >= cap:
            break

    return ExtractedDocument(
        document_id=compute_document_id(path),
        path=str(path),
        doc_format="pptx",
        size_bytes=size_bytes,
        total_units=total,
        unit_name="slide",
        start_unit=offset,
        end_unit=end_idx,
        chunks=chunks,
        tables=tables,
        warnings=[UNTRUSTED_DOC_WARNING],
        truncated=end_idx < total,
        next_offset=end_idx if end_idx < total else None,
        metadata={"slide_count": len(prs.slides)},
    )
