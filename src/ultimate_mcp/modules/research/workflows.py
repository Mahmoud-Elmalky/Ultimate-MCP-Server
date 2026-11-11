"""Deterministic evidence workflows (Phase 02 §D, ADR-0005 — no hidden LLM calls)."""

from __future__ import annotations

import re
import urllib.parse
from datetime import UTC, datetime
from typing import Any

from ultimate_mcp.core.models import (
    ErrorCode,
    Provenance,
    ResultStatus,
    ToolResult,
    UltimateMCPError,
)
from ultimate_mcp.modules.research.search_service import (
    multi_search_service,
    normalize_url_for_dedup,
)
from ultimate_mcp.providers.base import SearchProvider

STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "are",
        "was",
        "were",
        "have",
        "has",
        "had",
        "not",
        "but",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "would",
        "could",
        "should",
        "into",
        "about",
        "over",
        "after",
        "before",
    }
)


def tokenize_keywords(text: str) -> set[str]:
    """Extract lowercase alphanumeric tokens >= 3 chars excluding basic stopwords."""
    words = re.findall(r"[a-zA-Z0-9]{3,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


def compare_sources_service(sources: list[dict[str, str]]) -> ToolResult:
    """Compare supplied source records deterministically while retaining source IDs/URLs."""
    if not isinstance(sources, list) or len(sources) < 2 or len(sources) > 10:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            "compare_sources requires between 2 and 10 source objects.",
        )

    normalized: list[dict[str, Any]] = []
    token_sets: list[set[str]] = []
    provenance: list[Provenance] = []
    now_iso = datetime.now(UTC).isoformat()

    for idx, src in enumerate(sources):
        if not isinstance(src, dict):
            raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Each source must be an object.")
        sid = str(src.get("id") or f"src_{idx + 1}").strip()
        url = str(src.get("url") or "").strip() or None
        title = str(src.get("title") or sid).strip()
        content = str(src.get("content") or src.get("snippet") or "").strip()
        if not content:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                f"Source '{sid}' is missing 'content' or 'snippet'.",
            )
        toks = tokenize_keywords(f"{title} {content}")
        token_sets.append(toks)
        normalized.append(
            {
                "source_id": sid,
                "title": title,
                "url": url,
                "char_length": len(content),
                "keyword_count": len(toks),
            }
        )
        provenance.append(
            Provenance(
                provider="supplied_source",
                retrieved_at=now_iso,
                source_url=url,
                source_id=sid,
                untrusted_content=True,
            )
        )

    shared_terms = sorted(set.intersection(*token_sets))[:30] if token_sets else []
    per_source_unique: dict[str, list[str]] = {}
    for idx, info in enumerate(normalized):
        others = set().union(*(token_sets[j] for j in range(len(token_sets)) if j != idx))
        per_source_unique[info["source_id"]] = sorted(token_sets[idx] - others)[:15]

    return ToolResult.ok(
        tool="compare_sources",
        summary=(
            f"Compared {len(normalized)} sources; found {len(shared_terms)} shared key terms."
        ),
        data={
            "sources": normalized,
            "shared_terms": shared_terms,
            "unique_terms_by_source": per_source_unique,
        },
        provenance=provenance,
    )


def verify_claim_service(
    claim: str,
    *,
    providers: list[SearchProvider],
    max_results_per_provider: int = 4,
) -> ToolResult:
    """Gather independent sources for ``claim`` and report lexical overlap and uncertainty.

    Does NOT act as a truth oracle; the host model must inspect the cited evidence.
    """
    cleaned = claim.strip()
    if len(cleaned) < 5 or len(cleaned) > 500:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            "Claim must be between 5 and 500 characters.",
        )

    multi_res = multi_search_service(
        cleaned,
        providers=providers,
        search_type="general",
        max_results_per_provider=max_results_per_provider,
    )
    claim_tokens = tokenize_keywords(cleaned)
    raw_results: list[dict[str, Any]] = multi_res.data.get("results", [])

    evidence_items: list[dict[str, Any]] = []
    domains: set[str] = set()
    high_overlap_count = 0

    for idx, row in enumerate(raw_results):
        url = str(row.get("url") or "")
        host = (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
        if host:
            domains.add(host)
        row_tokens = tokenize_keywords(f"{row.get('title', '')} {row.get('snippet', '')}")
        overlap = sorted(claim_tokens.intersection(row_tokens))
        ratio = round(len(overlap) / max(1, len(claim_tokens)), 3)
        if ratio >= 0.5:
            high_overlap_count += 1
        evidence_items.append(
            {
                "evidence_id": f"ev_{idx + 1}",
                "title": row.get("title"),
                "url": url,
                "domain": host,
                "provider": row.get("provider"),
                "snippet": row.get("snippet"),
                "published_at": row.get("published_at"),
                "claim_term_overlap_ratio": ratio,
                "matched_claim_terms": overlap,
            }
        )

    if len(domains) >= 2 and high_overlap_count >= 2:
        strength = "multiple_independent_matches"
    elif high_overlap_count >= 1:
        strength = "single_or_partial_match"
    else:
        strength = "insufficient_evidence"

    return ToolResult.ok(
        tool="verify_claim",
        summary=(
            f"Gathered {len(evidence_items)} source(s) across {len(domains)} independent "
            f"domain(s); evidence_strength='{strength}'."
        ),
        data={
            "claim": cleaned,
            "evidence_strength": strength,
            "independent_domain_count": len(domains),
            "high_overlap_source_count": high_overlap_count,
            "uncertainty_note": (
                "Deterministic lexical evidence gathering only (not a semantic truth "
                "oracle). Verify snippets and dates directly before concluding."
            ),
            "evidence": evidence_items,
        },
        warnings=multi_res.warnings,
        provenance=multi_res.provenance,
        status=multi_res.status,
    )


def deep_research_service(
    topic: str,
    *,
    sub_queries: list[str] | None = None,
    providers: list[SearchProvider],
    max_queries: int = 3,
    max_results_per_query: int = 4,
) -> ToolResult:
    """Execute a bounded, deterministic multi-query research pass without hidden LLM loops."""
    cleaned_topic = topic.strip()
    if len(cleaned_topic) < 3 or len(cleaned_topic) > 500:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            "Research topic must be between 3 and 500 characters.",
        )

    capped_queries = max(1, min(max_queries, 4))
    queries = [cleaned_topic]
    for sq in sub_queries or []:
        sq_clean = sq.strip()
        if sq_clean and sq_clean not in queries and len(queries) < capped_queries:
            queries.append(sq_clean)

    all_findings: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    provenance: list[Provenance] = []
    warnings: list[str] = []

    for q in queries:
        step = multi_search_service(
            q,
            providers=providers,
            max_results_per_provider=max(1, min(max_results_per_query, 5)),
        )
        warnings.extend(step.warnings)
        for row in step.data.get("results", []):
            norm = normalize_url_for_dedup(str(row.get("url") or ""))
            if norm in seen_urls:
                continue
            seen_urls.add(norm)
            source_id = f"src_{len(all_findings) + 1}"
            all_findings.append(
                {
                    "source_id": source_id,
                    "query": q,
                    "title": row.get("title"),
                    "url": row.get("url"),
                    "snippet": row.get("snippet"),
                    "provider": row.get("provider"),
                    "published_at": row.get("published_at"),
                }
            )
        provenance.extend(step.provenance)

    status = ResultStatus.PARTIAL if warnings else ResultStatus.SUCCESS
    return ToolResult.ok(
        tool="deep_research",
        summary=(
            f"Completed bounded research across {len(queries)} query/queries; "
            f"collected {len(all_findings)} unique source(s)."
        ),
        data={
            "topic": cleaned_topic,
            "queries_executed": queries,
            "total_unique_sources": len(all_findings),
            "sources": all_findings,
        },
        warnings=warnings,
        provenance=provenance,
        status=status,
    )


def research_report_service(
    topic: str,
    findings: list[dict[str, Any]],
    *,
    uncertainties: list[str] | None = None,
) -> ToolResult:
    """Assemble supplied findings and source citations into a structured Markdown report."""
    cleaned_topic = topic.strip()
    if not cleaned_topic:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Report topic must not be empty.")
    if not findings or len(findings) > 25:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            "findings must contain between 1 and 25 source/claim items.",
        )

    now_iso = datetime.now(UTC).isoformat()
    lines: list[str] = [f"# Research Report: {cleaned_topic}", "", "## Evidence & Findings", ""]
    citations: list[dict[str, str | None]] = []
    provenance: list[Provenance] = []

    for idx, item in enumerate(findings):
        if not isinstance(item, dict):
            raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Each finding must be an object.")
        sid = str(item.get("source_id") or item.get("id") or f"S{idx + 1}").strip()
        title = str(item.get("title") or sid).strip()
        url = str(item.get("url") or "").strip()
        claim = str(item.get("claim") or item.get("snippet") or "").strip()
        if not url and not claim:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                f"Finding '{sid}' must include a 'claim'/'snippet' or 'url'.",
            )
        cite_str = f"[{sid}]({url})" if url else f"[{sid}]"
        lines.append(f"- **{title}** ({cite_str}): {claim}")
        citations.append({"source_id": sid, "title": title, "url": url or None})
        provenance.append(
            Provenance(
                provider="assembled_report",
                retrieved_at=now_iso,
                source_url=url or None,
                source_id=sid,
                untrusted_content=True,
            )
        )

    lines.extend(["", "## Uncertainties & Limitations", ""])
    if uncertainties:
        for unc in uncertainties:
            if unc.strip():
                lines.append(f"- {unc.strip()}")
    else:
        lines.append(
            "- Findings are assembled from external search snippets and have not been "
            "independently audited beyond the cited URLs."
        )

    markdown = "\n".join(lines)
    return ToolResult.ok(
        tool="research_report",
        summary=(
            f"Assembled research report for '{cleaned_topic}' with {len(citations)} citation(s)."
        ),
        data={
            "topic": cleaned_topic,
            "markdown": markdown,
            "citations": citations,
        },
        provenance=provenance,
    )
