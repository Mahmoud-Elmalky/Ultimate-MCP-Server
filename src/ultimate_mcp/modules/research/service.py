"""Public research service facade re-exporting search, live-data, and workflow services."""

from __future__ import annotations

from ultimate_mcp.modules.research.live_data_service import (
    get_exchange_rate_service,
    get_market_price_service,
    get_weather_service,
    legacy_calculate_or_time_service,
    legacy_youtube_expert_service,
    youtube_comments_service,
    youtube_search_service,
    youtube_transcript_service,
)
from ultimate_mcp.modules.research.search_service import (
    items_to_provenance,
    legacy_limited_web_search_service,
    multi_search_service,
    normalize_url_for_dedup,
    search_academic_service,
    search_news_service,
    search_official_docs_service,
    web_search_service,
)
from ultimate_mcp.modules.research.workflows import (
    STOPWORDS,
    compare_sources_service,
    deep_research_service,
    research_report_service,
    tokenize_keywords,
    verify_claim_service,
)

__all__ = [
    "STOPWORDS",
    "compare_sources_service",
    "deep_research_service",
    "get_exchange_rate_service",
    "get_market_price_service",
    "get_weather_service",
    "items_to_provenance",
    "legacy_calculate_or_time_service",
    "legacy_limited_web_search_service",
    "legacy_youtube_expert_service",
    "multi_search_service",
    "normalize_url_for_dedup",
    "research_report_service",
    "search_academic_service",
    "search_news_service",
    "search_official_docs_service",
    "tokenize_keywords",
    "verify_claim_service",
    "web_search_service",
    "youtube_comments_service",
    "youtube_search_service",
    "youtube_transcript_service",
]
