# Ultimate MCP Server v2 — Model-Independent Handoff (`docs/HANDOFF.md`)

> **Authoritative state document for model switches.** Always verify against `git status`, `git log`, and test execution before making changes.

## 1. Current Repository & Git State

- **Last updated:** 2026-11-11T22:58+02:00
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
- **Working tree:** Contains the complete, post-audit hardened **Phase 02 — Research Engine and Utilities** implementation, tests, and `docs/reports/phase-02-report.md` (uncommitted pending user authorization).
- **Untouched legacy files & live configurations:**
  - `ultimate_mcp_server.py` and `requirements.txt` remain untouched at their `aafcce6` baseline.
  - Separate live LM Studio server directory `D:\Coding\projects\Python\mcp search server for LM Studio\Ultimate MCP Server\` was **never modified**.
  - User's live `%USERPROFILE%\.lmstudio\mcp.json` was **never modified**; portable side-by-side configuration with both `Search-Tools` (legacy) and `Ultimate-MCP-v2` (v2) is committed in `mcp.json.example` (`2385d3e`).

## 2. Phase 02 Post-Audit Verification Results (Executed 2026-11-11T22:56+02:00)

| Check / Command | Exit Code | Verified Result |
|---|---|---|
| `uv run pytest -p no:cacheprovider --cov=ultimate_mcp --cov-report=term-missing` | `0` | **121 passed in 10.47s**, **87% total statement coverage** (3,392 stmts, 438 missed) |
| `uv run ruff check src tests` | `0` | `All checks passed!` |
| `uv run mypy` | `0` | `Success: no issues found in 47 source files` (`strict = true`) |
| `.\.venv-baseline\Scripts\python.exe -m pytest tests/characterization -p no:cacheprovider -q` | `0` | **56 passed in 7.83s** (all Phase 00 characterization tests preserved) |
| `uv run ultimate-mcp doctor` | `0` | `Ultimate MCP Server v2.0.0a1 (Python 3.12.10)`, `Profile: default | SQLite FTS5: True`, 5 enabled default tools |

### Measured Profile Tool-Definition Footprints
JSON schemas contain dense structural punctuation (`{`, `}`, `:`, `"`, `,`), so `chars / 4` underestimates actual LLM subword tokens by ~35–45%. Both compact JSON bytes, `chars / 4`, and structural BPE lexical token counts (`[A-Za-z0-9]+|[^A-Za-z0-9\s]`) are recorded below:

- **`default` (5 tools):** `2,833 B` | `chars/4 = 708` | `BPE lex tokens = 963` (fits 4k+ local models)
- **`youtube` (4 tools):** `2,237 B` | `chars/4 = 559` | `BPE lex tokens = 803` (fits 4k+ local models)
- **`live_data` (6 tools):** `3,307 B` | `chars/4 = 826` | `BPE lex tokens = 1,210` (fits 4k–8k+ local models)
- **`search` (7 tools):** `3,814 B` | `chars/4 = 953` | `BPE lex tokens = 1,385` (fits 4k–8k+ local models)
- **`web_research` (7 tools):** `3,975 B` | `chars/4 = 993` | `BPE lex tokens = 1,518` (fits 4k–8k+ local models)
- **`fact_check` (7 tools):** `4,555 B` | `chars/4 = 1,138` | `BPE lex tokens = 1,615` (fits 4k–8k+ local models)
- **`research` (23 tools):** `13,834 B` | `chars/4 = 3,458` | `BPE lex tokens = 5,088` (recommended for 16k+ context models)
- **`full` (30 tools):** `17,326 B` | `chars/4 = 4,331` | `BPE lex tokens = 6,356` (opt-in only for 32k+ context models)

## 3. LM Studio Host & UI Verification Status

- **Installed LM Studio version:** `0.4.28.0` (`lms` CLI commit `43d4b26`).
- **Explanation of Exit Code 1 in Earlier `lms status` / `lms ps` Check:**
  - `lms status` exited with `0` (`Server: OFF`).
  - `lms ps` exited with `0`, but wrote its informational message (`"No models are currently loaded. To load a model, run: lms load <model path>"`) to **stderr**.
  - When redirected via `2>&1` in PowerShell, native `stderr` lines are wrapped in `ErrorRecord` (`NativeCommandError`), causing the outer PowerShell session to exit with code `1` because `$Error` was non-empty, even though both `lms status` (`$LASTEXITCODE=0`) and `lms ps` (`$LASTEXITCODE=0`) succeeded natively.
- **UI Verification Limitation:**
  - Interactive LM Studio desktop UI testing remains **`pending` manual verification** (`tests/integration/test_v2_stdio_server.py` verifies protocol-level stdio startup, tool discovery, and tool calls via `mcp.client.stdio`).

## 4. Current Architecture & Phase 02 Technical Audit Hardening Decisions

1. **9-Step Pinned-IP SSRF & DNS-Rebinding Protection (`core/http.py`, `core/http_transport.py`):**
   - `validate_and_resolve_url` validates scheme (`http`/`https`), forbids userinfo credentials, blocks internal hostnames/suffixes, enforces port allowlists (`80`, `443`), and validates **all** resolved A and AAAA records (rejecting mixed public/private responses immediately).
   - `send_pinned_http_request` eliminates any second unvalidated HTTP-client DNS lookup by connecting the TCP socket directly to `(pinned_ip, effective_port)` where `pinned_ip = pre_ips[0]`.
   - Immediately after TCP connect and **before** TLS handshake or sending any HTTP bytes, `verify_connected_peer_ip` checks `sock.getpeername()[0]` against `pre_ips` and `is_public_ip_address`. If the peer IP is private or unapproved, the socket is closed immediately with `0` HTTP bytes transmitted.
   - For `https`, the verified socket is wrapped with `ssl.create_default_context().wrap_socket(sock, server_hostname=expected_host)` so TLS SNI and X.509 certificate hostname verification use the original hostname.
   - Requests send `Host: <expected_host>` and `Connection: close` (preventing connection pool reuse), ignore environment proxy variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`), stream/decompress up to `max_response_bytes`, and re-validate every redirect hop independently.
2. **Thread-Safe Per-Class TTL Cache (`core/cache.py`):**
   - Protected by `threading.RLock()` for concurrent worker-thread execution (`asyncio.to_thread`).
   - Enforces both global `max_entries=256` and per-data-class `max_entries_per_class=64`, purging expired items before evicting live entries via LRU.
   - `build_cache_key` rejects sensitive parameter keys (`api_key`, `token`, `password`, `authorization`, `cookie`, `session_id`) unless `allow_sensitive_policy=True`.
   - `NO_STALE_DATA_CLASSES = {MARKET_QUOTE, EXCHANGE_RATE}` guarantees financial quotes and FX rates are never served stale even if `allow_stale=True`.
3. **Conservative Deterministic Evidence Workflows (`modules/research/workflows.py`):**
   - `compare_sources_service` and `verify_claim_service` extract negation markers, directional polarity terms, ISO dates/4-digit years, and numeric quantities.
   - `compare_sources` emits calibrated `comparison_status` (`"Sources agree"`, `"Sources disagree"`, `"Insufficient evidence"`) and records pairwise conflicts.
   - `verify_claim` emits calibrated `verification_status` (`"Supporting evidence found"`, `"Conflicting evidence found"`, `"Insufficient evidence"`), treating any negation, numeric, date/year, or directional polarity mismatch on shared topic terms conservatively as conflicting evidence.

## 5. Remaining Risks & Next Phase (Phase 03 — Browser & Documents)

- **Closed Phase 00 Risks:** R1, R2, R4, R5, R6, R7, R8, R9, R10 (search), R11, R12, R13, R14.
- **Open Phase 00 Risks for Phase 03:**
  - **R3 (Critical):** Migrate legacy `read_local_file` into workspace-scoped multi-format document extraction (`document_read`, `document_search`, `document_qa`, `document_extract_tables`, `document_compare`, `document_convert`, `document_create`, `document_edit`, `image_ocr`) and keep `read_local_file` as a bounded compatibility wrapper.
  - **R10 (Documents):** Use `pypdf` instead of deprecated `PyPDF2`.
  - Implement Playwright browser automation adapter (`phases/PHASE-03_BROWSER_DOCUMENTS.md`).
