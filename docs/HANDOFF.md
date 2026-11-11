# Ultimate MCP Server v2 — Model-Independent Handoff (`docs/HANDOFF.md`)

> **Authoritative state document for model switches.** Always verify against `git status`, `git log`, and test execution before making changes.

## 1. Current Repository & Git State

- **Last updated:** 2026-11-12T00:56+02:00
- **Branch:** `feat/v2-phase-3` (branched from finalized Phase 02 checkpoint `1a97327` on `feat/v2-phase-2`)
- **Latest commit on `feat/v2-phase-3`:** `38da44c` — `feat(phase3): add browser security and network egress prototype` (with uncommitted Phase 03 Slice 3.1.1 + Browser Engine milestone changes awaiting review/authorization before commit)
- **Finalized Phase 02 checkpoint commit (`feat/v2-phase-2`):**
  - `1a97327` (`1a973272e32cc97b477810353465fd2c9b96fab7`) — `fix(phase2): finalize audit hardening and cache expiration`
- **Untouched legacy files & live configurations:**
  - `ultimate_mcp_server.py` and `requirements.txt` remain untouched at their `aafcce6` baseline.
  - Separate live LM Studio server directory `D:\Coding\projects\Python\mcp search server for LM Studio\Ultimate MCP Server\` was **never modified**.
  - User's live `%USERPROFILE%\.lmstudio\mcp.json` was **never modified**; portable side-by-side configuration (`Search-Tools` + `Ultimate-MCP-v2`) is in `mcp.json.example` (`2385d3e`).

## 2. Phase 03 Browser Milestone Verification Results

| Check / Command | Exit Code | Verified Result |
|---|---|---|
| `uv run --no-sync pytest -p no:cacheprovider --cov=ultimate_mcp --cov-report=term -v` | `0` | **150 passed in 32.78s**, **85% total statement coverage** (4,420 stmts, 643 missed) |
| `uv run --no-sync ruff check src tests` | `0` | `All checks passed!` |
| `uv run --no-sync mypy` | `0` | `Success: no issues found in 60 source files` (`strict = true`) |
| `.\.venv-baseline\Scripts\python.exe -m pytest tests/characterization -p no:cacheprovider -q` | `0` | **56 passed, 4 warnings in 7.15s** (all Phase 00 characterization tests preserved) |
| `uv run --no-sync ultimate-mcp doctor` | `0` | `Ultimate MCP Server v2.0.0a1 (Python 3.12.10)`, `Profile: default | SQLite FTS5: True`, 5 enabled default tools |
| `uv run --no-sync ultimate-mcp doctor --profile browser --json` | `0` | `browser.playwright_installed: true`, `browser.chromium_installed: true`, 15 enabled tools (14 browser tools + `get_current_time`) |

## 3. LM Studio Host & UI Verification Status

- **Installed LM Studio version:** `0.4.28.0` (`lms` CLI commit `43d4b26`).
- **UI Verification Limitation:** Interactive LM Studio desktop UI testing remains **`pending` manual verification** (`tests/integration/test_v2_stdio_server.py` verifies protocol-level stdio startup, tool discovery, and tool calls via `mcp.client.stdio`).

## 4. Phase 03 Implemented Browser Architecture (`src/ultimate_mcp/modules/browser/`)

All browser modules stay strictly under 300 lines per file and remain optional (`optional_packages=("playwright",)`), so core startup and `default` profile never import Playwright.

1. **Three-Layer Egress & WebRTC Hardening (`policy_types.py`, `proxy.py`, `egress.py`, `mutation_auth.py`):**
   - **Chromium launch args:** Uses `--host-resolver-rules=MAP * ~NOTFOUND` (zero `EXCLUDE 127.0.0.1`), or with `LoopbackValidatingProxy` binds proxy on `127.0.0.1:0` with `MAP egress-proxy.invalid 127.0.0.1 , MAP * ~NOTFOUND`, `--proxy-server=http://egress-proxy.invalid:<port>`, `--proxy-bypass-list=<-loopback>`, `--disable-quic`, `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`, `--enforce-webrtc-ip-permission-check`, and `WEBRTC_DISABLE_INIT_SCRIPT`.
   - **Empirical Chromium `158.0.8127.18` WebRTC finding:** Standalone `disable_non_proxied_udp` stops raw UDP STUN/TURN (`udp4=[]`, `udp6=[]`) but `turn:...transport=tcp` still opens direct TCP unless routed through host-resolver/proxy lockdown (`CONNECT` hits proxy -> `403`, zero hits on UDP/TCP STUN/TURN listeners). Windows OS-level limitations (`WINDOWS_ISOLATION_LIMITATION`) and optional Defender Firewall helpers (`build_windows_firewall_guidance`) are explicitly documented in `docs/SECURITY_MODEL.md`.
   - **Context & transport isolation:** Ephemeral `BrowserContext` (`service_workers="block"`, `accept_downloads=False`, `permissions=[]`), `context.route_web_socket("**/*", ...)` aborting `ws://`/`wss://`, and `context.route("**/*", ...)` fulfilling strictly via pinned-IP `fetch_safe_url`.
   - **Canonical L3 mutation binding (`mutation_auth.py`):** `prepare_browser_mutation_approval` + `MutationApprovalGate` bind single-use `ApprovalBroker` tokens (`L3_SENSITIVE`) to canonical `method`, `origin`, `url`, `content_type`, `request_origin_header`, `auth_context_sha256`, `session_state_sha256` (avoiding raw secret/cookie storage in SQLite or `audit_log`), `headers_sha256`, `session_id`, and `body_sha256`, with `max_redirects=0`.

2. **Full Browser Engine & 14 MCP Tools (`manager.py`, `navigation.py`, `interactions.py`, `forms.py`, `transfers.py`, `tools.py`, `action_tools.py`):**
   - **Session & tab isolation (`manager.py`):** Per-task isolated `BrowserContext` sessions (`max_sessions=3`, `max_tabs=5` popup cap), plus offline `check_playwright_browser_binaries()` doctor diagnostic.
   - **Navigation & read (`navigation.py`):** `browser_open`, `browser_back`, `browser_tabs` (`list`, `new`, `switch`, `close`), `browser_snapshot` (bounded untrusted page warning + `e1..eN` refs), `browser_screenshot` (saves PNG inside `output_dir/screenshots` without returning huge base64), `browser_scroll`, and bounded `browser_wait` (`<= 15s`).
   - **Interactions (`interactions.py`):** `browser_click`, `browser_type`, `browser_select` with `_resolve_unique_locator` missing/ambiguity checks and hard blocking of `<input type="submit|image">` / `<form>` submit buttons in `browser_click` and `<input type="file">` in `browser_type`.
   - **L3 approval-gated forms & transfers (`forms.py`, `transfers.py`):** `browser_submit_form` requires out-of-band `L3_SENSITIVE` `ApprovalBroker` approval (bound via `MutationApprovalGate` for `POST/PUT/PATCH/DELETE` and exact canonical payload for `GET`); `browser_download` validates pinned-IP URLs, blocks executable extensions (`.exe`, `.bat`, `.ps1`, `.cmd`, `.msi`, `.dll`, `.scr`, etc.) pre- and post-redirect, and atomically writes inside `output_dir/downloads`; `browser_upload` enforces `effective_read_roots`, file SHA-256 + `target_file_hash` tamper detection, and origin/selector binding.
   - **Cleanup (`action_tools.py`):** `browser_close_session` closes isolated sessions cleanly.
   - **Registration (`bootstrap.py`):** Exposed strictly under `ProfileName.BROWSER` and `ProfileName.FULL` with `optional_packages=("playwright",)` (default profile remains 5 tools; missing Playwright degrades gracefully without error).

## 5. Next Steps on `feat/v2-phase-3`

1. Await user review and commit authorization for the Browser Security + Browser Engine milestone.
2. Continue Phase 03 Section B & C (`phases/PHASE-03_BROWSER_DOCUMENTS.md`): Document Intelligence architecture and tools (`document_read`, `document_search`, `document_extract_tables`, `document_qa`, `document_compare`, `document_convert`, `document_create`, `document_edit`, `image_ocr`), bounded archive/size extraction, and Phase 03 completion report.
