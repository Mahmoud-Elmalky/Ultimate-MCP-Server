# Phase 02 — Research Engine and Utilities

## Objective

Migrate and improve the existing search/live-data/YouTube/calculator/clock tools through provider adapters, evidence provenance, freshness metadata, bounded result handling, and resilient errors.

## Prerequisites

Phase 01 core, typed results, timeout/retry, config, and security policies accepted.


## Recommended tooling and optional dependencies

- `httpx` for bounded HTTP requests (kept separate from the MCP SDK's own HTTP dependencies).
- `beautifulsoup4` and optionally `lxml` for HTML extraction.
- Tavily SDK as an optional provider; DuckDuckGo adapter using a currently maintained package/API verified at implementation time (do not blindly retain an obsolete import).
- `yfinance` optional for market data; document provider freshness limitations.
- `google-api-python-client` and the currently supported `youtube-transcript-api` API for YouTube; validate version/API before locking.
- Python `zoneinfo` for time zones; add `tzdata` only if Windows/runtime tests prove it necessary.
- `pytest` + mocked HTTP/provider fixtures for deterministic tests.

## Detailed implementation

### A. Provider interfaces
1. Define small interfaces for SearchProvider, WeatherProvider, MarketDataProvider, YouTubeProvider, and RSSProvider.
2. Keep provider-specific SDK and HTTP details inside adapters.
3. Initialize providers lazily; validate API keys only when a provider is used.
4. Support provider health/availability diagnostics.
5. Return provider name, retrieval timestamp, source URL/ID, freshness, and limitations.

### B. Web search
1. Migrate Tavily search to an optional provider.
2. Migrate DuckDuckGo to an optional provider using a currently maintained package/API verified at implementation time.
3. Implement `web_search`, `search_news`, `search_official_docs`, and `search_academic` only when supported by a real provider.
4. Implement `multi_search` with bounded parallelism, timeout, deduplication, per-result provider labels, and no silent provider blending.
5. Normalize results into title, URL, snippet/content excerpt, published time (if supplied), retrieved time, provider, and rank.
6. Reject malformed URLs and cap result count/content bytes.
7. Do not imply that a general search provider provides exhaustive or authoritative coverage.

### C. Web content extraction
1. Migrate legacy `read_article` to `read_webpage` with compatibility wrapper as needed.
2. Implement URL validation, DNS/SSRF policy, redirects disabled or manually revalidated, content-type allowlist, compressed/decompressed byte limits, and timeouts.
3. Parse readable content and metadata; preserve canonical URL and fetched time.
4. Extract links and tables as separate bounded functions.
5. Add same-domain bounded crawler only after per-URL policy is proven; cap pages, depth, queue, bytes, and runtime.
6. Save long extracts as local artifacts and return artifact IDs/continuation rather than huge tool responses.

### D. Evidence workflows
1. Implement `compare_sources` over supplied source records; retain source IDs.
2. Implement `verify_claim` as a bounded evidence-gathering helper, not an oracle. Require multiple independent sources when available; disclose conflicts and unknowns.
3. Implement `deep_research` with explicit max queries, max pages, max duration, max output, and cancellation.
4. Implement `research_report` with claim-to-source mapping and explicit uncertainty.
5. No hidden model call: the host model plans and interprets; the workflow performs bounded deterministic retrieval and assembly.

### E. Utilities migration
1. Migrate `get_weather`; use provider time, units, parsed status, and explicit error handling.
2. Migrate `get_market_price`; remove fragile symbol rewriting, identify supported symbol format, return currency/provider timestamp/market status where available, and avoid “real-time” wording unless justified.
3. Split `calculate_or_time` into `calculate` and `get_current_time`; use Phase 01 AST calculator.
4. Validate IANA time zones and handle unavailable zone database clearly on Windows.
5. Split `youtube_expert` into `youtube_search`, `youtube_transcript`, and `youtube_comments`.
6. Verify current YouTube transcript library API/version. Handle disabled captions, language fallback, quota, comments disabled, private/unavailable videos, and pagination.
7. Keep old names as wrappers only for a documented compatibility window.

### F. Caching and rate limits
1. Cache only where freshness permits; cache keys include provider, query, locale, and relevant parameters.
2. Apply TTL per data class, not one global cache time.
3. Mark cached/stale data explicitly.
4. Respect `Retry-After`, provider quotas, and request rate limits.

### G. Tests
- Mock search provider ranking/deduplication and partial provider failures.
- SSRF, redirects, content-type, response-size, malformed HTML, and timeout tests.
- Source provenance and freshness tests.
- Market symbol normalization, empty/stale quote, and provider error tests.
- Weather malformed response/unit tests.
- YouTube transcript/comments unavailable/quota tests.
- Calculator adversarial AST inputs and time-zone tests.
- No live provider tests in default CI.

## Acceptance criteria

- Existing utility capabilities are preserved or mapped to explicit new tools.
- Missing API keys disable only affected providers.
- Results identify provider and source/freshness metadata.
- Search fallback and partial outcomes are visible rather than hidden.
- URL fetching passes all SSRF/redirect/size tests.
- Calculator has no dynamic evaluation.
- All current data claims are timestamped and caveated correctly.
- Tool outputs remain within tested token/byte budgets.
