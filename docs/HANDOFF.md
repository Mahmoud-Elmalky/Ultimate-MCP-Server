# Ultimate MCP Server v2 — Model-Independent Handoff (`docs/HANDOFF.md`)

> **Authoritative state document for model switches.** Always verify against `git status`, `git log`, and test execution before making changes.

## 1. Current Repository & Git State

- **Last updated:** 2026-11-11
- **Branch:** `feat/v2-phase-0`
- **HEAD commit:** `bdafc65` (`docs: add v2 agent implementation pack for incremental migration`)
- **Working tree:** Contains completed **Phase 01 — Core Foundation** implementation (uncommitted per handoff git-safety rule: *"request user authorization before creating commits or pushing to GitHub"*).
- **Untouched legacy files:**
  - `ultimate_mcp_server.py` and `requirements.txt` remain untouched in the repository root.
  - Separate live LM Studio copy at `D:\Coding\projects\Python\mcp search server for LM Studio\Ultimate MCP Server\` was **never modified**.
  - User's `%USERPROFILE%\.lmstudio\mcp.json` was **never modified**.

## 2. Virtual Environments

Two isolated environments exist side by side:

1. **v2 development environment (`.venv`):**
   - Managed by `uv` (`pyproject.toml` + `uv.lock`, Python 3.12.10, `mcp==2.3.0`).
   - Run v2 tests/linters/typechecks:
     - `uv run pytest -p no:cacheprovider --cov=ultimate_mcp --cov-report=term-missing` → **57 passed, 88% coverage**
     - `uv run ruff check src tests` → **All checks passed!**
     - `uv run mypy` → **Success: no issues found in 26 source files** (`strict = true`)
2. **Legacy characterization environment (`.venv-baseline`):**
   - Pinned to the user's live LM Studio interpreter versions (`tests/characterization/requirements-baseline.txt`, `mcp==1.27.0`).
   - Run legacy characterization tests:
     - `.venv-baseline\Scripts\python -m pytest tests/characterization -p no:cacheprovider -q` → **56 passed**

## 3. Phase Status Overview

| Phase | Status | Report | Summary |
|---|---|---|---|
| **Phase 00 — Baseline Audit** | `complete` | `docs/reports/phase-00-report.md` | Audited legacy server, identified R1–R15 risks, added 56 characterization tests |
| **Phase 01 — Core Foundation** | `complete` | `docs/reports/phase-01-report.md` | Package layout, `Settings`, redaction, `ToolResult`/`ErrorCode`, `ExecutionManager`, L0–L4 permissions, SQLite `ApprovalBroker` + CLI, scoped filesystem, AST `calculate`, `get_current_time`, `read_text_file`, `prepare_write_file`, `execute_approved_action` |
| **Phase 02 — Research & Utilities** | `next` | `phases/PHASE-02_RESEARCH_UTILITIES.md` | SSRF-hardened web fetch/read, Tavily/DDG search providers, market price, weather, YouTube search/transcript/comments, unit conversion |
| **Phase 03–08** | `planned` | `phases/PHASE-03...08.md` | Browser & documents, coding & system, data & media, memory & knowledge, intelligence & workflows, evaluation & release |

## 4. Key Technical Decisions & Findings from Phase 01

1. **MCP Python SDK v2 (`mcp==2.3.0`) API:**
   - Import server via `from mcp.server import MCPServer`.
   - SDK v2 uses snake_case on `CallToolResult` (`result.is_error`, not `result.isError`) and `Tool` (`tool.input_schema`, not `tool.inputSchema`).
   - Raising `ToolError(text)` inside a tool wrapper causes `MCPServer` to prefix `"Error executing tool <name>: "` to the response text. To preserve valid JSON envelopes on tool failures while still setting MCP's `is_error=True`, tool wrappers return `mcp.types.CallToolResult(content=[TextContent(type="text", text=json_str)], is_error=result.is_error)` via `_adapt_result` in `src/ultimate_mcp/core/bootstrap.py`.
2. **Pydantic Settings field population:**
   - `Settings` sets `populate_by_name=True` in `SettingsConfigDict` so both Python field names (`Settings(read_roots=...)`) and environment variable aliases (`ULTIMATE_MCP_READ_ROOTS`) work seamlessly.
3. **Out-of-Band Approval Broker (`src/ultimate_mcp/core/approvals.py`):**
   - Backed by SQLite (`state/ultimate_mcp.sqlite3`) in WAL mode.
   - Binds each prepared action to a canonical SHA-256 digest of `(action_type, payload)` and captures target file pre-state hash (`MISSING` or `sha256:<hex>`).
   - Approved out-of-band via `uv run ultimate-mcp approvals approve <approval_id>` and consumed atomically once via `execute_approved_action`.
4. **Measured v2 Core Performance (Windows i7-12700H, 16 GB RAM):**
   - Warm gateway startup: **1.41–1.46 s** (down from 2.52–2.68 s in legacy).
   - Idle working set: **69.6 MiB** (down from 128.8 MiB in legacy).
   - Default profile schema footprint (5 tools): **3,029 chars (~757 tokens)** (down from 10,134 chars in legacy).

## 5. Risk Register Status

| ID | Risk | Status after Phase 01 |
|---|---|---|
| R1 | Unpinned deps break fresh install on MCP 2.x | **Closed in v2** (`pyproject.toml` + `uv.lock` with `mcp==2.3.0`) |
| R2 | Unsafe `eval` in calculator | **Closed in v2** (`modules/utilities/calculator.py` AST allowlist + bounds) |
| R3 | Unrestricted local file read / traversal | **Closed in v2 core** (`core/filesystem.py` + `read_text_file`); PDF/CSV migration in P3/P5 |
| R4 | SSRF in webpage reader | Open — implement SSRF guard + `read_webpage` in **Phase 02** |
| R5 | Missing Tavily key crashes startup | **Closed in v2** (zero-key startup verified) |
| R6 | Failures returned as normal text (`isError=false`) | **Closed in v2** (`ToolResult` + `CallToolResult(is_error=True)`) |
| R7 | YouTube transcript broken on `youtube-transcript-api` 1.2.x | Open — fix in **Phase 02** using instance `.fetch()` API |
| R8 | Silent truncation at 4,000 chars | **Closed in v2 core** (`to_mcp_text` artifact spillover + `read_text_file` offset pagination) |
| R9 | Market price claims "Live" without timestamp/currency | Open — fix in **Phase 02** |
| R10 | Deprecated deps (`duckduckgo-search`, `PyPDF2`, `pytz`) | `pytz` replaced by `zoneinfo`+`tzdata` in P1; `ddgs` in P2; `pypdf` in P3 |
| R11 | Heavy eager imports slow startup and raise RAM | **Closed in v2** (lazy optional extras; RAM reduced 46%) |
| R12 | Raw exception details leak to model | **Closed in v2** (`ExecutionManager` maps unhandled errors to redacted `INTERNAL_ERROR`) |
| R13–R14 | DDG keyword heuristic & ticker rewriting | Open — fix in **Phase 02** |
| R15 | Heavy tool descriptions (~2.5k tokens for 8 tools) | **Closed in v2 core** (concise descriptions; 5 tools = ~757 tokens) |

## 6. Exact Next Action (Phase 02 — Research & Utilities)

1. Read `phases/PHASE-02_RESEARCH_UTILITIES.md` and `docs/SECURITY_MODEL.md` §5 (Web and SSRF policy).
2. Implement the SSRF-safe HTTP fetching layer (`src/ultimate_mcp/core/http.py`):
   - Allow only `http`/`https`, reject credentials in URL, resolve DNS and block loopback/private/link-local/multicast/reserved/metadata IPs (`169.254.169.254`, `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `::1`, `fc00::/7`, `fe80::/10`), disable auto-redirects and revalidate every redirect hop (max 3–5 hops), enforce byte-stream download caps and content-type checks.
3. Implement `convert_units` and `get_exchange_rate` / `get_market_price` / `get_weather` / `web_search` (Tavily) / `search_news` & `multi_search` (DDGS) / `read_webpage` / `extract_links` / `extract_tables` / `rss_search` / `youtube_search` / `youtube_transcript` (using `YouTubeTranscriptApi().fetch(...)`) / `youtube_comments`, plus legacy compatibility wrappers where specified.
4. Add unit, SSRF security, and integration tests for all Phase 02 providers using offline mocks/fixtures.
