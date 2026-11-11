# Ultimate MCP Server v2 — Model-Independent Handoff (`docs/HANDOFF.md`)

> **Authoritative state document for model switches.** Always verify against `git status`, `git log`, and test execution before making changes.

## 1. Current Repository & Git State

- **Last updated:** 2026-11-11T23:34+02:00
- **Branch:** `feat/v2-phase-2`
- **Current HEAD commit:** `ffcca82` (`ffcca828d1ec7878448e6a6d7f98c0170b5b302f` — `fix(phase2): harden pinned-IP SSRF transport, evidence calibration, and TTL cache`)
- **Commit history on branch:**
  - `ffcca82` — `fix(phase2): harden pinned-IP SSRF transport, evidence calibration, and TTL cache`
  - `44b77c9` — `feat: implement phase 02 research engine, live-data adapters, and SSRF-hardened HTTP`
  - `2385d3e` — `docs: finalize phase 01 verification and phase 02 transition`
  - `36166b3` — `feat: implement phase 01 core foundation, security policy, and MCP v2 gateway`
  - `bdafc65` — `docs: add v2 agent implementation pack for incremental migration`
  - `61a9d45` — `docs: add phase 0 baseline audit, legacy tool inventory, and report`
  - `2d00c9d` — `test: add characterization suite for legacy MCP server`
  - `ae5304d` — `chore: ignore local virtualenvs and tool caches`
  - `aafcce6` (`origin/main`, `main`) — `Update ultimate_mcp_server.py`
- **Working tree:** Contains the complete, post-audit verified **Phase 02 — Research Engine and Utilities** implementation, tests, `docs/reports/phase-02-report.md`, and updated Phase 03 Browser Security specification in `phases/PHASE-03_BROWSER_DOCUMENTS.md` (uncommitted pending user authorization).
- **Untouched legacy files & live configurations:**
  - `ultimate_mcp_server.py` and `requirements.txt` remain untouched at their `aafcce6` baseline.
  - Separate live LM Studio server directory `D:\Coding\projects\Python\mcp search server for LM Studio\Ultimate MCP Server\` was **never modified**.
  - User's live `%USERPROFILE%\.lmstudio\mcp.json` was **never modified**; portable side-by-side configuration with both `Search-Tools` (legacy) and `Ultimate-MCP-v2` (v2) is committed in `mcp.json.example` (`2385d3e`).

## 2. Phase 02 Final Verification Results (Executed 2026-11-11T23:30+02:00)

| Check / Command | Exit Code | Verified Result |
|---|---|---|
| `uv run pytest -p no:cacheprovider --cov=ultimate_mcp --cov-report=term-missing` | `0` | **122 passed in 11.41s**, **87% total statement coverage** (3,448 stmts, 433 missed) |
| `uv run ruff check src tests` | `0` | `All checks passed!` |
| `uv run mypy` | `0` | `Success: no issues found in 48 source files` (`strict = true`) |
| `.\.venv-baseline\Scripts\python.exe -m pytest tests/characterization -p no:cacheprovider -q` | `0` | **56 passed in 7.53s** (all Phase 00 characterization tests preserved) |
| `uv run ultimate-mcp doctor` | `0` | `Ultimate MCP Server v2.0.0a1 (Python 3.12.10)`, `Profile: default | SQLite FTS5: True`, 5 enabled default tools |

### Measured Profile Tool-Definition Footprints
JSON schemas contain dense structural punctuation (`{`, `}`, `:`, `"`, `,`), so `chars / 4` underestimates subword tokens. Both `chars / 4` and the `Structural Regex Token Proxy` (`len(re.findall(r'[A-Za-z0-9]+|[^A-Za-z0-9\s]', payload))`, a regex-based structural proxy rather than an actual BPE tokenizer) are recorded below alongside recommended (unverified heuristic) context windows:

- **`default` (5 tools):** `2,833 B` | `chars/4 = 708` | `Structural Regex Token Proxy = 963` (4k+ recommendation)
- **`youtube` (4 tools):** `2,237 B` | `chars/4 = 559` | `Structural Regex Token Proxy = 803` (4k+ recommendation)
- **`live_data` (6 tools):** `3,307 B` | `chars/4 = 826` | `Structural Regex Token Proxy = 1,210` (4k–8k+ recommendation)
- **`search` (7 tools):** `3,814 B` | `chars/4 = 953` | `Structural Regex Token Proxy = 1,385` (4k–8k+ recommendation)
- **`web_research` (7 tools):** `3,975 B` | `chars/4 = 993` | `Structural Regex Token Proxy = 1,518` (4k–8k+ recommendation)
- **`fact_check` (7 tools):** `4,568 B` | `chars/4 = 1,142` | `Structural Regex Token Proxy = 1,619` (4k–8k+ recommendation)
- **`research` (23 tools):** `13,847 B` | `chars/4 = 3,461` | `Structural Regex Token Proxy = 5,092` (16k+ recommendation)
- **`full` (30 tools):** `17,339 B` | `chars/4 = 4,334` | `Structural Regex Token Proxy = 6,360` (32k+ opt-in recommendation)

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
   - Immediately after TCP connect and **before** TLS handshake (`ssl_wrapper`) or sending any HTTP bytes (`sendall`), `verify_connected_peer_ip` checks `sock.getpeername()[0]` against `pre_ips` and `is_public_ip_address`. If the peer IP is private or unapproved, the socket is closed immediately with `0` TLS/HTTP bytes transmitted.
   - For `https`, `default_ssl_wrapper` wraps the verified socket with `ssl.create_default_context().wrap_socket(sock, server_hostname=expected_host)` (`CERT_REQUIRED`, `check_hostname=True`).
   - Requests send `Host: <expected_host>` and `Connection: close` (preventing connection pool reuse), ignore environment proxy variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`), stream/decompress up to `max_response_bytes`, and re-validate every redirect hop on a new pinned socket.
2. **Thread-Safe Per-Class TTL Cache (`core/cache.py`):**
   - Protected by `threading.RLock()` for concurrent worker-thread execution (`asyncio.to_thread`).
   - Enforces both global `max_entries=256` and per-data-class `max_entries_per_class=64`, purging expired items before evicting live entries via LRU.
   - `build_cache_key` isolates by `(data_class, provider, user_scope, query, locale, params)`, rejects sensitive parameter keys and credential-like values, and rejects un-isolated user identifiers (`user_id`, `account_id`, `tenant_id`) in `public` scope.
   - `NO_STALE_DATA_CLASSES = {MARKET_QUOTE, EXCHANGE_RATE}` guarantees financial quotes and FX rates are never served stale even if `allow_stale=True`.
3. **Conservative Deterministic Evidence Workflows (`modules/research/evidence_signals.py`, `modules/research/workflows.py`):**
   - `compare_sources_service` and `verify_claim_service` extract negation markers, directional polarity terms, causal-vs-correlational modality, subject/object role order around directional verbs, ISO dates/4-digit years/time periods (`q1`–`q4`, months, seasons), numeric quantities, and population/context groups (`age_cohort`, `organism_model`, `geography`, `platform`).
   - `compare_sources` emits calibrated `comparison_status` (`"No contradiction detected"`, `"Conflicting evidence detected"`, `"Insufficient evidence"`).
   - `verify_claim` emits calibrated `verification_status` (`"Potential supporting evidence"`, `"Conflicting evidence detected"`, `"Insufficient evidence"`).

## 5. Phase 03 Readiness & Browser Security Architecture

- **Added `§A.1 Browser Security & Network Egress Architecture` to `phases/PHASE-03_BROWSER_DOCUMENTS.md`**, specifying how Phase 03 will govern main navigation, redirects, subresources, XHR/fetch, WebSockets, service workers (`service_workers="block"`), background requests, DNS rebinding (`--host-resolver-rules` + pinned-IP `route.fulfill` via `fetch_safe_url` / loopback validating proxy), downloads/uploads, ephemeral context isolation, and Windows platform limitations.
