"""Tavily, DuckDuckGo (`ddgs`), and OpenAlex scholarly search provider adapters.

Follows ``phases/PHASE-02_RESEARCH_UTILITIES.md`` §B:
- Lazy initialization: API keys and optional packages are validated only when a provider is
  invoked, never at module import or server startup.
- Removes the legacy keyword heuristic that forced news results when `search_type="general"`.
- Rejects malformed or non-HTTP(S) result URLs and caps result count and snippet length.
"""

from __future__ import annotations

import importlib
import json
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ultimate_mcp.core.http import fetch_safe_url
from ultimate_mcp.core.models import ErrorCode, UltimateMCPError
from ultimate_mcp.core.registry import is_package_available
from ultimate_mcp.providers.base import ProviderHealth, SearchResultItem

MAX_SNIPPET_CHARS = 800
MAX_RESULTS_CAP = 10


def _is_valid_http_url(url: str) -> bool:
    cleaned = url.strip()
    if not cleaned:
        return False
    parsed = urllib.parse.urlsplit(cleaned)
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.hostname)


def _bound_snippet(text: str, max_chars: int = MAX_SNIPPET_CHARS) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars] + "…"


class TavilySearchProvider:
    """Lazy Tavily web and news search provider adapter."""

    def __init__(
        self,
        api_key: str | None = None,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip() or None
        self._client_factory = client_factory

    @property
    def name(self) -> str:
        return "tavily"

    def health(self) -> ProviderHealth:
        has_pkg = is_package_available("tavily")
        has_key = self._api_key is not None
        reasons: list[str] = []
        if not has_pkg:
            reasons.append("missing optional package: tavily-python")
        if not has_key:
            reasons.append("missing TAVILY_API_KEY")
        return ProviderHealth(
            provider=self.name,
            available=has_pkg and has_key,
            requires_api_key=True,
            api_key_configured=has_key,
            optional_packages=("tavily",),
            missing_packages=() if has_pkg else ("tavily",),
            reason="ready" if not reasons else "; ".join(reasons),
        )

    def _create_client(self) -> Any:
        if not self._api_key:
            raise UltimateMCPError(
                ErrorCode.AUTH_REQUIRED,
                "TAVILY_API_KEY is not configured. Set TAVILY_API_KEY or use "
                "DuckDuckGo search.",
            )
        if self._client_factory is not None:
            return self._client_factory(self._api_key)
        try:
            tavily_mod: Any = importlib.import_module("tavily")
        except ImportError as exc:
            raise UltimateMCPError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Optional package 'tavily-python' is not installed (install `[research]`).",
            ) from exc
        return tavily_mod.TavilyClient(api_key=self._api_key)

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        search_type: str = "general",
    ) -> list[SearchResultItem]:
        cleaned = query.strip()
        if len(cleaned) < 2 or len(cleaned) > 1000:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                "Search query must be between 2 and 1000 characters.",
            )
        capped = max(1, min(max_results, MAX_RESULTS_CAP))
        client = self._create_client()
        topic = "news" if search_type == "news" else "general"
        try:
            response = client.search(
                query=cleaned,
                search_depth="advanced",
                topic=topic,
                max_results=capped,
            )
        except UltimateMCPError:
            raise
        except Exception as exc:
            raise UltimateMCPError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"Tavily search failed: {type(exc).__name__}",
                retryable=True,
            ) from exc

        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            raise UltimateMCPError(
                ErrorCode.UPSTREAM_INVALID_RESPONSE,
                "Tavily returned a malformed response without a 'results' list.",
            )

        now_iso = datetime.now(UTC).isoformat()
        items: list[SearchResultItem] = []
        for raw in response["results"]:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            if not _is_valid_http_url(url):
                continue
            title = str(raw.get("title") or url).strip()
            snippet = _bound_snippet(str(raw.get("content") or ""))
            published = str(raw["published_date"]).strip() if raw.get("published_date") else None
            items.append(
                SearchResultItem(
                    title=title,
                    url=url,
                    snippet=snippet,
                    provider=self.name,
                    rank=len(items) + 1,
                    retrieved_at=now_iso,
                    published_at=published,
                )
            )
            if len(items) >= capped:
                break
        return items


class DuckDuckGoSearchProvider:
    """DuckDuckGo search provider supporting `ddgs` (with fallback to `duckduckgo_search`)."""

    def __init__(self, ddgs_factory: Callable[[], Any] | None = None) -> None:
        self._ddgs_factory = ddgs_factory

    @property
    def name(self) -> str:
        return "duckduckgo"

    @staticmethod
    def _find_ddgs_package() -> str | None:
        if is_package_available("ddgs"):
            return "ddgs"
        if is_package_available("duckduckgo_search"):
            return "duckduckgo_search"
        return None

    def health(self) -> ProviderHealth:
        pkg = self._find_ddgs_package()
        available = pkg is not None or self._ddgs_factory is not None
        return ProviderHealth(
            provider=self.name,
            available=available,
            requires_api_key=False,
            api_key_configured=True,
            optional_packages=("ddgs",),
            missing_packages=() if available else ("ddgs",),
            reason="ready" if available else "missing optional package: ddgs",
        )

    def _open_ddgs(self) -> Any:
        if self._ddgs_factory is not None:
            return self._ddgs_factory()
        pkg = self._find_ddgs_package()
        if pkg is None:
            raise UltimateMCPError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Optional package 'ddgs' is not installed (install `[research]`).",
            )
        mod: Any = importlib.import_module(pkg)
        return mod.DDGS()

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        search_type: str = "general",
    ) -> list[SearchResultItem]:
        cleaned = query.strip()
        if len(cleaned) < 2 or len(cleaned) > 1000:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                "Search query must be between 2 and 1000 characters.",
            )
        capped = max(1, min(max_results, MAX_RESULTS_CAP))
        now_iso = datetime.now(UTC).isoformat()
        items: list[SearchResultItem] = []

        try:
            ctx = self._open_ddgs()
            with ctx as ddgs:
                if search_type == "news":
                    raw_rows = list(ddgs.news(cleaned, max_results=capped))
                else:
                    raw_rows = list(ddgs.text(cleaned, max_results=capped))
        except UltimateMCPError:
            raise
        except Exception as exc:
            raise UltimateMCPError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"DuckDuckGo search failed: {type(exc).__name__}",
                retryable=True,
            ) from exc

        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            url = str(row.get("href") or row.get("url") or "").strip()
            if not _is_valid_http_url(url):
                continue
            title = str(row.get("title") or url).strip()
            body = _bound_snippet(str(row.get("body") or row.get("snippet") or ""))
            pub_date = str(row["date"]).strip() if row.get("date") else None
            items.append(
                SearchResultItem(
                    title=title,
                    url=url,
                    snippet=body,
                    provider=self.name,
                    rank=len(items) + 1,
                    retrieved_at=now_iso,
                    published_at=pub_date,
                )
            )
            if len(items) >= capped:
                break
        return items


def _reconstruct_inverted_abstract(inverted: Any) -> str:
    if not isinstance(inverted, dict) or not inverted:
        return ""
    words_by_pos: dict[int, str] = {}
    for word, positions in inverted.items():
        if isinstance(positions, list):
            for pos in positions:
                if isinstance(pos, int) and 0 <= pos < 5000:
                    words_by_pos[pos] = str(word)
    if not words_by_pos:
        return ""
    ordered = [words_by_pos[idx] for idx in sorted(words_by_pos)]
    return _bound_snippet(" ".join(ordered))


class OpenAlexAcademicProvider:
    """Scholarly paper search provider backed by the public OpenAlex REST API."""

    def __init__(self, json_fetcher: Callable[[str], dict[str, Any]] | None = None) -> None:
        self._fetcher = json_fetcher or self._default_fetcher

    @property
    def name(self) -> str:
        return "openalex"

    def health(self) -> ProviderHealth:
        has_httpx = is_package_available("httpx")
        return ProviderHealth(
            provider=self.name,
            available=has_httpx,
            requires_api_key=False,
            api_key_configured=True,
            optional_packages=("httpx",),
            missing_packages=() if has_httpx else ("httpx",),
            reason="ready" if has_httpx else "missing optional package: httpx",
        )

    @staticmethod
    def _default_fetcher(url: str) -> dict[str, Any]:
        resp = fetch_safe_url(
            url,
            allowed_content_types=("application/json", "text/plain"),
            max_response_bytes=1024 * 1024,
        )
        try:
            data = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise UltimateMCPError(
                ErrorCode.UPSTREAM_INVALID_RESPONSE,
                "OpenAlex returned invalid JSON.",
            ) from exc
        if not isinstance(data, dict):
            raise UltimateMCPError(
                ErrorCode.UPSTREAM_INVALID_RESPONSE,
                "OpenAlex response is not a JSON object.",
            )
        return data

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        search_type: str = "academic",
    ) -> list[SearchResultItem]:
        cleaned = query.strip()
        if len(cleaned) < 2 or len(cleaned) > 1000:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                "Academic search query must be between 2 and 1000 characters.",
            )
        capped = max(1, min(max_results, MAX_RESULTS_CAP))
        encoded_q = urllib.parse.quote(cleaned, safe="")
        url = f"https://api.openalex.org/works?search={encoded_q}&per-page={capped}"
        payload = self._fetcher(url)
        results = payload.get("results")
        if not isinstance(results, list):
            raise UltimateMCPError(
                ErrorCode.UPSTREAM_INVALID_RESPONSE,
                "OpenAlex response missing 'results' list.",
            )

        now_iso = datetime.now(UTC).isoformat()
        items: list[SearchResultItem] = []
        for work in results:
            if not isinstance(work, dict):
                continue
            work_id = str(work.get("id") or "").strip()
            doi = str(work.get("doi") or "").strip()
            target_url = doi if _is_valid_http_url(doi) else work_id
            if not _is_valid_http_url(target_url):
                continue
            title = str(work.get("display_name") or work.get("title") or target_url).strip()
            abstract = _reconstruct_inverted_abstract(work.get("abstract_inverted_index"))
            cited_by = int(work.get("cited_by_count") or 0)
            pub_date = str(work.get("publication_date") or work.get("publication_year") or "")
            authors: list[str] = []
            for auth in work.get("authorships") or []:
                if isinstance(auth, dict) and isinstance(auth.get("author"), dict):
                    a_name = str(auth["author"].get("display_name") or "").strip()
                    if a_name:
                        authors.append(a_name)
            snippet_parts = []
            if authors:
                snippet_parts.append(f"Authors: {', '.join(authors[:4])}.")
            snippet_parts.append(f"Citations: {cited_by}.")
            if abstract:
                snippet_parts.append(abstract)
            items.append(
                SearchResultItem(
                    title=title,
                    url=target_url,
                    snippet=_bound_snippet(" ".join(snippet_parts)),
                    provider=self.name,
                    rank=len(items) + 1,
                    retrieved_at=now_iso,
                    published_at=pub_date or None,
                    authors=tuple(authors[:5]),
                    source_id=work_id or None,
                )
            )
            if len(items) >= capped:
                break
        return items
