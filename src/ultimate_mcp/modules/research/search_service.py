"""Web, news, official documentation, academic, and multi-provider search services."""

from __future__ import annotations

import urllib.parse
from dataclasses import asdict
from typing import Any

from ultimate_mcp.core.cache import CacheDataClass, PerClassTTLCache, build_cache_key
from ultimate_mcp.core.models import (
    ErrorCode,
    Provenance,
    ResultStatus,
    ToolResult,
    UltimateMCPError,
)
from ultimate_mcp.providers.base import SearchProvider, SearchResultItem


def normalize_url_for_dedup(url: str) -> str:
    """Normalize a URL for deterministic deduplication across providers."""
    parsed = urllib.parse.urlsplit(url.strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    return f"{host}{path}?{parsed.query}" if parsed.query else f"{host}{path}"


def items_to_provenance(
    items: list[SearchResultItem],
    *,
    freshness_note: str | None = None,
) -> list[Provenance]:
    """Convert normalized search items into `Provenance` records."""
    return [
        Provenance(
            provider=it.provider,
            retrieved_at=it.retrieved_at,
            source_url=it.url,
            source_id=it.source_id,
            published_at=it.published_at,
            freshness_note=freshness_note,
            untrusted_content=True,
        )
        for it in items
    ]


def web_search_service(
    query: str,
    *,
    primary_provider: SearchProvider,
    fallback_provider: SearchProvider | None = None,
    max_results: int = 5,
    cache: PerClassTTLCache[Any] | None = None,
) -> ToolResult:
    """Execute primary web search with visible, attributed fallback if primary is unavailable."""
    cache_key = build_cache_key(
        CacheDataClass.WEB_SEARCH,
        primary_provider.name,
        query,
        params={"max_results": max_results},
    )
    if cache is not None:
        hit = cache.get(cache_key)
        if hit is not None:
            cached_items: list[SearchResultItem] = hit.value
            return ToolResult.ok(
                tool="web_search",
                summary=(
                    f"Returned {len(cached_items)} cached search result(s) from "
                    f"'{hit.provider}'."
                ),
                data={
                    "query": query.strip(),
                    "provider": hit.provider,
                    "cached": True,
                    "results": [asdict(it) for it in cached_items],
                },
                provenance=items_to_provenance(
                    cached_items,
                    freshness_note=hit.freshness_note(),
                ),
            )

    warnings: list[str] = []
    used_provider = primary_provider.name
    try:
        items = primary_provider.search(query, max_results=max_results, search_type="general")
    except UltimateMCPError as primary_exc:
        if fallback_provider is None:
            raise
        warnings.append(
            f"Primary provider '{primary_provider.name}' unavailable "
            f"([{primary_exc.code.value}] {primary_exc.message}); fell back to "
            f"'{fallback_provider.name}'."
        )
        items = fallback_provider.search(query, max_results=max_results, search_type="general")
        used_provider = fallback_provider.name

    if cache is not None and used_provider == primary_provider.name:
        cache.put(cache_key, CacheDataClass.WEB_SEARCH, used_provider, items)

    status = ResultStatus.PARTIAL if warnings else ResultStatus.SUCCESS
    return ToolResult.ok(
        tool="web_search",
        summary=f"Found {len(items)} web result(s) via '{used_provider}'.",
        data={
            "query": query.strip(),
            "provider": used_provider,
            "cached": False,
            "coverage_note": (
                "General web search results are ranked excerpts, not exhaustive or "
                "guaranteed authoritative verification."
            ),
            "results": [asdict(it) for it in items],
        },
        warnings=warnings,
        provenance=items_to_provenance(items),
        status=status,
    )


def search_news_service(
    query: str,
    *,
    providers: list[SearchProvider],
    max_results: int = 5,
    cache: PerClassTTLCache[Any] | None = None,
) -> ToolResult:
    """Search dated news items across configured news-capable search providers."""
    if not providers:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "No news search providers are available.",
        )

    warnings: list[str] = []
    for prov in providers:
        cache_key = build_cache_key(
            CacheDataClass.NEWS_SEARCH,
            prov.name,
            query,
            params={"max_results": max_results},
        )
        if cache is not None:
            hit = cache.get(cache_key)
            if hit is not None:
                cached_items: list[SearchResultItem] = hit.value
                return ToolResult.ok(
                    tool="search_news",
                    summary=f"Returned {len(cached_items)} cached news item(s) from '{prov.name}'.",
                    data={
                        "query": query.strip(),
                        "provider": prov.name,
                        "cached": True,
                        "results": [asdict(it) for it in cached_items],
                    },
                    warnings=warnings,
                    provenance=items_to_provenance(
                        cached_items,
                        freshness_note=hit.freshness_note(),
                    ),
                )
        try:
            items = prov.search(query, max_results=max_results, search_type="news")
            if cache is not None:
                cache.put(cache_key, CacheDataClass.NEWS_SEARCH, prov.name, items)
            return ToolResult.ok(
                tool="search_news",
                summary=f"Found {len(items)} news item(s) via '{prov.name}'.",
                data={
                    "query": query.strip(),
                    "provider": prov.name,
                    "cached": False,
                    "results": [asdict(it) for it in items],
                },
                warnings=warnings,
                provenance=items_to_provenance(
                    items,
                    freshness_note="Publication dates are distinct from retrieval timestamps.",
                ),
                status=ResultStatus.PARTIAL if warnings else ResultStatus.SUCCESS,
            )
        except UltimateMCPError as exc:
            warnings.append(f"News provider '{prov.name}' failed: [{exc.code.value}] {exc.message}")

    raise UltimateMCPError(
        ErrorCode.PROVIDER_UNAVAILABLE,
        "All configured news search providers failed.",
        details={"warnings": warnings},
    )


def search_official_docs_service(
    query: str,
    *,
    domain: str | None = None,
    provider: SearchProvider,
    max_results: int = 5,
) -> ToolResult:
    """Search official documentation (optionally scoped to a specific vendor domain)."""
    scoped_query = f"{query.strip()} official documentation"
    if domain and domain.strip():
        clean_dom = (
            domain.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
        )
        scoped_query = f"site:{clean_dom} {query.strip()}"

    items = provider.search(scoped_query, max_results=max_results, search_type="general")
    return ToolResult.ok(
        tool="search_official_docs",
        summary=f"Found {len(items)} documentation result(s) via '{provider.name}'.",
        data={
            "query": query.strip(),
            "effective_query": scoped_query,
            "domain": domain,
            "provider": provider.name,
            "results": [asdict(it) for it in items],
        },
        provenance=items_to_provenance(items),
    )


def search_academic_service(
    query: str,
    *,
    provider: SearchProvider,
    max_results: int = 5,
    cache: PerClassTTLCache[Any] | None = None,
) -> ToolResult:
    """Search scholarly literature via the configured academic provider (OpenAlex)."""
    cache_key = build_cache_key(
        CacheDataClass.ACADEMIC_SEARCH,
        provider.name,
        query,
        params={"max_results": max_results},
    )
    if cache is not None:
        hit = cache.get(cache_key)
        if hit is not None:
            cached_items: list[SearchResultItem] = hit.value
            return ToolResult.ok(
                tool="search_academic",
                summary=(
                    f"Returned {len(cached_items)} cached scholarly work(s) from "
                    f"'{provider.name}'."
                ),
                data={
                    "query": query.strip(),
                    "provider": provider.name,
                    "cached": True,
                    "results": [asdict(it) for it in cached_items],
                },
                provenance=items_to_provenance(
                    cached_items,
                    freshness_note=hit.freshness_note(),
                ),
            )

    items = provider.search(query, max_results=max_results, search_type="academic")
    if cache is not None:
        cache.put(cache_key, CacheDataClass.ACADEMIC_SEARCH, provider.name, items)

    return ToolResult.ok(
        tool="search_academic",
        summary=f"Found {len(items)} scholarly work(s) via '{provider.name}'.",
        data={
            "query": query.strip(),
            "provider": provider.name,
            "cached": False,
            "coverage_note": "Scholarly metadata retrieved from OpenAlex index.",
            "results": [asdict(it) for it in items],
        },
        provenance=items_to_provenance(items),
    )


def multi_search_service(
    query: str,
    *,
    providers: list[SearchProvider],
    search_type: str = "general",
    max_results_per_provider: int = 4,
) -> ToolResult:
    """Query multiple search providers, deduplicate by URL, and preserve per-result provider."""
    if not providers:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "No search providers are configured for multi_search.",
        )

    merged: list[dict[str, Any]] = []
    seen_urls: dict[str, int] = {}
    provenance: list[Provenance] = []
    warnings: list[str] = []
    succeeded_providers: list[str] = []

    for prov in providers:
        try:
            items = prov.search(
                query,
                max_results=max_results_per_provider,
                search_type=search_type,
            )
            succeeded_providers.append(prov.name)
        except UltimateMCPError as exc:
            warnings.append(f"Provider '{prov.name}' failed ([{exc.code.value}] {exc.message}).")
            continue

        for it in items:
            norm = normalize_url_for_dedup(it.url)
            if norm in seen_urls:
                existing_idx = seen_urls[norm]
                existing_provs: list[str] = merged[existing_idx]["providers"]
                if it.provider not in existing_provs:
                    existing_provs.append(it.provider)
                continue
            seen_urls[norm] = len(merged)
            merged.append(
                {
                    "title": it.title,
                    "url": it.url,
                    "snippet": it.snippet,
                    "provider": it.provider,
                    "providers": [it.provider],
                    "rank": len(merged) + 1,
                    "published_at": it.published_at,
                    "retrieved_at": it.retrieved_at,
                }
            )
            provenance.append(
                Provenance(
                    provider=it.provider,
                    retrieved_at=it.retrieved_at,
                    source_url=it.url,
                    published_at=it.published_at,
                    untrusted_content=True,
                )
            )

    if not succeeded_providers:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "All search providers in multi_search failed.",
            details={"warnings": warnings},
        )

    status = ResultStatus.PARTIAL if warnings else ResultStatus.SUCCESS
    return ToolResult.ok(
        tool="multi_search",
        summary=(
            f"Merged {len(merged)} unique result(s) across {', '.join(succeeded_providers)}."
        ),
        data={
            "query": query.strip(),
            "search_type": search_type,
            "providers_succeeded": succeeded_providers,
            "results": merged,
        },
        warnings=warnings,
        provenance=provenance,
        status=status,
    )


def legacy_limited_web_search_service(
    query: str,
    search_type: str = "general",
    *,
    provider: SearchProvider,
    cache: PerClassTTLCache[Any] | None = None,
) -> ToolResult:
    """Compatibility wrapper for legacy `limited_web_search` without silent keyword overrides."""
    if search_type == "news":
        res = search_news_service(query, providers=[provider], max_results=5, cache=cache)
    elif search_type == "general":
        res = web_search_service(
            query,
            primary_provider=provider,
            max_results=5,
            cache=cache,
        )
    else:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Unsupported search_type '{search_type}'. Expected 'general' or 'news'.",
        )
    res.warnings.append(
        "Deprecated tool 'limited_web_search': prefer 'web_search', 'search_news', "
        "or 'multi_search'."
    )
    return res
