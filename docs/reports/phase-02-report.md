# Phase 02 Report — Research Engine and Utilities

## Status
- Status: `complete` (post-audit verified)
- Commit/branch (if known): `feat/v2-phase-2` (HEAD `ffcca82` + final calibration working-tree changes ready for user-authorized checkpoint commit)
- Date: 2026-11-11

## Summary
- Implemented SSRF-hardened outbound HTTP (`src/ultimate_mcp/core/http.py` and `src/ultimate_mcp/core/http_transport.py`) with a **9-step pinned-IP execution path**: URL parsing, DNS A/AAAA resolution, strict public-IP validation across all resolved records (rejecting mixed public/private responses), direct TCP socket connection to a validated `pinned_ip` (eliminating any second unvalidated HTTP-client DNS lookup), pre-TLS/pre-request peer-IP verification (`sock.getpeername()[0]`) before TLS handshake or HTTP bytes are sent, TLS SNI + X.509 certificate verification against `expected_host`, isolated non-pooled requests (`Connection: close`, environment proxies ignored), bounded streaming/decompression, and per-hop redirect re-validation on a fresh pinned socket.
- Implemented thread-safe, bounded per-data-class TTL caching (`src/ultimate_mcp/core/cache.py`) with `threading.RLock`, global (`max_entries=256`) and per-class (`max_entries_per_class=64`) LRU limits, proactive expired-entry purging, provider/query/locale/`user_scope` isolation, rejection of sensitive parameter keys and credential-like values, and a hard prohibition against serving stale financial data (`MARKET_QUOTE`, `EXCHANGE_RATE`).
- Migrated and upgraded all search, live-data, web extraction, RSS, YouTube, and utility capabilities behind lazy provider interfaces and deterministic evidence workflows (`compare_sources`, `verify_claim`, `deep_research`, `research_report`) with conservative calibrated statuses:
  - `compare_sources`: `"No contradiction detected"`, `"Conflicting evidence detected"`, `"Insufficient evidence"`.
  - `verify_claim`: `"Potential supporting evidence"`, `"Conflicting evidence detected"`, `"Insufficient evidence"`.
- Added focused small-model profiles (`search`, `web_research`, `fact_check`, `live_data`, `youtube`) alongside `default` (5 tools), `research` (23 tools), and `full` (30 tools), with compact JSON bytes, `chars / 4`, and the `Structural Regex Token Proxy` heuristic documented.
- Added the mandatory **Browser Security & Network Egress Architecture** section (`§A.1`) to `phases/PHASE-03_BROWSER_DOCUMENTS.md` before Phase 03 implementation begins.
- Preserved `ultimate_mcp_server.py`, `%USERPROFILE%\.lmstudio\mcp.json`, and all 56 Phase 00 characterization tests untouched and passing.

## Implemented
- **Pinned-IP SSRF-Hardened HTTP & Thread-Safe Per-Class TTL Cache (`core/http.py`, `core/http_transport.py`, `core/cache.py`):**
  - `is_public_ip_address`, `validate_and_resolve_url`, `send_pinned_http_request`, `fetch_safe_url`: blocks `file://`, `ftp://`, `gopher://`, `javascript:`, userinfo (`user:pass@host`), non-standard ports, `localhost`/`.local`/`.internal`/`metadata.google.internal`, IPv4/IPv6 loopback, RFC 1918 private, link-local/cloud metadata (`169.254.169.254`, `fe80::/10`), ULA (`fc00::/7`), CGNAT (`100.64.0.0/10`), RFC 5737 documentation/benchmarking, multicast, reserved, and IPv4-mapped IPv6 (`::ffff:127.0.0.1`).
  - Connects TCP directly to `(pinned_ip, port)`, verifies `sock.getpeername()[0]` against `pre_ips` before TLS handshake or sending any HTTP bytes, wraps `https` sockets with `server_hostname=expected_host` (`CERT_REQUIRED`, `check_hostname=True`), sets `Host: <expected_host>` and `Connection: close`, ignores `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`, and re-validates every redirect hop (`max_redirects=4`) on a new pinned socket.
  - `PerClassTTLCache`: thread-safe (`threading.RLock`), enforces both `max_entries=256` and `max_entries_per_class=64`, purges expired entries before LRU eviction, isolates by `(data_class, provider, user_scope, query, locale, params)`, rejects sensitive parameter keys and credential-bearing values, rejects un-isolated user identifiers (`user_id`, `account_id`, `tenant_id`) in `public` scope, and prohibits stale serving for `MARKET_QUOTE` and `EXCHANGE_RATE`.
- **Provider Protocols & Adapters (`providers/`):**
  - `providers/base.py`: `SearchProvider`, `WeatherProvider`, `MarketDataProvider`, `YouTubeProvider`, `RSSProvider`, `ProviderHealth`, and typed report dataclasses.
  - `providers/search/adapters.py`: `TavilySearchProvider`, `DuckDuckGoSearchProvider`, and `OpenAlexAcademicProvider`.
  - `providers/weather/wttr.py`: `WttrWeatherProvider`.
  - `providers/finance/yfinance_provider.py`: `YFinanceMarketProvider` & `normalize_market_symbol`.
  - `providers/youtube/adapter.py`: `DefaultYouTubeProvider` & `extract_youtube_video_id`.
- **Utilities, Web Extraction, RSS & Calibrated Evidence Workflows (`modules/utilities/`, `modules/research/`):**
  - `convert_units`: deterministic converter across length, mass, time, volume, speed, area, data, pressure, and temperature.
  - `web_search`, `search_news`, `search_official_docs`, `search_academic`, `multi_search`: visible fallback (`status="partial"` + warning when primary fails), URL deduplication, and per-result provider attribution.
  - `read_webpage`, `extract_links`, `extract_tables`, `crawl_website`, `rss_search`: `BeautifulSoup` + stdlib `HTMLParser` fallback, offset pagination, bounded same-domain BFS crawl (`max_pages<=5`, `max_depth<=2`), and RSS 2.0 / Atom XML parser blocking `<!DOCTYPE` / `<!ENTITY` (XXE).
  - `compare_sources` & `verify_claim` (`evidence_signals.py`, `workflows.py`): deterministic lexical overlap plus negation, numeric, temporal (dates/years/quarters/months/seasons), causal-vs-correlational modality, subject/object role inversion, and population/context (`age_cohort`, `organism_model`, `geography`, `platform`) checks using conservative calibrated labels (`No contradiction detected`, `Potential supporting evidence`, `Conflicting evidence detected`, `Insufficient evidence`).

## Files added/changed
- Updated source (`src/ultimate_mcp/`):
  - `core/config.py`, `core/bootstrap.py`
- Added source (`src/ultimate_mcp/`):
  - `core/cache.py`, `core/http.py`, `core/http_transport.py`
  - `providers/base.py`
  - `providers/search/__init__.py`, `providers/search/adapters.py`
  - `providers/weather/__init__.py`, `providers/weather/wttr.py`
  - `providers/finance/__init__.py`, `providers/finance/yfinance_provider.py`
  - `providers/youtube/__init__.py`, `providers/youtube/adapter.py`
  - `modules/utilities/units.py`, `modules/utilities/tools.py`
  - `modules/research/__init__.py`, `modules/research/search_service.py`, `modules/research/live_data_service.py`, `modules/research/web_extractor.py`, `modules/research/evidence_signals.py`, `modules/research/workflows.py`, `modules/research/service.py`, `modules/research/tools.py`
- Added tests:
  - `tests/security/test_http_and_cache_phase2.py`
  - `tests/unit/test_phase2_providers_and_utilities.py`
  - `tests/unit/test_phase2_research_and_workflows.py`
- Added/updated docs:
  - `docs/reports/phase-02-report.md`
  - `docs/HANDOFF.md`
  - `IMPLEMENTATION_ROADMAP.md`
  - `phases/PHASE-03_BROWSER_DOCUMENTS.md`

## Tests and commands actually executed
| Command | Exit/result | Notes |
|---|---|---|
| `uv run pytest -p no:cacheprovider --cov=ultimate_mcp --cov-report=term-missing` | 0 | **122 passed** in 11.41s, **87% total coverage** (3,448 stmts, 433 missed) |
| `uv run ruff check src tests` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 48 source files` (`strict = true`) |
| `.\.venv-baseline\Scripts\python.exe -m pytest tests/characterization -p no:cacheprovider -q` | 0 | **56 passed** in 7.53s (legacy characterization preserved) |
| `uv run ultimate-mcp doctor` | 0 | Verified `default` profile (5 tools) and provider health diagnostics |

## Performance & Local Model Context Footprint
Methodology: Measured compact JSON bytes (`separators=(',', ':')`) from `gw.mcp.list_tools()` alongside two heuristic estimators (neither is an exact BPE tokenizer measurement):
1. `chars / 4` Heuristic (standard prose approximation).
2. `Structural Regex Token Proxy` (`len(re.findall(r'[A-Za-z0-9]+|[^A-Za-z0-9\s]', payload))`, a regex-based structural proxy that counts alphanumeric runs and JSON punctuation tokens separately, not an actual model BPE tokenizer).

| Profile | Enabled Tools | Compact JSON Bytes | `chars / 4` Heuristic | Structural Regex Token Proxy | Recommended Context Window (Unverified Heuristic) |
|---|---:|---:|---:|---:|---|
| `browser` | 1 | 429 B | 107 | 152 | 4k+ (recommendation) |
| `documents` | 3 | 1,635 B | 408 | 565 | 4k+ (recommendation) |
| `youtube` | 4 | 2,237 B | 559 | 803 | 4k+ (recommendation) |
| `default` | 5 | 2,833 B | 708 | 963 | 4k+ (recommendation) |
| `live_data` | 6 | 3,307 B | 826 | 1,210 | 4k–8k+ (recommendation) |
| `coding` | 6 | 3,529 B | 882 | 1,230 | 4k–8k+ (recommendation) |
| `search` | 7 | 3,814 B | 953 | 1,385 | 4k–8k+ (recommendation) |
| `web_research` | 7 | 3,975 B | 993 | 1,518 | 4k–8k+ (recommendation) |
| `fact_check` | 7 | 4,568 B | 1,142 | 1,619 | 4k–8k+ (recommendation) |
| `research` | 23 | 13,847 B | 3,461 | 5,092 | 16k+ (recommendation) |
| `full` | 30 | 17,339 B | 4,334 | 6,360 | 32k+ (opt-in recommendation) |

## Acceptance criteria
| Criterion | Result | Evidence |
|---|---|---|
| Existing utility capabilities preserved or mapped to explicit new tools | Pass | 23 research/utility tools + 4 legacy wrappers verified in `test_phase2_providers_and_utilities.py` & `test_phase2_research_and_workflows.py` |
| Missing API keys disable only affected providers | Pass | `TavilySearchProvider(api_key=None)` and `DefaultYouTubeProvider(api_key=None)` tested; `youtube_transcript` and `DuckDuckGoSearchProvider` work without keys |
| Results identify provider and source/freshness metadata | Pass | Every tool attaches `Provenance(provider=..., retrieved_at=..., source_url/id=..., freshness_note=...)` |
| Search fallback and partial outcomes are visible rather than hidden | Pass | `web_search_service`, `search_news_service`, and `multi_search_service` return `ResultStatus.PARTIAL` with explicit warnings when a provider fails |
| URL fetching passes all SSRF/pinned-IP/redirect/size tests | Pass | 53/53 tests in `tests/security/test_http_and_cache_phase2.py`, proving zero TLS/HTTP bytes are transmitted when peer IP is private or unapproved |
| Evidence workflows use conservative calibrated labels and detect conflicts/context divergence | Pass | Verified in `test_deterministic_evidence_workflows` (`No contradiction detected`, `Potential supporting evidence`, `Conflicting evidence detected`, `Insufficient evidence`) |
| All current data claims are timestamped and caveated correctly | Pass | `get_weather`, `get_market_price`, and `get_exchange_rate` include observation/quote timestamps, units/currency, delayed-feed caveats, and never serve stale financial quotes |

## Recommendation
- Proceed to next phase? `yes` (pending user review and commit authorization)
- Reason: All Phase 02 acceptance criteria, pinned-IP DNS-rebinding defenses, conservatively calibrated evidence workflows, thread-safe TTL cache limits, Phase 03 browser security architecture specification, offline tests (122 passing, 87% coverage), strict mypy, ruff, and 56 legacy characterization tests pass.
