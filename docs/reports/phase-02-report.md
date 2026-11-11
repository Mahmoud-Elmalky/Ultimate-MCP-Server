# Phase 02 Report — Research Engine and Utilities

## Status
- Status: `complete`
- Commit/branch (if known): `feat/v2-phase-2` (working tree ready for user-authorized commit; base `2385d3e`)
- Date: 2026-11-11

## Summary
- Implemented SSRF-hardened outbound HTTP (`src/ultimate_mcp/core/http.py`) with DNS resolution, IPv4/IPv6/IPv4-mapped private/loopback/link-local/metadata/CGNAT blocking, manual per-hop redirect validation, post-connection DNS-rebinding defense, content-type allowlists, timeouts, and streaming byte caps.
- Implemented bounded per-data-class TTL caching (`src/ultimate_mcp/core/cache.py`) with explicit `[Cached result ...]` freshness notes.
- Migrated and upgraded all search, live-data, web extraction, RSS, YouTube, and utility capabilities behind lazy provider interfaces (`SearchProvider`, `WeatherProvider`, `MarketDataProvider`, `YouTubeProvider`, `RSSProvider`) and deterministic evidence workflows (`compare_sources`, `verify_claim`, `deep_research`, `research_report`) with zero hidden LLM calls.
- Kept `default` profile compact (5 core tools, ~881 estimated tokens), exposed the 23 v2 research & utility tools in `research` profile (~4,246 estimated tokens), and placed the 4 legacy compatibility wrappers (`calculate_or_time`, `limited_web_search`, `read_article`, `youtube_expert`) in `full` profile (30 tools, ~5,362 estimated tokens).
- Preserved `ultimate_mcp_server.py`, `%USERPROFILE%\.lmstudio\mcp.json`, and all 56 Phase 00 characterization tests untouched and passing.

## Implemented
- **SSRF-Hardened HTTP & Per-Class TTL Cache (`core/http.py`, `core/cache.py`):**
  - `is_public_ip_address`, `validate_and_resolve_url`, `fetch_safe_url`: blocks `file://`, `ftp://`, `gopher://`, `javascript:`, userinfo (`user:pass@host`), non-standard ports, `localhost`/`.local`/`.internal`/`metadata.google.internal`, IPv4/IPv6 loopback, RFC 1918 private, link-local/cloud metadata (`169.254.169.254`, `fe80::/10`), ULA (`fc00::/7`), CGNAT (`100.64.0.0/10`), multicast, reserved, and IPv4-mapped IPv6 (`::ffff:127.0.0.1`).
  - Manual redirect loop (`max_redirects=4`) re-validating scheme, host, port, and DNS on every hop, plus post-request DNS re-resolution to detect DNS rebinding.
  - `PerClassTTLCache`: distinct TTLs for `web_search` (600s), `news_search` (300s), `academic_search` (1800s), `weather` (300s), `market_quote` (60s), `exchange_rate` (120s), `webpage` (900s), and `rss_feed` (300s).
- **Provider Protocols & Adapters (`providers/`):**
  - `providers/base.py`: `SearchProvider`, `WeatherProvider`, `MarketDataProvider`, `YouTubeProvider`, `RSSProvider`, `ProviderHealth`, and typed report dataclasses.
  - `providers/search/adapters.py`: `TavilySearchProvider` (lazy `tavily-python`), `DuckDuckGoSearchProvider` (lazy `ddgs` with `duckduckgo_search` fallback; removes the legacy `general`-mode news keyword override), and `OpenAlexAcademicProvider` (reconstructs inverted-index abstracts and citations).
  - `providers/weather/wttr.py`: `WttrWeatherProvider` (`wttr.in/?format=j1` with °C/°F, humidity, wind, observation time, resolved area, and optional 3-day forecast).
  - `providers/finance/yfinance_provider.py`: `YFinanceMarketProvider` & `normalize_market_symbol` (removes fragile legacy ticker guessing; returns currency, exchange, market state, quote timestamp, and explicit delayed/indicative freshness caveat; adds `get_exchange_rate`).
  - `providers/youtube/adapter.py`: `DefaultYouTubeProvider` & `extract_youtube_video_id` (fixes Phase 00 Risk R7 by using `YouTubeTranscriptApi().fetch(video_id, languages=...)` for `youtube-transcript-api` 1.2.x while preserving `.get_transcript()` fallback; splits `youtube_search`, `youtube_transcript`, and `youtube_comments`).
- **Utilities, Web Extraction, RSS & Evidence Workflows (`modules/utilities/`, `modules/research/`):**
  - `convert_units`: deterministic converter across length, mass, time, volume, speed, area, data, pressure, and temperature (with absolute-zero validation).
  - `web_search`, `search_news`, `search_official_docs`, `search_academic`, `multi_search`: visible fallback (`status="partial"` + warning when primary fails), URL deduplication, and per-result provider attribution.
  - `read_webpage`, `extract_links`, `extract_tables`, `crawl_website`, `rss_search`: `BeautifulSoup` + stdlib `HTMLParser` fallback, offset pagination, bounded same-domain BFS crawl (`max_pages<=5`, `max_depth<=2`), and RSS 2.0 / Atom XML parser blocking `<!DOCTYPE` / `<!ENTITY` (XXE).
  - `compare_sources`, `verify_claim`, `deep_research`, `research_report`: deterministic lexical overlap, multi-domain evidence gathering, bounded multi-query research, and cited Markdown report assembly without hidden LLM loops.
  - Legacy compatibility wrappers (`calculate_or_time`, `limited_web_search`, `read_article`, `youtube_expert`) registered in `full` profile with explicit deprecation warnings.

## Files added/changed
- Updated source:
  - `src/ultimate_mcp/core/bootstrap.py`
- Added source (`src/ultimate_mcp/`):
  - `core/cache.py`, `core/http.py`
  - `providers/base.py`
  - `providers/search/__init__.py`, `providers/search/adapters.py`
  - `providers/weather/__init__.py`, `providers/weather/wttr.py`
  - `providers/finance/__init__.py`, `providers/finance/yfinance_provider.py`
  - `providers/youtube/__init__.py`, `providers/youtube/adapter.py`
  - `modules/utilities/units.py`, `modules/utilities/tools.py`
  - `modules/research/__init__.py`, `modules/research/search_service.py`, `modules/research/live_data_service.py`, `modules/research/web_extractor.py`, `modules/research/workflows.py`, `modules/research/service.py`, `modules/research/tools.py`
- Added tests:
  - `tests/security/test_http_and_cache_phase2.py`
  - `tests/unit/test_phase2_providers_and_utilities.py`
  - `tests/unit/test_phase2_research_and_workflows.py`
- Added/updated docs:
  - `docs/reports/phase-02-report.md`
  - `docs/HANDOFF.md`
  - `IMPLEMENTATION_ROADMAP.md`

## Tests and commands actually executed
| Command | Exit/result | Notes |
|---|---|---|
| `uv run pytest -p no:cacheprovider --cov=ultimate_mcp --cov-report=term-missing` | 0 | **121 passed** in 8.77s, **86% total coverage** (3,157 stmts, 430 missed) |
| `uv run ruff check src tests` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 46 source files` (`strict = true`) |
| `.\.venv-baseline\Scripts\python.exe -m pytest tests/characterization -p no:cacheprovider -q` | 0 | **56 passed** in 7.30s (legacy characterization preserved) |
| `uv run ultimate-mcp doctor` | 0 | Verified `default` profile (5 tools) and provider health diagnostics |

## Security review
- Threats checked:
  - SSRF against IPv4/IPv6 loopback (`127.0.0.1`, `::1`), RFC 1918 private ranges (`10.0.0.1`, `172.16.0.1`, `192.168.1.1`), AWS/GCP/Azure metadata (`169.254.169.254`, `metadata.google.internal`), IPv6 link-local/ULA (`fe80::1`, `fd00::1`), IPv4-mapped IPv6 (`::ffff:127.0.0.1`, `::ffff:169.254.169.254`), CGNAT (`100.64.0.1`), and `.localhost`/`.local`/`.internal` suffixes.
  - Redirect-based SSRF (`302` from public host to `http://169.254.169.254/latest/meta-data` or `http://127.0.0.1/admin`), redirect loops, and missing `Location` headers.
  - DNS rebinding (hostname resolving to public IP on pre-check and private IP on post-connection check).
  - Non-HTTP schemes (`file://`, `ftp://`, `gopher://`, `javascript:`), embedded URL credentials, and non-standard ports.
  - Oversized HTTP bodies (`RESPONSE_TOO_LARGE`), disallowed binary content types (`UNSUPPORTED_FORMAT`), and XML `<!DOCTYPE` / `<!ENTITY` expansion (XXE) in RSS/Atom feeds.
- Tests passed/failed: 121/121 v2 unit, security, and stdio integration tests passed (including 52 dedicated HTTP/SSRF/cache tests).
- Remaining risks:
  - Phase 00 risks R4 (SSRF/unbounded webpage fetch), R7 (`youtube-transcript-api` 1.2.x breakage), R9 (unqualified "Live" market price), R10 (`duckduckgo-search` -> `ddgs` in research adapter), R13 (`limited_web_search` keyword override), and R14 (fragile market ticker rewriting) are **closed in the v2 runtime**.
  - Phase 00 Risk R3 (unscoped `read_local_file` for PDF/DOCX/XLSX/CSV) and `PyPDF2` -> `pypdf` migration are scheduled for Phase 03 (`phases/PHASE-03_BROWSER_DOCUMENTS.md`).

## Dependencies
- Added/removed: No new core dependencies added; core install remains 5 lightweight packages (`mcp[cli]`, `pydantic`, `pydantic-settings`, `platformdirs`, `tzdata`).
- Core vs optional: All third-party providers (`httpx`, `bs4`, `tavily`, `ddgs`, `yfinance`, `googleapiclient`, `youtube_transcript_api`) are lazily imported via `importlib.import_module` inside adapters and have stdlib fallbacks where practical (`html.parser.HTMLParser`, `xml.etree.ElementTree`, OpenAlex JSON).

## Compatibility
- Python: 3.12.10 in `.venv` (`>=3.11`).
- MCP SDK: `mcp 2.3.0`.
- LM Studio version/host test: LM Studio `0.4.28.0` installed locally; `mcp.json.example` validated; live LM Studio desktop UI test remains `pending` manual user verification.

## Performance & Context Footprint
- Tool schema footprint by profile (measured via `gw.mcp.list_tools()`):
  - `default` profile: **5 tools**, **3,525 JSON chars (~881 estimated tokens)**.
  - `research` profile: **23 tools**, **16,986 JSON chars (~4,246 estimated tokens)**.
  - `full` profile: **30 tools** (includes 4 deprecated legacy aliases), **21,448 JSON chars (~5,362 estimated tokens)**.

## Acceptance criteria
| Criterion | Result | Evidence |
|---|---|---|
| Existing utility capabilities preserved or mapped to explicit new tools | Pass | 23 research/utility tools + 4 legacy wrappers verified in `test_phase2_providers_and_utilities.py` & `test_phase2_research_and_workflows.py` |
| Missing API keys disable only affected providers | Pass | `TavilySearchProvider(api_key=None)` and `DefaultYouTubeProvider(api_key=None)` tested; `youtube_transcript` and `DuckDuckGoSearchProvider` work without keys |
| Results identify provider and source/freshness metadata | Pass | Every tool attaches `Provenance(provider=..., retrieved_at=..., source_url/id=..., freshness_note=...)` |
| Search fallback and partial outcomes are visible rather than hidden | Pass | `web_search_service`, `search_news_service`, and `multi_search_service` return `ResultStatus.PARTIAL` with explicit warnings when a provider fails |
| URL fetching passes all SSRF/redirect/size tests | Pass | 52/52 tests in `tests/security/test_http_and_cache_phase2.py` |
| Calculator has no dynamic evaluation | Pass | `calculate` and `legacy_calculate_or_time_service` both use the Phase 01 AST evaluator |
| All current data claims are timestamped and caveated correctly | Pass | `get_weather`, `get_market_price`, and `get_exchange_rate` include observation/quote timestamps, units/currency, and delayed-feed caveats |
| Tool outputs remain within tested token/byte budgets | Pass | Offset pagination (`read_webpage`, `youtube_transcript`), bounded crawls (`max_pages<=5`), and `ToolResult.to_mcp_text` artifact spillover |

## Known limitations / follow-up
- Live LM Studio UI verification of `Ultimate-MCP-v2` remains a manual user step.
- Phase 03 (`phases/PHASE-03_BROWSER_DOCUMENTS.md`) will implement Playwright browser automation and multi-format document extraction (`document_read`, `document_search`, `document_qa`, `document_extract_tables`, `read_local_file` wrapper).

## Recommendation
- Proceed to next phase? `yes`
- Reason: All Phase 02 acceptance criteria, SSRF/security invariants, offline provider/workflow tests (121 passing, 86% coverage), strict mypy, ruff, and 56 legacy characterization tests pass.
