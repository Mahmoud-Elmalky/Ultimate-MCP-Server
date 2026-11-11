"""Deterministic offline tests for Phase 02 search, web extraction, RSS, and evidence workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ultimate_mcp.app import build_doctor_report, create_gateway
from ultimate_mcp.core.cache import PerClassTTLCache
from ultimate_mcp.core.config import ProfileName, Settings
from ultimate_mcp.core.http import SafeHttpResponse
from ultimate_mcp.core.models import ErrorCode, ResultStatus, UltimateMCPError
from ultimate_mcp.modules.research.service import (
    compare_sources_service,
    deep_research_service,
    legacy_limited_web_search_service,
    multi_search_service,
    research_report_service,
    search_academic_service,
    search_news_service,
    search_official_docs_service,
    verify_claim_service,
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
from ultimate_mcp.providers.base import SearchResultItem
from ultimate_mcp.providers.search.adapters import (
    DuckDuckGoSearchProvider,
    OpenAlexAcademicProvider,
    TavilySearchProvider,
)


def _html_response(
    url: str,
    html: str,
    *,
    final_url: str | None = None,
    content_type: str = "text/html",
) -> SafeHttpResponse:
    raw = html.encode("utf-8")
    return SafeHttpResponse(
        requested_url=url,
        final_url=final_url or url,
        status_code=200,
        content_type=content_type,
        headers={"content-type": content_type},
        body=raw,
        text=html,
        redirect_count=0 if final_url is None else 1,
        resolved_ips=("93.184.216.34",),
        retrieved_at="2026-11-11T19:00:00+00:00",
    )


def test_search_providers_and_web_search_fallback() -> None:
    class FakeTavilyClient:
        def search(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "results": [
                    {
                        "title": "Official Python Docs",
                        "url": "https://docs.python.org/3/",
                        "content": "Python 3.12 reference documentation.",
                        "published_date": "2026-10-01",
                    },
                    {"title": "Bad URL", "url": "javascript:alert(1)", "content": "ignored"},
                ]
            }

    class FakeDDGContext:
        def __enter__(self) -> FakeDDGContext:
            return self

        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            return None

        def text(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
            return [
                {
                    "title": f"DDG Result for {query}",
                    "href": "https://docs.python.org/3/",
                    "body": "Python 3.12 standard library.",
                },
                {
                    "title": "Independent Guide",
                    "href": "https://realpython.com/python312/",
                    "body": "Python 3.12 reference documentation features.",
                },
            ]

        def news(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
            return [
                {
                    "title": "Python Release News",
                    "url": "https://blog.python.org/release",
                    "body": "Release announcement.",
                    "date": "2026-11-01",
                }
            ]

    tavily_ok = TavilySearchProvider(
        api_key="tvly-test",
        client_factory=lambda k: FakeTavilyClient(),
    )
    tavily_unauth = TavilySearchProvider(api_key=None)
    ddg_ok = DuckDuckGoSearchProvider(ddgs_factory=FakeDDGContext)
    assert ddg_ok.health().available is True

    cache: PerClassTTLCache[Any] = PerClassTTLCache()
    res1 = web_search_service(
        "python 3.12",
        primary_provider=tavily_ok,
        fallback_provider=ddg_ok,
        cache=cache,
    )
    assert res1.status == ResultStatus.SUCCESS
    assert res1.data["provider"] == "tavily"
    assert len(res1.data["results"]) == 1

    # Cache hit
    res_cached = web_search_service(
        "python 3.12",
        primary_provider=tavily_ok,
        fallback_provider=ddg_ok,
        cache=cache,
    )
    assert res_cached.data["cached"] is True

    # Visible fallback when Tavily lacks API key
    res_fb = web_search_service(
        "python 3.12",
        primary_provider=tavily_unauth,
        fallback_provider=ddg_ok,
    )
    assert res_fb.status == ResultStatus.PARTIAL
    assert res_fb.data["provider"] == "duckduckgo"
    assert any("fell back to 'duckduckgo'" in w for w in res_fb.warnings)

    # News search fallback + cache hit
    news_res = search_news_service("python release", providers=[tavily_unauth, ddg_ok], cache=cache)
    assert news_res.status == ResultStatus.PARTIAL
    assert news_res.data["provider"] == "duckduckgo"
    assert news_res.data["results"][0]["published_at"] == "2026-11-01"

    news_cached = search_news_service(
        "python release",
        providers=[ddg_ok],
        cache=cache,
    )
    assert news_cached.data["cached"] is True

    # Official docs scoped search
    docs_res = search_official_docs_service(
        "asyncio taskgroup",
        domain="docs.python.org",
        provider=ddg_ok,
    )
    assert docs_res.data["effective_query"] == "site:docs.python.org asyncio taskgroup"

    # Multi-search URL deduplication & provider attribution
    multi_res = multi_search_service("python 3.12", providers=[tavily_ok, ddg_ok])
    assert multi_res.status == ResultStatus.SUCCESS
    assert len(multi_res.data["results"]) == 2
    assert multi_res.data["results"][0]["providers"] == ["tavily", "duckduckgo"]

    # Partial failure in multi_search when one provider fails
    multi_partial = multi_search_service("python 3.12", providers=[tavily_unauth, ddg_ok])
    assert multi_partial.status == ResultStatus.PARTIAL
    assert any("tavily" in w for w in multi_partial.warnings)

    # Legacy limited_web_search wrapper does NOT rewrite queries on general mode
    leg_res = legacy_limited_web_search_service("gold price", "general", provider=ddg_ok)
    assert leg_res.data["results"][0]["title"] == "DDG Result for gold price"
    assert any("Deprecated" in w for w in leg_res.warnings)


def test_openalex_academic_provider_and_service() -> None:
    sample_openalex = {
        "results": [
            {
                "id": "https://openalex.org/W12345",
                "display_name": "Attention Is All You Need",
                "doi": "https://doi.org/10.48550/arXiv.1706.03762",
                "publication_date": "2017-06-12",
                "cited_by_count": 95000,
                "authorships": [{"author": {"display_name": "Ashish Vaswani"}}],
                "abstract_inverted_index": {"Dominant": [0], "sequence": [1], "models": [2]},
            }
        ]
    }
    acad_prov = OpenAlexAcademicProvider(json_fetcher=lambda u: sample_openalex)
    cache: PerClassTTLCache[Any] = PerClassTTLCache()
    res = search_academic_service("transformer attention", provider=acad_prov, cache=cache)
    assert res.status == ResultStatus.SUCCESS
    item = res.data["results"][0]
    assert item["title"] == "Attention Is All You Need"
    assert "Dominant sequence models" in item["snippet"]
    assert "Citations: 95000." in item["snippet"]

    cached = search_academic_service("transformer attention", provider=acad_prov, cache=cache)
    assert cached.data["cached"] is True


def test_webpage_extraction_links_tables_crawler_and_rss() -> None:
    pages = {
        "https://example.com/start": _html_response(
            "https://example.com/start",
            """
            <html>
              <head>
                <title>Start Page</title>
                <link rel="canonical" href="https://example.com/start" />
                <script>console.log("secret");</script>
              </head>
              <body>
                <h1>Welcome</h1>
                <p>
                  First paragraph of content with enough text to exceed sixty-four
                  characters easily during pagination tests.
                </p>
                <a href="/about">About Us</a>
                <a href="https://other.org/ext">External</a>
                <a href="javascript:void(0)">Ignore JS</a>
                <table>
                  <tr><th>Metric</th><th>Value</th></tr>
                  <tr><td>Latency</td><td>12ms</td></tr>
                </table>
              </body>
            </html>
            """,
        ),
        "https://example.com/about": _html_response(
            "https://example.com/about",
            "<html><head><title>About</title></head><body><p>About body text.</p></body></html>",
        ),
    }

    def fake_fetcher(url: str) -> SafeHttpResponse:
        if url not in pages:
            raise UltimateMCPError(ErrorCode.NOT_FOUND, f"404 for {url}")
        return pages[url]

    page_res = read_webpage_service(
        "https://example.com/start",
        offset=0,
        max_chars=64,
        fetcher=fake_fetcher,
    )
    assert page_res.status == ResultStatus.PARTIAL
    assert page_res.data["title"] == "Start Page"
    assert "console.log" not in page_res.data["content"]
    assert page_res.metadata.next_offset == 64

    links_res = extract_links_service(
        "https://example.com/start",
        same_domain_only=True,
        fetcher=fake_fetcher,
    )
    assert len(links_res.data["links"]) == 1
    assert links_res.data["links"][0]["url"] == "https://example.com/about"

    tables_res = extract_tables_service("https://example.com/start", fetcher=fake_fetcher)
    assert tables_res.data["total_tables_found"] == 1
    assert tables_res.data["tables"][0]["rows"] == [["Metric", "Value"], ["Latency", "12ms"]]

    crawl_res = crawl_website_service(
        "https://example.com/start",
        max_pages=3,
        max_depth=1,
        fetcher=fake_fetcher,
    )
    assert crawl_res.data["pages_crawled"] == 2

    art_res = legacy_read_article_service("https://example.com/about", fetcher=fake_fetcher)
    assert "About body text" in art_res.data["content"]
    assert any("Deprecated" in w for w in art_res.warnings)

    # RSS 2.0 and Atom + XXE protection
    rss_xml = """<?xml version="1.0"?>
    <rss version="2.0">
      <channel>
        <title>Security Feed</title>
        <item>
          <title>SSRF Advisory</title>
          <link>https://example.com/adv1</link>
          <description>Patch your HTTP client.</description>
          <pubDate>2026-11-10</pubDate>
        </item>
      </channel>
    </rss>"""
    rss_res = rss_search_service(
        "https://example.com/feed.xml",
        query="ssrf",
        fetcher=lambda u: _html_response(u, rss_xml, content_type="application/rss+xml"),
    )
    assert rss_res.data["feed_title"] == "Security Feed"
    assert len(rss_res.data["items"]) == 1

    atom_xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Atom Feed</title>
      <entry>
        <title>Atom Item 1</title>
        <link href="https://example.com/atom1"/>
        <summary>Atom summary text</summary>
        <updated>2026-11-11T00:00:00Z</updated>
        <id>urn:uuid:1</id>
      </entry>
    </feed>"""
    atom_res = rss_search_service(
        "https://example.com/atom.xml",
        fetcher=lambda u: _html_response(u, atom_xml, content_type="application/atom+xml"),
    )
    assert atom_res.data["feed_title"] == "Atom Feed"
    assert atom_res.data["items"][0]["link"] == "https://example.com/atom1"

    xxe_xml = '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><rss/>'
    with pytest.raises(UltimateMCPError) as exc_xxe:
        rss_search_service(
            "https://example.com/xxe.xml",
            fetcher=lambda u: _html_response(u, xxe_xml, content_type="application/xml"),
        )
    assert exc_xxe.value.code == ErrorCode.UNSUPPORTED_FORMAT


def test_deterministic_evidence_workflows() -> None:
    # 1. No contradiction detected on shared topic with consistent signals
    comp = compare_sources_service(
        [
            {
                "id": "s1",
                "title": "Python 3.12 Release",
                "url": "https://docs.python.org/3.12/",
                "content": "Python 3.12 improves error messages and asyncio performance in 2023.",
            },
            {
                "id": "s2",
                "title": "RealPython Review",
                "url": "https://realpython.com/3.12/",
                "content": "Python 3.12 improves asyncio speed and subinterpreters in 2023.",
            },
        ]
    )
    assert comp.status == ResultStatus.SUCCESS
    assert comp.data["comparison_status"] == "No contradiction detected"
    assert comp.data["conflicts_detected"] == []
    assert "python" in comp.data["shared_terms"]
    assert "asyncio" in comp.data["shared_terms"]

    # 2. Negation conflict despite high lexical overlap
    comp_neg = compare_sources_service(
        [
            {
                "id": "s1",
                "title": "Claim A",
                "content": "The 2026 solar battery prototype achieved 45% efficiency in lab tests.",
            },
            {
                "id": "s2",
                "title": "Fact Check B",
                "content": (
                    "The 2026 solar battery prototype did not achieve 45% efficiency in lab tests."
                ),
            },
        ]
    )
    assert comp_neg.data["comparison_status"] == "Conflicting evidence detected"
    assert len(comp_neg.data["conflicts_detected"]) == 1

    # 3. Contradictory numbers, years, and time periods (Q1 vs Q4)
    comp_num = compare_sources_service(
        [
            {
                "id": "s1",
                "title": "Report 1",
                "content": "Project Aurora launched in Q1 2022 with 15% latency reduction.",
            },
            {
                "id": "s2",
                "title": "Report 2",
                "content": "Project Aurora launched in Q4 2024 with 40% latency reduction.",
            },
        ]
    )
    assert comp_num.data["comparison_status"] == "Conflicting evidence detected"
    reasons = comp_num.data["conflicts_detected"][0]["conflict_reasons"]
    assert any("contradictory_years" in r for r in reasons)
    assert any("different_time_periods" in r for r in reasons)
    assert any("conflicting_numeric_values" in r for r in reasons)

    # 4. Similar wording with different meanings (role inversion & causal vs correlational)
    comp_meaning = compare_sources_service(
        [
            {
                "id": "s1",
                "title": "Study A",
                "content": "InhibitorX blocked KinaseY activity and proves clinical remission.",
            },
            {
                "id": "s2",
                "title": "Study B",
                "content": (
                    "KinaseY blocked InhibitorX activity and is correlated with remission."
                ),
            },
        ]
    )
    assert comp_meaning.data["comparison_status"] == "Conflicting evidence detected"
    meaning_reasons = comp_meaning.data["conflicts_detected"][0]["conflict_reasons"]
    assert any("semantic_role_inversion" in r for r in meaning_reasons)
    assert any("causal_vs_correlational_meaning_mismatch" in r for r in meaning_reasons)

    # 5. Same topic but different populations/contexts (adults vs children; mice vs humans)
    comp_pop = compare_sources_service(
        [
            {
                "id": "s1",
                "title": "Adult Trial",
                "content": "PeptideZ dosage reduced blood pressure by 18% in adults.",
            },
            {
                "id": "s2",
                "title": "Pediatric Trial",
                "content": "PeptideZ dosage reduced blood pressure by 6% in children.",
            },
        ]
    )
    assert comp_pop.data["comparison_status"] == "Insufficient evidence"
    assert len(comp_pop.data["context_divergences"]) == 1
    assert "age_cohort" in comp_pop.data["context_divergences"][0]["context_differences"][0]

    # 6. Missing / unrelated evidence (< 2 shared topical terms)
    comp_insuff = compare_sources_service(
        [
            {"id": "s1", "title": "Cooking", "content": "Baking sourdough bread requires flour."},
            {"id": "s2", "title": "Astrophysics", "content": "Neutron stars emit radio pulses."},
        ]
    )
    assert comp_insuff.data["comparison_status"] == "Insufficient evidence"

    class FakeProvider:
        name = "mock_search"

        def health(self) -> Any:
            return None

        def search(
            self,
            query: str,
            *,
            max_results: int = 5,
            search_type: str = "general",
        ) -> list[SearchResultItem]:
            return [
                SearchResultItem(
                    title="Python 3.12 asyncio benchmarks",
                    url="https://docs.python.org/bench",
                    snippet="Python 3.12 improves asyncio performance significantly.",
                    provider="mock_search",
                    rank=1,
                    retrieved_at="2026-11-11T19:00:00+00:00",
                ),
                SearchResultItem(
                    title="Independent Python 3.12 benchmark",
                    url="https://lwn.net/Articles/python312/",
                    snippet="Independent tests show Python 3.12 improves asyncio performance.",
                    provider="mock_search",
                    rank=2,
                    retrieved_at="2026-11-11T19:00:00+00:00",
                ),
            ]

    ver = verify_claim_service(
        "Python 3.12 improves asyncio performance",
        providers=[FakeProvider()],
    )
    assert ver.data["verification_status"] == "Potential supporting evidence"
    assert ver.data["evidence_strength"] == "multiple_independent_matches"
    assert ver.data["independent_domain_count"] == 2

    # Conflicting evidence: high lexical overlap but negation or conflicting numbers/dates
    class ConflictingProvider:
        name = "mock_conflict"

        def health(self) -> Any:
            return None

        def search(
            self,
            query: str,
            *,
            max_results: int = 5,
            search_type: str = "general",
        ) -> list[SearchResultItem]:
            return [
                SearchResultItem(
                    title="Debunking Python 3.12 80% speedup myth",
                    url="https://bench.example.org/debunk",
                    snippet=(
                        "Claims that Python 3.12 improves asyncio performance by 80% in 2025 "
                        "are false and debunked; actual improvement was 15% in 2023."
                    ),
                    provider="mock_conflict",
                    rank=1,
                    retrieved_at="2026-11-11T19:00:00+00:00",
                )
            ]

    ver_conflict = verify_claim_service(
        "Python 3.12 improves asyncio performance by 80% in 2025",
        providers=[ConflictingProvider()],
    )
    assert ver_conflict.data["verification_status"] == "Conflicting evidence detected"
    assert ver_conflict.data["evidence_strength"] == "conflicting_evidence"
    assert ver_conflict.data["conflicting_source_count"] == 1

    # Different population/context in verify_claim (mice study vs humans claim) -> Insufficient
    class MouseStudyProvider:
        name = "mock_mouse"

        def health(self) -> Any:
            return None

        def search(
            self,
            query: str,
            *,
            max_results: int = 5,
            search_type: str = "general",
        ) -> list[SearchResultItem]:
            return [
                SearchResultItem(
                    title="CompoundZ longevity trial in mice",
                    url="https://bio.example.org/mice",
                    snippet="CompoundZ treatment extended median lifespan by 30% in mice.",
                    provider="mock_mouse",
                    rank=1,
                    retrieved_at="2026-11-11T19:00:00+00:00",
                )
            ]

    ver_pop = verify_claim_service(
        "CompoundZ treatment extended median lifespan by 30% in humans",
        providers=[MouseStudyProvider()],
    )
    assert ver_pop.data["verification_status"] == "Insufficient evidence"
    assert ver_pop.data["context_mismatch_source_count"] == 1

    # Insufficient evidence when results do not overlap the claim
    ver_insuff = verify_claim_service(
        "Quantum teleportation across transatlantic fiber optics in 1998",
        providers=[FakeProvider()],
    )
    assert ver_insuff.data["verification_status"] == "Insufficient evidence"
    assert ver_insuff.data["evidence_strength"] == "insufficient_evidence"

    deep = deep_research_service(
        "Python 3.12 performance",
        sub_queries=["Python 3.12 asyncio", "Python 3.12 subinterpreters"],
        providers=[FakeProvider()],
        max_queries=3,
    )
    assert len(deep.data["queries_executed"]) == 3
    assert deep.data["total_unique_sources"] == 2

    rep = research_report_service(
        "Python 3.12 Summary",
        findings=deep.data["sources"],
        uncertainties=["Benchmarks vary by OS workload."],
    )
    assert "# Research Report: Python 3.12 Summary" in rep.data["markdown"]
    assert len(rep.data["citations"]) == 2


async def test_research_and_full_profile_footprints_and_wrappers(tmp_path: Path) -> None:
    cfg = Settings(
        profile=ProfileName.RESEARCH,
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "out",
    )
    research_report = build_doctor_report(settings=cfg, profile_override=ProfileName.RESEARCH)
    assert research_report["enabled_tool_count"] == 23
    assert "convert_units" in research_report["enabled_tools"]
    assert "web_search" in research_report["enabled_tools"]
    assert "calculate_or_time" not in research_report["enabled_tools"]

    full_report = build_doctor_report(settings=cfg, profile_override=ProfileName.FULL)
    browser_ready = bool(full_report["optional_groups"]["browser"]["all_installed"])
    assert full_report["enabled_tool_count"] == (44 if browser_ready else 30)
    assert "calculate_or_time" in full_report["enabled_tools"]
    assert "youtube_expert" in full_report["enabled_tools"]

    # Focused small-model profiles (4k-8k context window friendly)
    search_rep = build_doctor_report(settings=cfg, profile_override=ProfileName.SEARCH)
    assert search_rep["enabled_tool_count"] == 7
    assert "web_search" in search_rep["enabled_tools"]

    web_res_rep = build_doctor_report(settings=cfg, profile_override=ProfileName.WEB_RESEARCH)
    assert web_res_rep["enabled_tool_count"] == 7
    assert "read_webpage" in web_res_rep["enabled_tools"]

    fact_rep = build_doctor_report(settings=cfg, profile_override=ProfileName.FACT_CHECK)
    assert fact_rep["enabled_tool_count"] == 7
    assert "verify_claim" in fact_rep["enabled_tools"]
    assert "compare_sources" in fact_rep["enabled_tools"]

    live_rep = build_doctor_report(settings=cfg, profile_override=ProfileName.LIVE_DATA)
    assert live_rep["enabled_tool_count"] == 6
    assert "get_weather" in live_rep["enabled_tools"]
    assert "get_market_price" in live_rep["enabled_tools"]

    yt_rep = build_doctor_report(settings=cfg, profile_override=ProfileName.YOUTUBE)
    assert yt_rep["enabled_tool_count"] == 4
    assert "youtube_transcript" in yt_rep["enabled_tools"]

    gw = create_gateway(settings=cfg, profile_override=ProfileName.FULL)
    unit_spec = gw.registry.get("convert_units")
    assert unit_spec is not None
    call_res = await unit_spec.mcp_wrapper(value=1000.0, from_unit="m", to_unit="km")
    assert call_res.is_error is False
    parsed = json.loads(call_res.content[0].text)
    assert parsed["data"]["converted_value"] == 1.0

    comp_spec = gw.registry.get("compare_sources")
    assert comp_spec is not None
    comp_res = await comp_spec.mcp_wrapper(
        sources=[
            {"id": "1", "title": "A", "content": "alpha beta gamma"},
            {"id": "2", "title": "B", "content": "beta gamma delta"},
        ]
    )
    assert comp_res.is_error is False

    rep_spec = gw.registry.get("research_report")
    assert rep_spec is not None
    rep_res = await rep_spec.mcp_wrapper(
        topic="Test Topic",
        findings=[{"source_id": "S1", "title": "Doc", "url": "https://example.com", "claim": "ok"}],
    )
    assert rep_res.is_error is False

    calc_leg = gw.registry.get("calculate_or_time")
    assert calc_leg is not None
    calc_leg_res = await calc_leg.mcp_wrapper(action="math", value="2 + 2")
    assert calc_leg_res.is_error is False
