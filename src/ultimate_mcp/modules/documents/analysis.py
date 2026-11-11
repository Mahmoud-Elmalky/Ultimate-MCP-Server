"""Deterministic bounded document search, evidence retrieval QA, and diff comparison."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ultimate_mcp.core.models import ErrorCode, ResultStatus, ToolResult, UltimateMCPError
from ultimate_mcp.modules.documents.models import (
    UNTRUSTED_DOC_WARNING,
    DocumentChunk,
    ExtractedDocument,
    ExtractedTable,
)
from ultimate_mcp.modules.documents.security import build_doc_provenance
from ultimate_mcp.modules.documents.service import extract_document_bounded

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    }
)
_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_MAX_SCAN_BATCHES = 12


def collect_all_bounded(
    path: str, allowed_roots: Sequence[Path], *, sheet_name: str | None = None
) -> tuple[ExtractedDocument, list[DocumentChunk], list[ExtractedTable], bool]:
    first = extract_document_bounded(path, allowed_roots, offset=0, sheet_name=sheet_name)
    chunks = list(first.chunks)
    tables = list(first.tables)
    cur_next = first.next_offset
    batches = 1
    scan_truncated = False
    while cur_next is not None and batches < _MAX_SCAN_BATCHES:
        nxt = extract_document_bounded(path, allowed_roots, offset=cur_next, sheet_name=sheet_name)
        chunks.extend(nxt.chunks)
        if nxt.tables and not first.tables:
            tables.extend(nxt.tables)
        cur_next = nxt.next_offset
        batches += 1
    if cur_next is not None:
        scan_truncated = True
    return first, chunks, tables, scan_truncated


def _table_rows_as_text(table: ExtractedTable) -> list[tuple[str, dict[str, Any]]]:
    entries: list[tuple[str, dict[str, Any]]] = []
    loc_base = table.location.to_dict()
    if table.headers:
        entries.append(("Headers: " + " | ".join(table.headers), {**loc_base, "row": 0}))
    for idx, row in enumerate(table.rows, start=table.location.row_start or 1):
        entries.append((" | ".join(str(c) for c in row), {**loc_base, "row": idx}))
    return entries


def _snippet_around(text: str, query_lower: str, radius: int = 120) -> str:
    pos = text.lower().find(query_lower)
    if pos < 0:
        return text[: radius * 2]
    start = max(0, pos - radius)
    end = min(len(text), pos + len(query_lower) + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def document_search_service(
    allowed_roots: Sequence[Path],
    *,
    path: str,
    query: str,
    case_sensitive: bool = False,
    max_matches: int = 10,
    sheet_name: str | None = None,
) -> ToolResult:
    """Search document chunks and table rows returning bounded snippets with provenance."""
    clean_query = query.strip()
    if not clean_query:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Search query cannot be empty.")

    cap = max(1, min(max_matches, 25))
    doc, chunks, tables, scan_trunc = collect_all_bounded(
        path, allowed_roots, sheet_name=sheet_name
    )
    needle = clean_query if case_sensitive else clean_query.lower()

    matches: list[dict[str, Any]] = []
    for chunk in chunks:
        hay = chunk.text if case_sensitive else chunk.text.lower()
        if needle in hay:
            matches.append(
                {
                    "kind": "chunk",
                    "chunk_id": chunk.chunk_id,
                    "snippet": _snippet_around(chunk.text, clean_query.lower()),
                    "location": chunk.location.to_dict(),
                }
            )
            if len(matches) >= cap:
                break

    if len(matches) < cap:
        for tbl in tables:
            for row_text, loc in _table_rows_as_text(tbl):
                hay = row_text if case_sensitive else row_text.lower()
                if needle in hay:
                    matches.append(
                        {"kind": "table_row", "snippet": row_text[:300], "location": loc}
                    )
                    if len(matches) >= cap:
                        break
            if len(matches) >= cap:
                break

    resolved = Path(doc.path)
    return ToolResult.ok(
        "document_search",
        f"Found {len(matches)} match(es) for '{clean_query}' in '{resolved.name}'.",
        {
            "document_id": doc.document_id,
            "path": doc.path,
            "query": clean_query,
            "match_count": len(matches),
            "matches": matches,
            "scan_truncated": scan_trunc,
            "content_trust": "untrusted_document_data",
        },
        warnings=[UNTRUSTED_DOC_WARNING],
        provenance=[build_doc_provenance(resolved, f"Search query '{clean_query}'")],
        status=ResultStatus.PARTIAL if scan_trunc else ResultStatus.SUCCESS,
    )


def _tokenize_terms(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1}


def document_qa_service(
    allowed_roots: Sequence[Path],
    *,
    path: str,
    question: str,
    top_k: int = 5,
    sheet_name: str | None = None,
) -> ToolResult:
    """Retrieve ranked evidence passages with provenance (no internal LLM inference)."""
    clean_q = question.strip()
    if not clean_q:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Question cannot be empty.")

    terms = _tokenize_terms(clean_q) or set(_TOKEN_RE.findall(clean_q.lower()))
    if not terms:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Question contains no searchable tokens.")

    k = max(1, min(top_k, 10))
    doc, chunks, tables, scan_trunc = collect_all_bounded(
        path, allowed_roots, sheet_name=sheet_name
    )
    scored: list[tuple[float, dict[str, Any]]] = []
    q_lower = clean_q.lower()

    for chunk in chunks:
        c_terms = _tokenize_terms(chunk.text)
        overlap = terms & c_terms
        if not overlap and q_lower not in chunk.text.lower():
            continue
        score = len(overlap) / max(1, len(terms)) + (0.5 if q_lower in chunk.text.lower() else 0.0)
        scored.append(
            (
                score,
                {
                    "source_id": chunk.chunk_id,
                    "kind": "chunk",
                    "score": round(score, 3),
                    "matched_terms": sorted(overlap),
                    "excerpt": chunk.text[:900],
                    "location": chunk.location.to_dict(),
                },
            )
        )

    for tbl in tables:
        for row_text, loc in _table_rows_as_text(tbl):
            r_terms = _tokenize_terms(row_text)
            overlap = terms & r_terms
            if not overlap:
                continue
            score = len(overlap) / max(1, len(terms))
            scored.append(
                (
                    score,
                    {
                        "source_id": f"table_{tbl.table_index}_row_{loc.get('row', 0)}",
                        "kind": "table_row",
                        "score": round(score, 3),
                        "matched_terms": sorted(overlap),
                        "excerpt": row_text[:500],
                        "location": loc,
                    },
                )
            )

    scored.sort(key=lambda item: item[0], reverse=True)
    evidence = [item[1] for item in scored[:k]]
    resolved = Path(doc.path)
    return ToolResult.ok(
        "document_qa",
        f"Retrieved {len(evidence)} cited evidence passage(s) from '{resolved.name}'.",
        {
            "document_id": doc.document_id,
            "path": doc.path,
            "question": clean_q,
            "retrieval_mode": "deterministic_lexical_ranking",
            "evidence_count": len(evidence),
            "evidence": evidence,
            "scan_truncated": scan_trunc,
            "content_trust": "untrusted_document_data",
        },
        warnings=[UNTRUSTED_DOC_WARNING],
        provenance=[build_doc_provenance(resolved, "Grounded QA retrieval")],
        status=ResultStatus.PARTIAL if scan_trunc else ResultStatus.SUCCESS,
    )
