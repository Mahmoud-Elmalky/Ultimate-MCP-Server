# Ultimate MCP Server v2 — Model-Independent Handoff (`docs/HANDOFF.md`)

> **Authoritative state document for model switches.** Always verify against `git status`, `git log`, and test execution before making changes.

## 1. Current Repository & Git State

- **Last updated:** 2026-11-11T21:35+02:00
- **Branch:** `feat/v2-phase-2`
- **Base checkpoint commit:** `2385d3e` (`2385d3ee14528d5410e6b7c5474316a83a960cb8` — `docs: finalize phase 01 verification and phase 02 transition`)
- **Commit history on branch:**
  - `2385d3e` — `docs: finalize phase 01 verification and phase 02 transition`
  - `36166b3` — `feat: implement phase 01 core foundation, security policy, and MCP v2 gateway`
  - `bdafc65` — `docs: add v2 agent implementation pack for incremental migration`
  - `61a9d45` — `docs: add phase 0 baseline audit, legacy tool inventory, and report`
  - `2d00c9d` — `test: add characterization suite for legacy MCP server`
  - `ae5304d` — `chore: ignore local virtualenvs and tool caches`
  - `aafcce6` (`origin/main`, `main`) — `Update ultimate_mcp_server.py`
- **Working tree:** Contains the complete, verified **Phase 02 — Research Engine and Utilities** implementation, tests, and `docs/reports/phase-02-report.md` (uncommitted pending user authorization).
- **Untouched legacy files & live configurations:**
  - `ultimate_mcp_server.py` and `requirements.txt` remain untouched at their `aafcce6` baseline.
  - Separate live LM Studio server directory `D:\Coding\projects\Python\mcp search server for LM Studio\Ultimate MCP Server\` was **never modified**.
  - User's live `%USERPROFILE%\.lmstudio\mcp.json` was **never modified**; portable side-by-side configuration with both `Search-Tools` (legacy) and `Ultimate-MCP-v2` (v2) is committed in `mcp.json.example` (`2385d3e`).

## 2. Phase 02 Verification Results (Executed 2026-11-11T21:34+02:00)

| Check / Command | Exit Code | Verified Result |
|---|---|---|
| `uv run pytest -p no:cacheprovider --cov=ultimate_mcp --cov-report=term-missing` | `0` | **121 passed in 8.77s**, **86% total statement coverage** (3,157 stmts, 430 missed) |
| `uv run ruff check src tests` | `0` | `All checks passed!` |
| `uv run mypy` | `0` | `Success: no issues found in 46 source files` (`strict = true`) |
| `.\.venv-baseline\Scripts\python.exe -m pytest tests/characterization -p no:cacheprovider -q` | `0` | **56 passed in 7.30s** (all Phase 00 characterization tests preserved) |
| `uv run ultimate-mcp doctor` | `0` | `Ultimate MCP Server v2.0.0a1 (Python 3.12.10)`, `Profile: default | SQLite FTS5: True`, 5 enabled default tools; provider health reported for `tavily`, `duckduckgo`, `openalex`, `wttr.in`, `yfinance`, `youtube` |

### Measured Profile Tool-Definition Footprints
- **`default` profile:** `5` tools (`calculate`, `execute_approved_action`, `get_current_time`, `prepare_write_file`, `read_text_file`), **`3,525` JSON chars (~`881` estimated tokens)**.
- **`research` profile:** `23` tools (`calculate`, `compare_sources`, `convert_units`, `crawl_website`, `deep_research`, `extract_links`, `extract_tables`, `get_current_time`, `get_exchange_rate`, `get_market_price`, `get_weather`, `multi_search`, `read_webpage`, `research_report`, `rss_search`, `search_academic`, `search_news`, `search_official_docs`, `verify_claim`, `web_search`, `youtube_comments`, `youtube_search`, `youtube_transcript`), **`16,986` JSON chars (~`4,246` estimated tokens)**.
- **`full` profile:** `30` tools (all 23 `research` tools + 3 workspace/approval tools + 4 deprecated legacy compatibility wrappers `calculate_or_time`, `limited_web_search`, `read_article`, `youtube_expert`), **`21,448` JSON chars (~`5,362` estimated tokens)**.

## 3. LM Studio Host & UI Verification Status

- **Installed LM Studio version:** `0.4.28.0` (`lms` CLI commit `43d4b26`).
- **Explanation of Exit Code 1 in Earlier `lms status` / `lms ps` Check:**
  - `lms status` exited with `0` (`Server: OFF`).
  - `lms ps` exited with `0`, but wrote its informational message (`"No models are currently loaded. To load a model, run: lms load <model path>"`) to **stderr**.
  - When redirected via `2>&1` in PowerShell, native `stderr` lines are wrapped in `ErrorRecord` (`NativeCommandError`), causing the outer PowerShell session to exit with code `1` because `$Error` was non-empty, even though both `lms status` (`$LASTEXITCODE=0`) and `lms ps` (`$LASTEXITCODE=0`) succeeded natively.
- **UI Verification Limitation:**
  - Interactive LM Studio desktop UI testing remains **`pending` manual verification** (`tests/integration/test_v2_stdio_server.py` verifies protocol-level stdio startup, tool discovery, and tool calls via `mcp.client.stdio`).

## 4. Current Architecture & Phase 02 Implementation Decisions

1. **SSRF-Hardened Outbound HTTP & Per-Class TTL Cache (`core/http.py`, `core/cache.py`):**
   - `validate_and_resolve_url` & `fetch_safe_url` enforce `http`/`https` schemes, allowed ports (`80`, `443`, `8080`, `8443`), no userinfo credentials, DNS resolution (`socket.getaddrinfo`) blocking IPv4/IPv6 loopback, RFC 1918 private, link-local/metadata (`169.254.0.0/16`, `fe80::/10`), ULA (`fc00::/7`), CGNAT (`100.64.0.0/10`), multicast, reserved, and IPv4-mapped IPv6 addresses, manual per-hop redirect validation (`max_redirects=4`), post-connection DNS re-resolution to detect DNS rebinding, content-type allowlists, and streaming byte caps (`max_response_bytes`).
   - `PerClassTTLCache` enforces per-data-class TTLs (`web_search`=600s, `news_search`=300s, `academic_search`=1800s, `weather`=300s, `market_quote`=60s, `exchange_rate`=120s) and attaches explicit `[Cached result from ...]` freshness notes.
2. **Lazy Provider Adapters (`providers/`):**
   - Optional third-party libraries (`httpx`, `bs4`, `tavily`, `ddgs`, `yfinance`, `googleapiclient`, `youtube_transcript_api`) are lazily imported via `importlib.import_module` only when a provider is invoked, keeping startup fast and `mypy --strict` clean without requiring optional extras in the core environment.
   - `DefaultYouTubeProvider` supports `youtube-transcript-api` 1.2.x (`YouTubeTranscriptApi().fetch(video_id, languages=...)`) with fallback to `.get_transcript()`, closing Phase 00 Risk R7.
   - `YFinanceMarketProvider` removes fragile ticker guessing (closing R14) and qualifies quotes as delayed/last-available (closing R9).
   - `DuckDuckGoSearchProvider` prefers `ddgs` (closing R10 for search) and removes the legacy `general`-mode news keyword override (closing R13).
3. **Web Extraction & Deterministic Evidence Workflows (`modules/research/`):**
   - `web_extractor.py` uses `BeautifulSoup` when installed and falls back to a built-in stdlib `HTMLParser` (`_StdlibHTMLParser`) so HTML text, link, table, and same-domain crawl extraction work even without optional packages installed.
   - `parse_rss_or_atom_xml` rejects `<!DOCTYPE` and `<!ENTITY` declarations before parsing RSS 2.0 / Atom XML to prevent XXE/entity expansion.
   - `compare_sources`, `verify_claim`, `deep_research`, and `research_report` are 100% deterministic (ADR-0005 — no hidden LLM loops).

## 5. Remaining Risks & Next Phase (Phase 03 — Browser & Documents)

- **Closed Phase 00 Risks:** R1, R2, R4, R5, R6, R7, R8, R9, R10 (search), R11, R12, R13, R14.
- **Open Phase 00 Risks for Phase 03:**
  - **R3 (Critical):** Migrate legacy `read_local_file` into workspace-scoped multi-format document extraction (`document_read`, `document_search`, `document_qa`, `document_extract_tables`, `document_compare`, `document_convert`, `document_create`, `document_edit`, `image_ocr`) and keep `read_local_file` as a bounded compatibility wrapper.
  - **R10 (Documents):** Use `pypdf` instead of deprecated `PyPDF2`.
  - Implement Playwright browser automation adapter (`browser_open`, `browser_snapshot`, `browser_screenshot`, `browser_click`, `browser_type`, `browser_select`, `browser_scroll`, `browser_wait`, `browser_download`, `browser_upload`, `browser_submit_form`) behind optional `[browser]` extra and L0–L3 approval policies (`phases/PHASE-03_BROWSER_DOCUMENTS.md`).
