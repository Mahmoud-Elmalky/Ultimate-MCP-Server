"""Bounded document rendering and atomic backup-preserving file writers."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ultimate_mcp.core.filesystem import atomic_write_text, resolve_scoped_path
from ultimate_mcp.core.models import ErrorCode, UltimateMCPError
from ultimate_mcp.modules.documents.models import ExtractedDocument
from ultimate_mcp.modules.documents.security import inspect_zip_archive_safety

CREATABLE_FORMATS: frozenset[str] = frozenset({"txt", "md", "json", "csv", "docx", "xlsx"})


def render_extracted_to_format(doc: ExtractedDocument, target_fmt: str) -> str:
    """Render an extracted document cleanly into ``txt``, ``md``, ``json``, or ``csv``."""
    fmt = target_fmt.lower().lstrip(".")
    if fmt == "json":
        return json.dumps(doc.to_dict(), indent=2, ensure_ascii=False)
    if fmt == "csv":
        if not doc.tables:
            raise UltimateMCPError(
                ErrorCode.UNSUPPORTED_FORMAT,
                f"Document '{Path(doc.path).name}' has no structured tables to export as CSV.",
            )
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        tbl = doc.tables[0]
        if tbl.headers:
            writer.writerow(tbl.headers)
        for row in tbl.rows:
            writer.writerow(
                [c.get("cached") if isinstance(c, dict) and "cached" in c else c for c in row]
            )
        return buf.getvalue()
    if fmt in ("txt", "md"):
        lines: list[str] = []
        if fmt == "md":
            lines.append(f"# {Path(doc.path).name}")
            lines.append("")
        for chunk in doc.chunks:
            loc = chunk.location.to_dict()
            tag = (
                f"Page {loc['page']}"
                if "page" in loc
                else f"Slide {loc['slide']}"
                if "slide" in loc
                else chunk.chunk_id
            )
            lines.append(f"## {tag}" if fmt == "md" else f"[{tag}]")
            lines.append(chunk.text)
            lines.append("")
        for tbl in doc.tables:
            if fmt == "md" and tbl.headers:
                lines.append("| " + " | ".join(tbl.headers) + " |")
                lines.append("| " + " | ".join("---" for _ in tbl.headers) + " |")
                for row in tbl.rows:
                    lines.append("| " + " | ".join(str(c) for c in row) + " |")
                lines.append("")
            else:
                if tbl.headers:
                    lines.append(" | ".join(tbl.headers))
                for row in tbl.rows:
                    lines.append(" | ".join(str(c) for c in row))
                lines.append("")
        return "\n".join(lines).strip() + "\n"
    raise UltimateMCPError(
        ErrorCode.UNSUPPORTED_FORMAT, f"Unsupported conversion format '{target_fmt}'."
    )


def write_docx_file(
    dest: Path, *, title: str | None, paragraphs: list[str], tables: list[list[Any]]
) -> int:
    """Create a `.docx` file inside workspace via python-docx."""
    try:
        import docx
    except ImportError as exc:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Optional dependency 'python-docx' is required to write DOCX files.",
        ) from exc

    document = docx.Document()
    if title:
        document.add_heading(title, level=1)
    for para in paragraphs:
        document.add_paragraph(para)
    if tables:
        col_count = max(len(r) for r in tables)
        tbl = document.add_table(rows=len(tables), cols=col_count)
        for r_idx, row in enumerate(tables):
            for c_idx in range(col_count):
                val = row[c_idx] if c_idx < len(row) else ""
                tbl.rows[r_idx].cells[c_idx].text = str(val)
    dest.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(dest))
    return dest.stat().st_size


def write_xlsx_file(
    dest: Path, *, sheet_name: str, headers: list[str], rows: list[list[Any]]
) -> int:
    """Create a `.xlsx` file inside workspace via openpyxl."""
    try:
        import openpyxl  # type: ignore[import-untyped]
    except ImportError as exc:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Optional dependency 'openpyxl' is required to write XLSX files.",
        ) from exc

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = (sheet_name or "Sheet1")[:31]
    if headers:
        ws.append(headers)
    for row in rows:
        ws.append(row)
    dest.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(dest))
    wb.close()
    return dest.stat().st_size


def create_timestamped_backup(
    source: Path, allowed_write_roots: tuple[Path, ...] | list[Path]
) -> Path:
    """Copy ``source`` to a timestamped ``.bak`` sibling before applying an in-place edit."""
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    candidate = source.with_name(f"{source.name}.{stamp}.bak")
    resolved_bak = resolve_scoped_path(candidate, allowed_write_roots, must_exist=False)
    counter = 1
    while resolved_bak.exists():
        resolved_bak = resolve_scoped_path(
            source.with_name(f"{source.name}.{stamp}.{counter}.bak"),
            allowed_write_roots,
            must_exist=False,
        )
        counter += 1
    resolved_bak.write_bytes(source.read_bytes())
    return resolved_bak


def _apply_docx_edit(
    src: Path, dest: Path, *, mode: str, target_text: str | None, replacement_text: str | None
) -> int:
    inspect_zip_archive_safety(src)
    try:
        import docx
    except ImportError as exc:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Optional dependency 'python-docx' is required to edit DOCX files.",
        ) from exc
    document = docx.Document(str(src))
    if mode == "append":
        document.add_paragraph(replacement_text or "")
    elif mode == "replace_text":
        if not target_text:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT, "target_text required for replace_text."
            )
        replaced = False
        for para in document.paragraphs:
            if target_text in para.text:
                para.text = para.text.replace(target_text, replacement_text or "")
                replaced = True
        if not replaced:
            raise UltimateMCPError(ErrorCode.NOT_FOUND, f"Target text not found in '{src.name}'.")
    else:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "append_row is only valid for CSV files.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(dest))
    return dest.stat().st_size


def apply_document_edit(
    src: Path,
    dest: Path,
    *,
    suffix: str,
    allowed_write_roots: Sequence[Path],
    mode: str,
    target_text: str | None,
    replacement_text: str | None,
    row_values: list[str] | None,
) -> int:
    """Apply bounded text/CSV/DOCX edit and persist atomically to ``dest``."""
    if suffix == ".docx":
        return _apply_docx_edit(
            src, dest, mode=mode, target_text=target_text, replacement_text=replacement_text
        )

    raw = src.read_text(encoding="utf-8")
    if mode == "append_row":
        if suffix != ".csv" or not row_values:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT, "append_row requires .csv file and row_values."
            )
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerow(row_values)
        updated = (raw.rstrip("\n") + "\n" + buf.getvalue()) if raw.strip() else buf.getvalue()
    elif mode == "append":
        sep = "" if (not raw or raw.endswith("\n")) else "\n"
        updated = raw + sep + (replacement_text or "")
    else:
        if not target_text or target_text not in raw:
            raise UltimateMCPError(ErrorCode.NOT_FOUND, "target_text not found in document.")
        updated = raw.replace(target_text, replacement_text or "")
        if suffix == ".json":
            try:
                json.loads(updated)
            except json.JSONDecodeError as exc:
                raise UltimateMCPError(
                    ErrorCode.INVALID_INPUT, f"Edit produces invalid JSON: {exc}"
                ) from exc
    atomic_write_text(dest, updated, allowed_write_roots, overwrite=True)
    return dest.stat().st_size
