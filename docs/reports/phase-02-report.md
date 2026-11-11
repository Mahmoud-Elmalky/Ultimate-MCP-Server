# Phase 02 Report — Research Engine and Utilities

## Status
- Status: `complete` (post-audit verified)
- Commit/branch (if known): `feat/v2-phase-2`. Prior Phase 02 commits: `44b77c9`, `ffcca82`, `1a9b92b`. The final checkpoint commit adds the cache expiration fix and these doc corrections.
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

## Tests and commands actually executed (final run 2026-11-11T23:48+02:00)
| Command | Exit/result | Notes |
|---|---|---|
| `uv run pytest -p no:cacheprovider --cov=ultimate_mcp --cov-report=term-missing` | 0 | **125 passed** in 6.83s, **88% total coverage** (3,448 stmts, 431 missed) |
| `uv run pytest -p no:cacheprovider tests/security/test_http_and_cache_phase2.py -q` | 0 | **56 passed** (HTTP/SSRF/pinned-IP + cache security tests) |
| `uv run ruff check src tests` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 48 source files` (`strict = true`) |
| `.\.venv-baseline\Scripts\python.exe -m pytest tests/characterization -p no:cacheprovider -q` | 0 | **56 passed** in 6.00s (legacy characterization preserved) |
| `uv run ultimate-mcp doctor` | 0 | `default` profile, 5 tools, SQLite FTS5 available |

## Cache expiration semantics (final)
- An entry is **fresh only while `now < expires_at`** and **expired when `now >= expires_at`** (both in `get()` and in the proactive purge inside `put()`).
- Stale fallback is returned only when the caller passes `allow_stale=True`, `max_stale_seconds > 0`, and `now - expires_at <= max_stale_seconds`. It is best-effort: any `put()` purges expired entries first.
- `MARKET_QUOTE` and `EXCHANGE_RATE` are never returned at or after expiry, even with `allow_stale=True`.
- Tests: `test_cache_expiration_boundary_now_ge_expires_at_is_expired`, `test_financial_cache_never_returned_at_or_after_expiration[market_quote|exchange_rate]`, `test_per_class_ttl_cache_freshness_stale_and_eviction`.

## Performance & Local Model Context Footprint
Methodology: compact JSON bytes (`separators=(',', ':')`) from `gw.mcp.list_tools()`, re-measured 2026-11-11T23:48+02:00, together with two heuristic estimators. **Neither one is an exact tokenizer measurement:**
1. `chars / 4` heuristic (a prose approximation).
2. `Structural Regex Token Proxy` (`len(re.findall(r'[A-Za-z0-9]+|[^A-Za-z0-9\s]', payload))`). It is a regex proxy that counts alphanumeric runs and punctuation separately, not a model BPE tokenizer.

> Correction: the previous revision of this report listed figures for `fact_check`, `research`, and `full` that were not re-measured after the tool-description edits. The table below contains the re-measured values.

| Profile | Enabled Tools | Compact JSON Bytes | `chars / 4` Heuristic | Structural Regex Token Proxy | Recommended Context Window (recommendation, not verified per model) |
|---|---:|---:|---:|---:|---|
| `browser` | 1 | 429 B | 107 | 152 | 4k+ |
| `documents` | 3 | 1,635 B | 408 | 565 | 4k+ |
| `youtube` | 4 | 2,237 B | 559 | 803 | 4k+ |
| `default` | 5 | 2,833 B | 708 | 963 | 4k+ |
| `live_data` | 6 | 3,307 B | 826 | 1,210 | 4k–8k+ |
| `coding` | 6 | 3,529 B | 882 | 1,230 | 4k–8k+ |
| `search` | 7 | 3,814 B | 953 | 1,385 | 4k–8k+ |
| `web_research` | 7 | 3,975 B | 993 | 1,518 | 4k–8k+ |
| `fact_check` | 7 | 4,607 B | 1,151 | 1,622 | 4k–8k+ |
| `research` | 23 | 13,886 B | 3,471 | 5,095 | 16k+ |
| `full` | 30 | 17,378 B | 4,344 | 6,363 | 32k+ (opt-in) |

## Acceptance criteria
| Criterion | Result | Evidence |
|---|---|---|
| Existing utility capabilities preserved or mapped to explicit new tools | Pass | 23 research/utility tools + 4 legacy wrappers verified in `test_phase2_providers_and_utilities.py` & `test_phase2_research_and_workflows.py` |
| Missing API keys disable only affected providers | Pass | `TavilySearchProvider(api_key=None)` and `DefaultYouTubeProvider(api_key=None)` tested; `youtube_transcript` and `DuckDuckGoSearchProvider` work without keys |
| Results identify provider and source/freshness metadata | Pass | Every tool attaches `Provenance(provider=..., retrieved_at=..., source_url/id=..., freshness_note=...)` |
| Search fallback and partial outcomes are visible rather than hidden | Pass | `web_search_service`, `search_news_service`, and `multi_search_service` return `ResultStatus.PARTIAL` with explicit warnings when a provider fails |
| URL fetching passes all SSRF/pinned-IP/redirect/size tests | Pass | 56/56 tests in `tests/security/test_http_and_cache_phase2.py`; socket-spy tests prove zero TLS/HTTP bytes when the peer IP is private or unapproved |
| Evidence workflows use conservative calibrated labels and detect conflicts/context divergence | Pass | `test_deterministic_evidence_workflows` (`No contradiction detected`, `Potential supporting evidence`, `Conflicting evidence detected`, `Insufficient evidence`) |
| All current data claims are timestamped and caveated correctly | Pass | Weather/market/FX outputs carry timestamps and caveats; financial cache entries are never returned at or after expiry |

## Known limitations
- LM Studio desktop UI verification of `Ultimate-MCP-v2` is **pending** manual verification; only protocol-level stdio tests (`tests/integration/test_v2_stdio_server.py`) have been executed.
- Evidence workflows are deterministic lexical/regex heuristics and are not semantic verification.
- The cache is in-process only. Sensitive-value detection is regex-based.
- The pinned transport connects to `pre_ips[0]` only, with no multi-IP failover.

## Recommendation
- Proceed to next phase? `yes`
- Reason: all Phase 02 acceptance criteria are evidenced by the final run above: 125 V2 tests, 88% coverage, 56 HTTP/cache security tests, 56 legacy tests, ruff, strict mypy, and doctor.
