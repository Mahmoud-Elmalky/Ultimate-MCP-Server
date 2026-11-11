"""Deterministic evidence workflows (Phase 02 §D, ADR-0005 — no hidden LLM calls).

Provides conservative lexical, negation, numeric, and date/year consistency checks for
``compare_sources`` and ``verify_claim`` without declaring claims factually true from token
overlap alone.
"""

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

NEGATION_MARKERS: frozenset[str] = frozenset(
    {
        "not",
        "never",
        "no",
        "none",
        "false",
        "untrue",
        "incorrect",
        "debunked",
        "myth",
        "hoax",
        "fake",
        "fabricated",
        "fails",
        "failed",
        "cannot",
        "cant",
        "wont",
        "doesnt",
        "isnt",
        "wasnt",
        "werent",
        "without",
        "refuted",
        "disproved",
        "contrary",
        "denies",
        "denied",
    }
)

POSITIVE_POLARITY_TERMS: frozenset[str] = frozenset(
    {
        "increase",
        "increased",
        "increases",
        "improve",
        "improves",
        "improved",
        "faster",
        "higher",
        "grew",
        "rose",
        "supported",
        "enabled",
        "safe",
    }
)

NEGATIVE_POLARITY_TERMS: frozenset[str] = frozenset(
    {
        "decrease",
        "decreased",
        "decreases",
        "degrade",
        "degrades",
        "degraded",
        "slower",
        "lower",
        "shrank",
        "fell",
        "unsupported",
        "disabled",
        "unsafe",
        "regression",
    }
)

LEXICAL_LIMITATION_NOTE = (
    "Deterministic lexical, negation, numeric, and date consistency check only (zero LLM "
    "calls; not a semantic truth oracle). High keyword overlap does not prove factual truth; "
    "inspect cited primary snippets and publication dates directly."
)


def tokenize_keywords(text: str) -> set[str]:
    """Extract lowercase alphanumeric tokens >= 3 chars excluding basic stopwords."""
    words = re.findall(r"[a-zA-Z0-9]{3,}", text.lower())
    return {w for w in words if w not in STOPWORDS and w not in NEGATION_MARKERS}


def _extract_signals(text: str) -> dict[str, Any]:
    """Extract negation cues, years/dates, non-year numbers, and directional polarity."""
    cleaned = text.lower().replace("'", "")
    tokens = set(re.findall(r"[a-z0-9]+", cleaned))
    negations = sorted(tokens.intersection(NEGATION_MARKERS))
    years = sorted(set(re.findall(r"\b(?:19\d{2}|20\d{2})\b", cleaned)))
    iso_dates = sorted(set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", cleaned)))
    raw_nums = re.findall(r"\b\d+(?:\.\d+)?%?", cleaned)
    numbers = sorted({n for n in raw_nums if n not in years})
    pos_hits = sorted(tokens.intersection(POSITIVE_POLARITY_TERMS))
    neg_hits = sorted(tokens.intersection(NEGATIVE_POLARITY_TERMS))
    return {
        "negations": negations,
        "years": years,
        "dates": iso_dates,
        "numbers": numbers,
        "positive_polarity": pos_hits,
        "negative_polarity": neg_hits,
    }


def _detect_pairwise_conflicts(sig_a: dict[str, Any], sig_b: dict[str, Any]) -> list[str]:
    """Return conservative conflict reasons between two signal dictionaries on a shared topic."""
    conflicts: list[str] = []
    if bool(sig_a["negations"]) != bool(sig_b["negations"]):
        conflicts.append(
            f"negation_mismatch ({sig_a['negations'] or ['affirmative']} vs "
            f"{sig_b['negations'] or ['affirmative']})"
        )
    if (sig_a["positive_polarity"] and sig_b["negative_polarity"]) or (
        sig_a["negative_polarity"] and sig_b["positive_polarity"]
    ):
        conflicts.append("directional_polarity_conflict")
    if sig_a["dates"] and sig_b["dates"] and not set(sig_a["dates"]).intersection(sig_b["dates"]):
        conflicts.append(f"contradictory_dates ({sig_a['dates']} vs {sig_b['dates']})")
    elif (
        sig_a["years"] and sig_b["years"] and not set(sig_a["years"]).intersection(sig_b["years"])
    ):
        conflicts.append(f"contradictory_years ({sig_a['years']} vs {sig_b['years']})")
    if (
        sig_a["numbers"]
        and sig_b["numbers"]
        and not set(sig_a["numbers"]).intersection(sig_b["numbers"])
    ):
        conflicts.append(f"conflicting_numeric_values ({sig_a['numbers']} vs {sig_b['numbers']})")
    return conflicts


def compare_sources_service(sources: list[dict[str, str]]) -> ToolResult:
    """Compare supplied source records deterministically with conflict detection."""
    if not isinstance(sources, list) or len(sources) < 2 or len(sources) > 10:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            "compare_sources requires between 2 and 10 source objects.",
        )

    normalized: list[dict[str, Any]] = []
    token_sets: list[set[str]] = []
    signals_list: list[dict[str, Any]] = []
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
        combined_text = f"{title} {content}"
        toks = tokenize_keywords(combined_text)
        sig = _extract_signals(combined_text)
        token_sets.append(toks)
        signals_list.append(sig)
        normalized.append(
            {
                "source_id": sid,
                "title": title,
                "url": url,
                "char_length": len(content),
                "keyword_count": len(toks),
                "signals": sig,
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

    conflicts_detected: list[dict[str, Any]] = []
    for i in range(len(normalized)):
        for j in range(i + 1, len(normalized)):
            pair_shared = token_sets[i].intersection(token_sets[j])
            if len(pair_shared) >= 1:
                reasons = _detect_pairwise_conflicts(signals_list[i], signals_list[j])
                if reasons:
                    conflicts_detected.append(
                        {
                            "source_a": normalized[i]["source_id"],
                            "source_b": normalized[j]["source_id"],
                            "shared_topic_terms": sorted(pair_shared)[:10],
                            "conflict_reasons": reasons,
                        }
                    )

    if conflicts_detected:
        comparison_status = "Sources disagree"
    elif len(shared_terms) >= 2:
        comparison_status = "Sources agree"
    else:
        comparison_status = "Insufficient evidence"

    return ToolResult.ok(
        tool="compare_sources",
        summary=(
            f"Compared {len(normalized)} sources: status='{comparison_status}', "
            f"shared_terms={len(shared_terms)}, conflicts={len(conflicts_detected)}."
        ),
        data={
            "comparison_status": comparison_status,
            "sources": normalized,
            "shared_terms": shared_terms,
            "unique_terms_by_source": per_source_unique,
            "conflicts_detected": conflicts_detected,
            "verification_methodology": "deterministic_lexical_and_signal_comparison",
            "uncertainty_note": LEXICAL_LIMITATION_NOTE,
        },
        provenance=provenance,
    )


def verify_claim_service(
    claim: str,
    *,
    providers: list[SearchProvider],
    max_results_per_provider: int = 4,
) -> ToolResult:
    """Gather independent sources for ``claim`` and classify supporting/conflicting evidence."""
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
    claim_signals = _extract_signals(cleaned)
    raw_results: list[dict[str, Any]] = multi_res.data.get("results", [])

    evidence_items: list[dict[str, Any]] = []
    domains: set[str] = set()
    supporting_domains: set[str] = set()
    supporting_count = 0
    conflicting_count = 0

    for idx, row in enumerate(raw_results):
        url = str(row.get("url") or "")
        host = (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
        if host:
            domains.add(host)
        combined = f"{row.get('title', '')} {row.get('snippet', '')}"
        row_tokens = tokenize_keywords(combined)
        row_signals = _extract_signals(combined)
        overlap = sorted(claim_tokens.intersection(row_tokens))
        ratio = round(len(overlap) / max(1, len(claim_tokens)), 3)

        conflicts = _detect_pairwise_conflicts(claim_signals, row_signals) if overlap else []
        if ratio >= 0.4 and conflicts:
            stance = "conflicting"
            conflicting_count += 1
        elif ratio >= 0.5 and not conflicts:
            stance = "supporting"
            supporting_count += 1
            if host:
                supporting_domains.add(host)
        else:
            stance = "insufficient"

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
                "stance": stance,
                "conflict_reasons": conflicts,
            }
        )

    if conflicting_count > 0:
        verification_status = "Conflicting evidence found"
        strength = "conflicting_evidence"
    elif supporting_count >= 1:
        verification_status = "Supporting evidence found"
        strength = (
            "multiple_independent_matches"
            if len(supporting_domains) >= 2 and supporting_count >= 2
            else "single_or_partial_match"
        )
    else:
        verification_status = "Insufficient evidence"
        strength = "insufficient_evidence"

    return ToolResult.ok(
        tool="verify_claim",
        summary=(
            f"Gathered {len(evidence_items)} source(s) across {len(domains)} domain(s): "
            f"status='{verification_status}', supporting={supporting_count}, "
            f"conflicting={conflicting_count}."
        ),
        data={
            "claim": cleaned,
            "verification_status": verification_status,
            "evidence_strength": strength,
            "independent_domain_count": len(domains),
            "high_overlap_source_count": supporting_count,
            "supporting_source_count": supporting_count,
            "conflicting_source_count": conflicting_count,
            "verification_methodology": "deterministic_lexical_negation_numeric_check",
            "uncertainty_note": LEXICAL_LIMITATION_NOTE,
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
            all_findings.append(
                {
                    "source_id": f"src_{len(all_findings) + 1}",
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
        lines.append(f"- {LEXICAL_LIMITATION_NOTE}")

    return ToolResult.ok(
        tool="research_report",
        summary=(
            f"Assembled research report for '{cleaned_topic}' with {len(citations)} citation(s)."
        ),
        data={
            "topic": cleaned_topic,
            "markdown": "\n".join(lines),
            "citations": citations,
        },
        provenance=provenance,
    )
