# Ultimate MCP Server v2 — Model-Independent Handoff (`docs/HANDOFF.md`)

> **Authoritative state document for model switches.** Always verify against `git status`, `git log`, and test execution before making changes.

## 1. Current Repository & Git State

- **Last updated:** 2026-11-11T23:52+02:00
- **Branch:** `feat/v2-phase-2` (preparing final Phase 02 checkpoint commit before creating `feat/v2-phase-3`)
- **Prior Phase 02 commits:**
  - `1a9b92b` — `fix(phase2): calibrate evidence labels, harden cache scoping, and specify phase 03 browser security`
  - `ffcca82` — `fix(phase2): harden pinned-IP SSRF transport, evidence calibration, and TTL cache`
  - `44b77c9` — `feat: implement phase 02 research engine, live-data adapters, and SSRF-hardened HTTP`
  - `2385d3e` — `docs: finalize phase 01 verification and phase 02 transition`
  - `36166b3` — `feat: implement phase 01 core foundation, security policy, and MCP v2 gateway`
- **Untouched legacy files & live configurations:**
  - `ultimate_mcp_server.py` and `requirements.txt` remain untouched at their `aafcce6` baseline.
  - Separate live LM Studio server directory `D:\Coding\projects\Python\mcp search server for LM Studio\Ultimate MCP Server\` was **never modified**.
  - User's live `%USERPROFILE%\.lmstudio\mcp.json` was **never modified**; portable side-by-side configuration with both `Search-Tools` (legacy) and `Ultimate-MCP-v2` (v2) is committed in `mcp.json.example` (`2385d3e`).

## 2. Phase 02 Final Verification Results (Executed 2026-11-11T23:48+02:00)

| Check / Command | Exit Code | Verified Result |
|---|---|---|
| `uv run pytest -p no:cacheprovider --cov=ultimate_mcp --cov-report=term-missing` | `0` | **125 passed in 6.83s**, **88% total statement coverage** (3,448 stmts, 431 missed) |
| `uv run pytest -p no:cacheprovider tests/security/test_http_and_cache_phase2.py -q` | `0` | **56 passed in 0.22s** (HTTP/SSRF/pinned-IP + cache security tests) |
| `uv run ruff check src tests` | `0` | `All checks passed!` |
| `uv run mypy` | `0` | `Success: no issues found in 48 source files` (`strict = true`) |
| `.\.venv-baseline\Scripts\python.exe -m pytest tests/characterization -p no:cacheprovider -q` | `0` | **56 passed in 6.00s** (all Phase 00 characterization tests preserved) |
| `uv run ultimate-mcp doctor` | `0` | `Ultimate MCP Server v2.0.0a1 (Python 3.12.10)`, `Profile: default | SQLite FTS5: True`, 5 enabled default tools |

### Measured Profile Tool-Definition Footprints
JSON schemas contain dense structural punctuation (`{`, `}`, `:`, `"`, `,`), so `chars / 4` underestimates subword tokens. Neither `chars / 4` nor the `Structural Regex Token Proxy` (`len(re.findall(r'[A-Za-z0-9]+|[^A-Za-z0-9\s]', payload))`) is an actual model BPE tokenizer; context window sizes are recommendations, not verified per-model limits:

- **`default` (5 tools):** `2,833 B` | `chars/4 = 708` | `Structural Regex Token Proxy = 963` (4k+ recommendation)
- **`youtube` (4 tools):** `2,237 B` | `chars/4 = 559` | `Structural Regex Token Proxy = 803` (4k+ recommendation)
- **`live_data` (6 tools):** `3,307 B` | `chars/4 = 826` | `Structural Regex Token Proxy = 1,210` (4k–8k+ recommendation)
- **`search` (7 tools):** `3,814 B` | `chars/4 = 953` | `Structural Regex Token Proxy = 1,385` (4k–8k+ recommendation)
- **`web_research` (7 tools):** `3,975 B` | `chars/4 = 993` | `Structural Regex Token Proxy = 1,518` (4k–8k+ recommendation)
- **`fact_check` (7 tools):** `4,607 B` | `chars/4 = 1,151` | `Structural Regex Token Proxy = 1,622` (4k–8k+ recommendation)
- **`research` (23 tools):** `13,886 B` | `chars/4 = 3,471` | `Structural Regex Token Proxy = 5,095` (16k+ recommendation)
- **`full` (30 tools):** `17,378 B` | `chars/4 = 4,344` | `Structural Regex Token Proxy = 6,363` (32k+ opt-in recommendation)

## 3. LM Studio Host & UI Verification Status

- **Installed LM Studio version:** `0.4.28.0` (`lms` CLI commit `43d4b26`).
- **UI Verification Limitation:** Interactive LM Studio desktop UI testing remains **`pending` manual verification** (`tests/integration/test_v2_stdio_server.py` verifies protocol-level stdio startup, tool discovery, and tool calls via `mcp.client.stdio`).

## 4. Current Architecture & Phase 02 Technical Audit Hardening Decisions

1. **9-Step Pinned-IP SSRF & DNS-Rebinding Protection (`core/http.py`, `core/http_transport.py`):**
   - `validate_and_resolve_url` validates scheme (`http`/`https`), forbids userinfo credentials, blocks internal hostnames/suffixes, enforces port allowlists (`80`, `443`), and validates **all** resolved A and AAAA records (rejecting mixed public/private responses immediately).
   - `send_pinned_http_request` eliminates any second unvalidated HTTP-client DNS lookup by connecting the TCP socket directly to `(pinned_ip, effective_port)` where `pinned_ip = pre_ips[0]`.
   - Immediately after TCP connect and **before** TLS handshake (`ssl_wrapper`) or sending any HTTP bytes (`sendall`), `verify_connected_peer_ip` checks `sock.getpeername()[0]` against `pre_ips` and `is_public_ip_address`. If the peer IP is private or unapproved, the socket is closed immediately with `0` TLS/HTTP bytes transmitted.
   - Requests send `Host: <expected_host>` and `Connection: close`, ignore environment proxy variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`), stream/decompress up to `max_response_bytes`, and re-validate every redirect hop on a new pinned socket.
2. **Thread-Safe Per-Class TTL Cache (`core/cache.py`):**
   - Strict expiration boundary: fresh iff `now < expires_at`, expired iff `now >= expires_at` (both in `get()` and in `_purge_expired_locked()` during `put()`).
   - Protected by `threading.RLock()` for concurrent worker-thread execution (`asyncio.to_thread`).
   - Enforces both global `max_entries=256` and per-data-class `max_entries_per_class=64`, purging expired items before evicting live entries via LRU.
   - `build_cache_key` isolates by `(data_class, provider, user_scope, query, locale, params)`, rejects sensitive parameter keys and credential-like values, and rejects un-isolated user identifiers (`user_id`, `account_id`, `tenant_id`) in `public` scope.
   - `NO_STALE_DATA_CLASSES = {MARKET_QUOTE, EXCHANGE_RATE}` guarantees financial quotes and FX rates are never served at or after expiration, even with `allow_stale=True`.
3. **Conservative Deterministic Evidence Workflows (`modules/research/evidence_signals.py`, `modules/research/workflows.py`):**
   - Calibrated labels: `"No contradiction detected"`, `"Potential supporting evidence"`, `"Conflicting evidence detected"`, `"Insufficient evidence"`.

## 5. Phase 03 Readiness & First Milestone

- **Branch target:** Create `feat/v2-phase-3` from the finalized Phase 02 checkpoint commit.
- **First Slice:** Implement and test the **Browser Security & Network Egress** foundation (`src/ultimate_mcp/modules/browser/egress.py` + `tests/security/test_browser_egress_phase3.py`) following `phases/PHASE-03_BROWSER_DOCUMENTS.md` §A.1 before building the full Playwright toolset.
