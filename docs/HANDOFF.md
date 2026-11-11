# Ultimate MCP Server v2 — Model-Independent Handoff (`docs/HANDOFF.md`)

> **Authoritative state document for model switches.** Always verify against `git status`, `git log`, and test execution before making changes.

## 1. Current Repository & Git State (Verified Checkpoint)

- **Last updated:** 2026-11-11T20:03+02:00
- **Branch:** `feat/v2-phase-0`
- **HEAD commit:** `36166b3` (`36166b35bebe84f275d3c0baa69a60eaf1fe88f6` — `feat: implement phase 01 core foundation, security policy, and MCP v2 gateway`)
- **Commit history on branch:**
  - `36166b3` — `feat: implement phase 01 core foundation, security policy, and MCP v2 gateway`
  - `bdafc65` — `docs: add v2 agent implementation pack for incremental migration`
  - `61a9d45` — `docs: add phase 0 baseline audit, legacy tool inventory, and report`
  - `2d00c9d` — `test: add characterization suite for legacy MCP server`
  - `ae5304d` — `chore: ignore local virtualenvs and tool caches`
  - `aafcce6` (`origin/main`, `main`) — `Update ultimate_mcp_server.py`
- **Working tree:** Only transition documentation updates (`docs/HANDOFF.md`, `IMPLEMENTATION_ROADMAP.md`, `mcp.json.example`) are modified; `git diff --check` reports zero whitespace or formatting issues.
- **Ignored local artifacts verified excluded from Git (`git status --ignored --short`):**
  - `.venv/`, `.venv-baseline/`, `.coverage`, `.mypy_cache/`, `.ruff_cache/`, `__pycache__/`. No `.env`, secrets, SQLite `.db`/`.sqlite3` files, or scratch scripts exist in tracked or untracked Git paths.
- **Untouched legacy files & live configurations:**
  - `ultimate_mcp_server.py` and `requirements.txt` remain untouched at their `aafcce6` baseline.
  - Separate live LM Studio server directory `D:\Coding\projects\Python\mcp search server for LM Studio\Ultimate MCP Server\` was **never modified**.
  - User's live `%USERPROFILE%\.lmstudio\mcp.json` was **never modified**; a non-destructive side-by-side configuration with both `Search-Tools` (legacy) and `Ultimate-MCP-v2` (v2) is prepared in `mcp.json.example`.

## 2. Phase 01 Re-Verification Results (Executed 2026-11-11T20:01+02:00)

| Check / Command | Exit Code | Verified Result |
|---|---|---|
| `uv run pytest -p no:cacheprovider --cov=ultimate_mcp --cov-report=term-missing` | `0` | **57 passed in 7.35s**, **88% total statement coverage** (1,283 stmts, 153 missed) |
| `uv run ruff check src tests` | `0` | `All checks passed!` |
| `uv run mypy` | `0` | `Success: no issues found in 26 source files` (`strict = true`) |
| `.venv-baseline\Scripts\python -m pytest tests/characterization -p no:cacheprovider -q` | `0` | **56 passed in 7.29s** (all Phase 00 characterization tests preserved) |
| `uv run ultimate-mcp doctor` | `0` | `Ultimate MCP Server v2.0.0a1 (Python 3.12.10)`, `Profile: default | SQLite FTS5: True`, 5 enabled tools (`calculate`, `execute_approved_action`, `get_current_time`, `prepare_write_file`, `read_text_file`) |

## 3. LM Studio Host & UI Verification Status

- **Installed LM Studio version:** `0.4.28.0` (`lms` CLI commit `43d4b26`).
- **Live LM Studio state during checkpoint (`lms status` / `lms ps`):**
  - `lms status` exited with `0` (`Server: OFF`).
  - `lms ps` exited with `0`, but wrote its informational message (`"No models are currently loaded. To load a model, run: lms load <model path>"`) to **stderr**.
  - **Root cause of Exit Code 1 in the previous combined shell invocation:** PowerShell wraps native command `stderr` lines in `ErrorRecord` (`NativeCommandError`) when redirected via `2>&1`; the outer PowerShell session exited with code `1` because `$Error` was non-empty from `lms ps`'s stderr output, even though both `lms status` (`$LASTEXITCODE=0`) and `lms ps` (`$LASTEXITCODE=0`) succeeded natively. This is an expected PowerShell stderr-redirection artifact when no model is loaded in LM Studio, not a tool or CLI failure.
- **Prepared v2 host entry (`mcp.json.example`):**
  - Validated programmatically via `python -m json.tool`.
  - Preserves the existing `"Search-Tools"` legacy entry verbatim and adds a separate, portable `"Ultimate-MCP-v2"` entry using `"command": "uv"` and placeholder paths (`"C:/PATH/TO/Ultimate-MCP-Server"` and `"C:/PATH/TO/ALLOWED_WORKSPACE"`). On this Windows machine, replace `"uv"` with `"C:/Users/<username>/.local/bin/uv.exe"` (if `uv` is not on the system-wide GUI `PATH`) and `"C:/PATH/TO/Ultimate-MCP-Server"` with `"D:/Coding/projects/Ultimate-MCP-Server-v2-Agent-Pack"`, using literal paths rather than unverified environment-variable expansion inside `args`.
  - The live `%USERPROFILE%\.lmstudio\mcp.json` was verified untouched.
- **UI Verification Limitation:**
  Interactive LM Studio desktop UI testing (server toggle, UI tool discovery, natural-language tool call with `gemma-4-e2b-it-uncensored` or `qwen3.5-4b-uncensored-hauhaucs-aggressive`) **cannot be automated from this headless shell session and remains `pending` manual verification**. Protocol-level stdio startup, tool discovery, `calculate`, `get_current_time`, workspace-scoped `read_text_file`, and `prepare_write_file` / `execute_approved_action` have been verified end-to-end via the official MCP SDK `stdio_client` in `tests/integration/test_v2_stdio_server.py`.

## 4. Current Architecture & Important Decisions

1. **Hybrid Modular Monolith (`src/ultimate_mcp/`):**
   - `core/config.py`: Single `Settings` (`pydantic-settings`, `populate_by_name=True`), platform-aware `state_dir`/`output_dir`, conservative root check rejecting drive root or home root unless `ULTIMATE_MCP_ALLOW_BROAD_ROOTS=true`.
   - `core/redaction.py`: Scrubbing for sensitive keys, configured secret values, and token patterns (`Bearer`, `tvly-`, `AIza`, `sk-`); JSON-line logging strictly to `stderr` + optional log file.
   - `core/models.py` & `core/bootstrap.py`: `ToolResult` envelope (`success`, `partial`, `needs_approval`, `error`) with 18 stable `ErrorCode` values, `Provenance`, `ResultMetadata`, artifact spillover when output exceeds `max_output_bytes`, and native `CallToolResult(is_error=result.is_error)` return adaptation so MCP SDK v2 does not corrupt JSON payloads on tool error.
   - `core/execution.py`: `ExecutionManager` enforcing category timeouts (`local`, `network`, `subprocess`), global and per-provider semaphores, cancellation propagation, transient-only exponential backoff + jitter retries for idempotent L0/L1 operations, and SQLite `audit_events` persistence.
   - `core/permissions.py`, `core/filesystem.py`, `core/approvals.py`, `storage/db.py`: Fail-closed L0–L4 policy, workspace-scoped path resolution (blocking `..`, symlink escapes, UNC shares, NUL bytes, and Windows reserved names), and SQLite WAL-mode `ApprovalBroker` bound to canonical SHA-256 payload digests, target file pre-state hashes, TTL expiry, and single-use atomic consumption via `ultimate-mcp approvals` CLI.

## 5. Remaining Risks & Manual Checks

- **Manual check pending:** LM Studio desktop UI test with the `"Ultimate-MCP-v2"` entry in `%USERPROFILE%\.lmstudio\mcp.json`.
- **Open Phase 00 risks scheduled for Phase 02:**
  - **R4 (High):** SSRF, unrestricted hosts, redirect chains, and unbounded downloads in webpage reading.
  - **R7 (Medium):** YouTube transcript broken on `youtube-transcript-api` 1.2.x (must use instance `.fetch()` API).
  - **R9 (Medium):** Market price claims "Live" without currency, provider timestamp, or market state.
  - **R10 (Medium):** Migrate `duckduckgo-search` to `ddgs` (`PyPDF2` → `pypdf` follows in Phase 03).
  - **R13 & R14 (Low):** Remove DuckDuckGo keyword override in `general` mode and fragile ticker rewriting in `get_market_price`.

## 6. Exact Next Action (Phase 02 — Research Engine and Utilities)

Once the user authorizes proceeding past this checkpoint:

1. **Slice 2.1 — SSRF-Hardened HTTP Client & TTL Cache (`src/ultimate_mcp/core/http.py`, `src/ultimate_mcp/core/cache.py`):**
   - Permit only `http` and `https`; reject userinfo credentials and non-standard ports (unless allowlisted).
   - Resolve DNS (`socket.getaddrinfo`) and block loopback, private, link-local, multicast, reserved,unspecified, and cloud metadata IPs (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `::1`, `fc00::/7`, `fe80::/10`).
   - Disable automatic redirects; manually follow up to `max_redirects=4` while re-validating scheme, host, and DNS on every hop.
   - Enforce connect/read/total timeouts, streaming byte limits (`max_response_bytes`), and content-type allowlists.
   - Add per-data-class TTL cache (`cache.py`) that marks cached responses with explicit freshness metadata.
2. **Slice 2.2 — Provider Interfaces & Utilities (`convert_units`, `get_weather`, `get_market_price`, `get_exchange_rate`):**
   - Define `SearchProvider`, `WeatherProvider`, `MarketDataProvider`, `YouTubeProvider`, and `RSSProvider` protocols in `src/ultimate_mcp/providers/base.py`.
   - Implement `convert_units` (`modules/utilities/units.py`), wttr.in JSON weather adapter (`providers/weather/`), and `yfinance` market/exchange-rate adapter (`providers/finance/`) with explicit timestamps, currencies, market state, and freshness caveats, plus safe `calculate_or_time` compatibility wrapper.
3. **Slice 2.3 — Search, Web Extraction & RSS (`web_search`, `search_news`, `search_official_docs`, `search_academic`, `multi_search`, `read_webpage`, `extract_links`, `extract_tables`, `crawl_website`, `rss_search`, plus `limited_web_search` and `read_article` wrappers):**
   - Lazy Tavily (`tavily-python`) and DuckDuckGo (`ddgs`) adapters, OpenAlex/arXiv academic search adapter, bounded HTML extraction (`beautifulsoup4`), bounded same-domain crawler, and RSS/Atom parser (`xml.etree.ElementTree` + `defusedxml`-style entity protection).
4. **Slice 2.4 — YouTube Split & Deterministic Evidence Workflows (`youtube_search`, `youtube_transcript`, `youtube_comments`, `youtube_expert` wrapper, `compare_sources`, `verify_claim`, `deep_research`, `research_report`):**
   - Use `YouTubeTranscriptApi().fetch(video_id, languages=...)` for 1.2.x compatibility; implement deterministic bounded evidence workflows with zero hidden LLM calls.
5. **Slice 2.5 — Offline Tests, Profile Footprint Measurement & Phase 02 Report.**
