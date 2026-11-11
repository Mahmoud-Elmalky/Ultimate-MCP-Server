"""Stdlib HTML document extractor stripping scripts/styles and preserving tables."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from ultimate_mcp.modules.documents.models import (
    DEFAULT_CHUNK_CHARS,
    DEFAULT_ROW_LIMIT,
    MAX_CSV_COLS,
    ExtractedDocument,
    ExtractedTable,
    SourceLocation,
)
from ultimate_mcp.modules.documents.text_formats import (
    paginate_text_blocks,
    read_utf8_text,
)


class _HtmlDocParser(HTMLParser):
    """Strip scripts/styles while preserving readable blocks and HTML tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._block_buf: list[str] = []
        self.blocks: list[str] = []
        self.tables: list[list[list[str]]] = []
        self._cur_table: list[list[str]] | None = None
        self._cur_row: list[str] | None = None
        self._cur_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript"):
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if t == "table":
            self._flush_block()
            self._cur_table = []
        elif t == "tr" and self._cur_table is not None:
            self._cur_row = []
        elif t in ("td", "th") and self._cur_row is not None:
            self._cur_cell = []
        elif t in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "blockquote"):
            self._flush_block()

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript") and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if t in ("td", "th") and self._cur_cell is not None and self._cur_row is not None:
            self._cur_row.append(" ".join("".join(self._cur_cell).split()))
            self._cur_cell = None
        elif t == "tr" and self._cur_row is not None and self._cur_table is not None:
            if any(cell for cell in self._cur_row):
                self._cur_table.append(self._cur_row)
            self._cur_row = None
        elif t == "table" and self._cur_table is not None:
            if self._cur_table:
                self.tables.append(self._cur_table)
            self._cur_table = None
        elif t in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "blockquote"):
            self._flush_block()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._cur_cell is not None:
            self._cur_cell.append(data)
        elif self._cur_table is None:
            self._block_buf.append(data)

    def _flush_block(self) -> None:
        text = " ".join("".join(self._block_buf).split())
        self._block_buf.clear()
        if text:
            self.blocks.append(text)

    def finalize(self) -> None:
        self._flush_block()


def extract_html(
    path: Path, size_bytes: int, *, offset: int = 0, max_chars: int = DEFAULT_CHUNK_CHARS
) -> ExtractedDocument:
    """Extract visible text blocks and tables from `.html`/`.htm` without script/style noise."""
    raw_text = read_utf8_text(path)
    parser = _HtmlDocParser()
    parser.feed(raw_text)
    parser.close()
    parser.finalize()

    segments: list[tuple[int, int, str]] = []
    cur_off = 0
    for idx, block in enumerate(parser.blocks):
        segments.append((idx, cur_off, block))
        cur_off += len(block) + 1
    if not segments:
        segments = [(0, 0, "")]

    tables: list[ExtractedTable] = []
    for t_idx, raw_rows in enumerate(parser.tables[:20]):
        headers = [str(c) for c in raw_rows[0][:MAX_CSV_COLS]]
        data_rows = [
            [str(c) for c in r[:MAX_CSV_COLS]] for r in raw_rows[1 : 1 + DEFAULT_ROW_LIMIT]
        ]
        tables.append(
            ExtractedTable(
                table_index=t_idx,
                headers=headers,
                rows=data_rows,
                total_rows=max(0, len(raw_rows) - 1),
                truncated=len(raw_rows) - 1 > DEFAULT_ROW_LIMIT,
                location=SourceLocation(source_path=str(path), table_index=t_idx, row_start=1),
            )
        )

    return paginate_text_blocks(
        path,
        "html",
        size_bytes,
        segments,
        offset=offset,
        max_chars=max_chars,
        tables=tables,
        metadata={"block_count": len(parser.blocks), "table_count": len(parser.tables)},
    )
