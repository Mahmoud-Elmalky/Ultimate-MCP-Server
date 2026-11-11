"""MCP tool registration for web search, extraction, RSS, and evidence workflows (Phase 02)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from mcp.types import CallToolResult
from pydantic import Field

from ultimate_mcp.core.cache import PerClassTTLCache
from ultimate_mcp.core.config import ProfileName, Settings
from ultimate_mcp.core.execution import ExecutionManager, TimeoutCategory
from ultimate_mcp.core.models import ToolResult
from ultimate_mcp.core.permissions import PermissionLevel
from ultimate_mcp.core.registry import ToolRegistry, ToolSpec
from ultimate_mcp.modules.research.search_service import (
    legacy_limited_web_search_service,
    multi_search_service,
    search_academic_service,
    search_news_service,
    search_official_docs_service,
    web_search_service,
)
from ultimate_mcp.modules.research.web_extractor import (
    crawl_website_service,
    extract_links_service,
    extract_tables_service,
    legacy_read_article_service,
    read_webpage_service,
    rss_search_service,
)
from ultimate_mcp.modules.research.workflows import (
    compare_sources_service,
    deep_research_service,
    research_report_service,
    verify_claim_service,
)
from ultimate_mcp.providers.search.adapters import (
    DuckDuckGoSearchProvider,
    OpenAlexAcademicProvider,
    TavilySearchProvider,
)

ResultAdapter = Callable[[ToolResult, Settings], CallToolResult]


def register_research_tools(
    registry: ToolRegistry,
    settings: Settings,
    executor: ExecutionManager,
    adapt_result: ResultAdapter,
    cache: PerClassTTLCache[Any],
    tavily_provider: TavilySearchProvider,
    ddg_provider: DuckDuckGoSearchProvider,
    academic_provider: OpenAlexAcademicProvider,
) -> None:
    """Register web search, content extraction, RSS, and deterministic evidence workflow tools."""

    async def web_search(
        query: Annotated[
            str,
            Field(min_length=2, max_length=400, description="Web search query."),
        ],
        max_results: Annotated[
            int,
            Field(ge=1, le=10, description="Maximum number of results to return (1-10)."),
        ] = 5,
    ) -> CallToolResult:
        """Search the web via primary provider (Tavily) with visible fallback to DuckDuckGo."""
        res = await executor.execute(
            "web_search",
            web_search_service,
            {
                "query": query,
                "primary_provider": tavily_provider,
                "fallback_provider": ddg_provider,
                "max_results": max_results,
                "cache": cache,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider=tavily_provider.name,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="web_search",
            module="research",
            description=(
                "Search the web and return ranked results with provider attribution, snippets, "
                "and retrieval timestamps."
            ),
            handler=web_search_service,
            mcp_wrapper=web_search,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset(
                {
                    ProfileName.SEARCH,
                    ProfileName.WEB_RESEARCH,
                    ProfileName.RESEARCH,
                    ProfileName.FULL,
                }
            ),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
            provider=tavily_provider.name,
        )
    )

    async def search_news(
        query: Annotated[
            str,
            Field(min_length=2, max_length=400, description="News search topic or query."),
        ],
        max_results: Annotated[
            int,
            Field(ge=1, le=10, description="Maximum news items to return (1-10)."),
        ] = 5,
    ) -> CallToolResult:
        """Search dated news items while keeping publication dates distinct from retrieval times."""
        res = await executor.execute(
            "search_news",
            search_news_service,
            {
                "query": query,
                "providers": [tavily_provider, ddg_provider],
                "max_results": max_results,
                "cache": cache,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider="news_search",
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="search_news",
            module="research",
            description=(
                "Search dated news articles and reports with publication dates and provider "
                "provenance."
            ),
            handler=search_news_service,
            mcp_wrapper=search_news,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset(
                {ProfileName.SEARCH, ProfileName.RESEARCH, ProfileName.FULL}
            ),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
        )
    )

    async def search_official_docs(
        query: Annotated[
            str,
            Field(min_length=2, max_length=400, description="Library, API, or product query."),
        ],
        domain: Annotated[
            str | None,
            Field(
                max_length=150,
                description="Optional vendor docs domain (e.g. 'docs.python.org').",
            ),
        ] = None,
        max_results: Annotated[
            int,
            Field(ge=1, le=10, description="Maximum documentation results (1-10)."),
        ] = 5,
    ) -> CallToolResult:
        """Search official vendor or library documentation (optionally scoped to a domain)."""
        active_prov = tavily_provider if settings.tavily_api_key else ddg_provider
        res = await executor.execute(
            "search_official_docs",
            search_official_docs_service,
            {
                "query": query,
                "domain": domain,
                "provider": active_prov,
                "max_results": max_results,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider=active_prov.name,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="search_official_docs",
            module="research",
            description=(
                "Search official product, framework, or library documentation, optionally scoped "
                "to a vendor domain."
            ),
            handler=search_official_docs_service,
            mcp_wrapper=search_official_docs,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset(
                {
                    ProfileName.SEARCH,
                    ProfileName.RESEARCH,
                    ProfileName.CODING,
                    ProfileName.FULL,
                }
            ),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
        )
    )

    async def search_academic(
        query: Annotated[
            str,
            Field(min_length=2, max_length=400, description="Scholarly or scientific query."),
        ],
        max_results: Annotated[
            int,
            Field(ge=1, le=10, description="Maximum scholarly works to return (1-10)."),
        ] = 5,
    ) -> CallToolResult:
        """Search peer-reviewed and scholarly metadata via OpenAlex."""
        res = await executor.execute(
            "search_academic",
            search_academic_service,
            {
                "query": query,
                "provider": academic_provider,
                "max_results": max_results,
                "cache": cache,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider=academic_provider.name,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="search_academic",
            module="research",
            description=(
                "Search scholarly papers and academic metadata via OpenAlex with publication "
                "years, DOIs/URLs, and citation counts."
            ),
            handler=search_academic_service,
            mcp_wrapper=search_academic,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset(
                {ProfileName.SEARCH, ProfileName.RESEARCH, ProfileName.FULL}
            ),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
            provider=academic_provider.name,
        )
    )

    async def multi_search(
        query: Annotated[
            str,
            Field(min_length=2, max_length=400, description="Search query across providers."),
        ],
        search_type: Annotated[
            str,
            Field(description="Search mode: 'general' or 'news'."),
        ] = "general",
        max_results_per_provider: Annotated[
            int,
            Field(ge=1, le=8, description="Max results per provider (1-8)."),
        ] = 4,
    ) -> CallToolResult:
        """Query multiple search providers, deduplicate URLs, and preserve per-item attribution."""
        res = await executor.execute(
            "multi_search",
            multi_search_service,
            {
                "query": query,
                "providers": [tavily_provider, ddg_provider],
                "search_type": search_type,
                "max_results_per_provider": max_results_per_provider,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider="multi_search",
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="multi_search",
            module="research",
            description=(
                "Query configured web providers in parallel, deduplicate URLs, and preserve "
                "per-result provider labels and partial-failure warnings."
            ),
            handler=multi_search_service,
            mcp_wrapper=multi_search,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset(
                {
                    ProfileName.SEARCH,
                    ProfileName.FACT_CHECK,
                    ProfileName.RESEARCH,
                    ProfileName.FULL,
                }
            ),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
        )
    )

    async def read_webpage(
        url: Annotated[
            str,
            Field(min_length=8, max_length=2000, description="Public http/https URL to read."),
        ],
        offset: Annotated[
            int,
            Field(ge=0, description="Character offset for reading long pages."),
        ] = 0,
        max_chars: Annotated[
            int,
            Field(ge=200, le=20000, description="Maximum characters to return in this page."),
        ] = 8000,
    ) -> CallToolResult:
        """Fetch and extract readable text from a public URL with SSRF and redirect validation."""
        res = await executor.execute(
            "read_webpage",
            read_webpage_service,
            {"url": url, "offset": offset, "max_chars": max_chars},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider="http_extractor",
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="read_webpage",
            module="research",
            description=(
                "Fetch and extract readable text, title, and canonical URL from a public webpage "
                "with strict SSRF protection and offset pagination."
            ),
            handler=read_webpage_service,
            mcp_wrapper=read_webpage,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset(
                {
                    ProfileName.WEB_RESEARCH,
                    ProfileName.FACT_CHECK,
                    ProfileName.RESEARCH,
                    ProfileName.FULL,
                }
            ),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
        )
    )

    async def extract_links(
        url: Annotated[
            str,
            Field(min_length=8, max_length=2000, description="Public http/https URL."),
        ],
        same_domain_only: Annotated[
            bool,
            Field(description="Return only links matching the page's domain."),
        ] = False,
        max_links: Annotated[
            int,
            Field(ge=1, le=200, description="Maximum links to return (1-200)."),
        ] = 50,
    ) -> CallToolResult:
        """Extract normalized http/https links from a public webpage."""
        res = await executor.execute(
            "extract_links",
            extract_links_service,
            {"url": url, "same_domain_only": same_domain_only, "max_links": max_links},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider="http_extractor",
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="extract_links",
            module="research",
            description="Extract bounded, deduplicated http/https links from a public webpage.",
            handler=extract_links_service,
            mcp_wrapper=extract_links,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset(
                {ProfileName.WEB_RESEARCH, ProfileName.RESEARCH, ProfileName.FULL}
            ),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
        )
    )

    async def extract_tables(
        url: Annotated[
            str,
            Field(min_length=8, max_length=2000, description="Public http/https URL."),
        ],
        max_tables: Annotated[
            int,
            Field(ge=1, le=10, description="Maximum tables to extract (1-10)."),
        ] = 5,
    ) -> CallToolResult:
        """Extract structured HTML tables from a public webpage."""
        res = await executor.execute(
            "extract_tables",
            extract_tables_service,
            {"url": url, "max_tables": max_tables},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider="http_extractor",
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="extract_tables",
            module="research",
            description="Extract bounded HTML tables (rows and cells) from a public webpage.",
            handler=extract_tables_service,
            mcp_wrapper=extract_tables,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset(
                {ProfileName.WEB_RESEARCH, ProfileName.RESEARCH, ProfileName.FULL}
            ),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
        )
    )

    async def crawl_website(
        start_url: Annotated[
            str,
            Field(min_length=8, max_length=2000, description="Starting public http/https URL."),
        ],
        max_pages: Annotated[
            int,
            Field(ge=1, le=5, description="Maximum pages to visit (1-5)."),
        ] = 3,
        max_depth: Annotated[
            int,
            Field(ge=0, le=2, description="Maximum link depth from start_url (0-2)."),
        ] = 1,
    ) -> CallToolResult:
        """Run a bounded same-domain BFS crawl with SSRF validation on every visited page."""
        res = await executor.execute(
            "crawl_website",
            crawl_website_service,
            {"start_url": start_url, "max_pages": max_pages, "max_depth": max_depth},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider="http_crawler",
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="crawl_website",
            module="research",
            description=(
                "Perform a strictly bounded same-domain crawl (max 5 pages, depth <= 2) with "
                "per-hop SSRF validation."
            ),
            handler=crawl_website_service,
            mcp_wrapper=crawl_website,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset(
                {ProfileName.WEB_RESEARCH, ProfileName.RESEARCH, ProfileName.FULL}
            ),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
        )
    )

    async def rss_search(
        feed_url: Annotated[
            str,
            Field(min_length=8, max_length=2000, description="Public RSS 2.0 or Atom feed URL."),
        ],
        query: Annotated[
            str | None,
            Field(max_length=200, description="Optional case-insensitive keyword filter."),
        ] = None,
        max_items: Annotated[
            int,
            Field(ge=1, le=25, description="Maximum feed items to return (1-25)."),
        ] = 10,
    ) -> CallToolResult:
        """Fetch and parse a public RSS or Atom feed with SSRF and XML entity protections."""
        res = await executor.execute(
            "rss_search",
            rss_search_service,
            {"feed_url": feed_url, "query": query, "max_items": max_items},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider="rss_atom",
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="rss_search",
            module="utilities",
            description=(
                "Read and filter items from an RSS 2.0 or Atom XML feed URL with SSRF and XXE "
                "entity protections."
            ),
            handler=rss_search_service,
            mcp_wrapper=rss_search,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset(
                {ProfileName.WEB_RESEARCH, ProfileName.RESEARCH, ProfileName.FULL}
            ),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
        )
    )

    async def compare_sources(
        sources: Annotated[
            list[dict[str, str]],
            Field(
                min_length=2,
                max_length=10,
                description="List of 2-10 source dicts with 'id', 'title', 'url', 'content'.",
            ),
        ],
    ) -> CallToolResult:
        """Compare 2-10 supplied source records deterministically while preserving source IDs."""
        res = await executor.execute(
            "compare_sources",
            compare_sources_service,
            {"sources": sources},
            permission=PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="compare_sources",
            module="research",
            description=(
                "Compare 2-10 supplied source excerpts deterministically using lexical overlap, "
                "negation, numeric, and date consistency checks ('Sources agree', "
                "'Sources disagree', or 'Insufficient evidence'; not a semantic truth oracle)."
            ),
            handler=compare_sources_service,
            mcp_wrapper=compare_sources,
            permission=PermissionLevel.L1_PREPARE,
            profiles=frozenset(
                {ProfileName.FACT_CHECK, ProfileName.RESEARCH, ProfileName.FULL}
            ),
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
            produces_provenance=True,
        )
    )

    async def verify_claim(
        claim: Annotated[
            str,
            Field(
                min_length=5,
                max_length=500,
                description="Factual claim to gather evidence for.",
            ),
        ],
        max_results_per_provider: Annotated[
            int,
            Field(ge=1, le=6, description="Max search results per provider (1-6)."),
        ] = 4,
    ) -> CallToolResult:
        """Gather independent web sources for a claim and report lexical overlap and uncertainty."""
        res = await executor.execute(
            "verify_claim",
            verify_claim_service,
            {
                "claim": claim,
                "providers": [tavily_provider, ddg_provider],
                "max_results_per_provider": max_results_per_provider,
            },
            permission=PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider="verify_claim",
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="verify_claim",
            module="research",
            description=(
                "Gather evidence across independent domains for a claim and classify status as "
                "'Supporting evidence found', 'Conflicting evidence found', or 'Insufficient "
                "evidence' via deterministic lexical/negation/numeric checks (not a semantic "
                "truth oracle)."
            ),
            handler=verify_claim_service,
            mcp_wrapper=verify_claim,
            permission=PermissionLevel.L1_PREPARE,
            profiles=frozenset(
                {ProfileName.FACT_CHECK, ProfileName.RESEARCH, ProfileName.FULL}
            ),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
        )
    )

    async def deep_research(
        topic: Annotated[
            str,
            Field(min_length=3, max_length=500, description="Primary research topic."),
        ],
        sub_queries: Annotated[
            list[str] | None,
            Field(description="Optional up to 3 focused sub-queries."),
        ] = None,
        max_queries: Annotated[
            int,
            Field(ge=1, le=4, description="Maximum total queries to execute (1-4)."),
        ] = 3,
    ) -> CallToolResult:
        """Run a bounded multi-query research workflow and deduplicate discovered sources."""
        res = await executor.execute(
            "deep_research",
            deep_research_service,
            {
                "topic": topic,
                "sub_queries": sub_queries,
                "providers": [tavily_provider, ddg_provider],
                "max_queries": max_queries,
            },
            permission=PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider="deep_research",
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="deep_research",
            module="research",
            description=(
                "Execute a bounded multi-query research workflow (max 4 queries, no autonomous "
                "loop) and return deduplicated, attributed sources."
            ),
            handler=deep_research_service,
            mcp_wrapper=deep_research,
            permission=PermissionLevel.L1_PREPARE,
            profiles=frozenset(
                {ProfileName.FACT_CHECK, ProfileName.RESEARCH, ProfileName.FULL}
            ),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
        )
    )

    async def research_report(
        topic: Annotated[
            str,
            Field(min_length=1, max_length=300, description="Report title or topic."),
        ],
        findings: Annotated[
            list[dict[str, Any]],
            Field(
                min_length=1,
                max_length=25,
                description="List of findings with 'source_id', 'title', 'url', and 'claim'.",
            ),
        ],
        uncertainties: Annotated[
            list[str] | None,
            Field(description="Explicit limitations, conflicts, or unknowns to disclose."),
        ] = None,
    ) -> CallToolResult:
        """Assemble supplied findings and source IDs/URLs into a cited Markdown report."""
        res = await executor.execute(
            "research_report",
            research_report_service,
            {"topic": topic, "findings": findings, "uncertainties": uncertainties},
            permission=PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="research_report",
            module="research",
            description=(
                "Assemble supplied findings and source citations into a structured Markdown "
                "report with explicit uncertainty disclosure."
            ),
            handler=research_report_service,
            mcp_wrapper=research_report,
            permission=PermissionLevel.L1_PREPARE,
            profiles=frozenset(
                {ProfileName.FACT_CHECK, ProfileName.RESEARCH, ProfileName.FULL}
            ),
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
            produces_provenance=True,
        )
    )

    # Legacy compatibility aliases (exposed only in FULL profile)
    async def limited_web_search(
        query: Annotated[str, Field(min_length=2, max_length=400)],
        search_type: Annotated[str, Field(description="'general' or 'news'.")] = "general",
    ) -> CallToolResult:
        """Deprecated compatibility wrapper: prefer `web_search` or `search_news`."""
        res = await executor.execute(
            "limited_web_search",
            legacy_limited_web_search_service,
            {
                "query": query,
                "search_type": search_type,
                "provider": ddg_provider,
                "cache": cache,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider=ddg_provider.name,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="limited_web_search",
            module="research",
            description=(
                "[Deprecated: use web_search, search_news, or multi_search] Legacy DuckDuckGo "
                "search wrapper."
            ),
            handler=legacy_limited_web_search_service,
            mcp_wrapper=limited_web_search,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.FULL}),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
            provider=ddg_provider.name,
        )
    )

    async def read_article(
        url: Annotated[str, Field(min_length=8, max_length=2000)],
    ) -> CallToolResult:
        """Deprecated compatibility wrapper: prefer `read_webpage`."""
        res = await executor.execute(
            "read_article",
            legacy_read_article_service,
            {"url": url},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider="http_extractor",
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="read_article",
            module="research",
            description="[Deprecated: use read_webpage] Legacy webpage reader wrapper.",
            handler=legacy_read_article_service,
            mcp_wrapper=read_article,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.FULL}),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
        )
    )
