"""Deterministic lexical, negation, numeric, temporal, causal, and context signal extractors.

Supports conservative evidence comparison in ``workflows.py`` without hidden LLM calls.
"""

from __future__ import annotations

import re
from typing import Any

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

CAUSAL_CERTAINTY_TERMS: frozenset[str] = frozenset(
    {
        "causes",
        "caused",
        "proves",
        "proved",
        "proven",
        "guarantees",
        "guaranteed",
        "cures",
        "cured",
        "eliminates",
        "eliminated",
    }
)

HEDGED_CORRELATION_TERMS: frozenset[str] = frozenset(
    {
        "correlated",
        "correlation",
        "associated",
        "association",
        "may",
        "might",
        "could",
        "preliminary",
        "unproven",
        "hypothesized",
        "speculated",
        "inconclusive",
    }
)

TIME_PERIOD_MARKERS: frozenset[str] = frozenset(
    {
        "q1",
        "q2",
        "q3",
        "q4",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "spring",
        "summer",
        "autumn",
        "fall",
        "winter",
    }
)

POPULATION_CONTEXT_GROUPS: dict[str, frozenset[str]] = {
    "age_cohort": frozenset(
        {"adults", "adult", "children", "child", "pediatric", "infants", "elderly", "seniors"}
    ),
    "organism_model": frozenset(
        {"humans", "human", "patients", "clinical", "mice", "mouse", "rodents", "rats", "vitro"}
    ),
    "geography": frozenset(
        {"usa", "united_states", "america", "europe", "european", "uk", "britain", "asia", "japan"}
    ),
    "platform": frozenset(
        {"windows", "linux", "macos", "android", "ios", "mobile", "desktop", "server"}
    ),
}

DIRECTIONAL_VERBS: tuple[str, ...] = (
    "caused",
    "causes",
    "blocked",
    "blocks",
    "replaced",
    "replaces",
    "outperformed",
    "outperforms",
    "exceeded",
    "exceeds",
    "inhibited",
    "inhibits",
    "reduced",
    "reduces",
    "triggered",
    "triggers",
)

LEXICAL_LIMITATION_NOTE = (
    "Deterministic lexical, negation, numeric, temporal, causal-modality, and context "
    "consistency check only (zero LLM calls; not a semantic truth oracle). 'No contradiction "
    "detected' and 'Potential supporting evidence' do not prove factual truth; inspect cited "
    "primary sources and dates directly."
)


def tokenize_keywords(text: str) -> set[str]:
    """Extract lowercase alphanumeric tokens >= 3 chars excluding basic stopwords/negations."""
    words = re.findall(r"[a-zA-Z0-9]{3,}", text.lower())
    return {w for w in words if w not in STOPWORDS and w not in NEGATION_MARKERS}


def extract_signals(text: str) -> dict[str, Any]:
    """Extract negation, temporal, numeric, modality, role-order, and population signals."""
    cleaned = text.lower().replace("'", "").replace("united states", "united_states")
    ordered_words = re.findall(r"[a-z0-9_]+", cleaned)
    tokens = set(ordered_words)
    years = sorted(set(re.findall(r"\b(?:19\d{2}|20\d{2})\b", cleaned)))
    iso_dates = sorted(set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", cleaned)))
    raw_nums = re.findall(r"\b\d+(?:\.\d+)?%?", cleaned)
    numbers = sorted({n for n in raw_nums if n not in years})

    contexts: dict[str, list[str]] = {}
    for group_name, group_terms in POPULATION_CONTEXT_GROUPS.items():
        matched = sorted(tokens.intersection(group_terms))
        if matched:
            contexts[group_name] = matched

    verb_frames: list[tuple[str, str, str]] = []
    content_words = [
        w
        for w in ordered_words
        if len(w) >= 3 and w not in STOPWORDS and w not in NEGATION_MARKERS
    ]
    for idx, word in enumerate(content_words):
        if word in DIRECTIONAL_VERBS and 0 < idx < len(content_words) - 1:
            verb_frames.append((content_words[idx - 1], word.rstrip("s"), content_words[idx + 1]))

    return {
        "negations": sorted(tokens.intersection(NEGATION_MARKERS)),
        "years": years,
        "dates": iso_dates,
        "periods": sorted(tokens.intersection(TIME_PERIOD_MARKERS)),
        "numbers": numbers,
        "positive_polarity": sorted(tokens.intersection(POSITIVE_POLARITY_TERMS)),
        "negative_polarity": sorted(tokens.intersection(NEGATIVE_POLARITY_TERMS)),
        "causal_certainty": sorted(tokens.intersection(CAUSAL_CERTAINTY_TERMS)),
        "hedged_correlation": sorted(tokens.intersection(HEDGED_CORRELATION_TERMS)),
        "contexts": contexts,
        "verb_frames": verb_frames,
    }


def detect_context_divergences(sig_a: dict[str, Any], sig_b: dict[str, Any]) -> list[str]:
    """Detect when two texts on the same topic apply to disjoint populations or contexts."""
    divergences: list[str] = []
    ctx_a: dict[str, list[str]] = sig_a.get("contexts", {})
    ctx_b: dict[str, list[str]] = sig_b.get("contexts", {})
    for group_name in sorted(set(ctx_a).intersection(ctx_b)):
        set_a = set(ctx_a[group_name])
        set_b = set(ctx_b[group_name])
        if set_a and set_b and not set_a.intersection(set_b):
            divergences.append(
                f"population_or_context_mismatch:{group_name} ({sorted(set_a)} vs {sorted(set_b)})"
            )
    return divergences


def detect_pairwise_conflicts(sig_a: dict[str, Any], sig_b: dict[str, Any]) -> list[str]:
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
    if (sig_a["causal_certainty"] and sig_b["hedged_correlation"]) or (
        sig_a["hedged_correlation"] and sig_b["causal_certainty"]
    ):
        conflicts.append("causal_vs_correlational_meaning_mismatch")

    for subj_a, verb_a, obj_a in sig_a.get("verb_frames", []):
        for subj_b, verb_b, obj_b in sig_b.get("verb_frames", []):
            if verb_a == verb_b and subj_a == obj_b and obj_a == subj_b and subj_a != obj_a:
                conflicts.append(
                    f"semantic_role_inversion ({subj_a}->{verb_a}->{obj_a} vs "
                    f"{subj_b}->{verb_b}->{obj_b})"
                )

    if sig_a["dates"] and sig_b["dates"] and not set(sig_a["dates"]).intersection(sig_b["dates"]):
        conflicts.append(f"contradictory_dates ({sig_a['dates']} vs {sig_b['dates']})")
    elif (
        sig_a["years"] and sig_b["years"] and not set(sig_a["years"]).intersection(sig_b["years"])
    ):
        conflicts.append(f"contradictory_years ({sig_a['years']} vs {sig_b['years']})")
    if (
        sig_a["periods"]
        and sig_b["periods"]
        and not set(sig_a["periods"]).intersection(sig_b["periods"])
    ):
        conflicts.append(f"different_time_periods ({sig_a['periods']} vs {sig_b['periods']})")
    if (
        sig_a["numbers"]
        and sig_b["numbers"]
        and not set(sig_a["numbers"]).intersection(sig_b["numbers"])
    ):
        conflicts.append(f"conflicting_numeric_values ({sig_a['numbers']} vs {sig_b['numbers']})")
    return conflicts
