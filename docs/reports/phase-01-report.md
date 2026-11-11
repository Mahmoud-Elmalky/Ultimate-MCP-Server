# Phase 01 Report — Core Foundation, Reliability, Security, and Packaging

## Status
- Status: `complete`
- Commit/branch (if known): `feat/v2-phase-0` (working tree ready for user-authorized commit; base `bdafc65`)
- Date: 2026-11-11

## Summary
- Implemented the modular `ultimate_mcp` v2 package on official MCP Python SDK v2 (`mcp 2.3.0`) with a locked `uv.lock`.
- Built centralized validated settings (`Settings`), secret redaction, structured stderr/file logging, standardized `ToolResult`/`ErrorCode` contracts, bounded output budgets with artifact spillover, `ExecutionManager` (timeouts, semaphores, cancellation, transient-only retry), fail-closed L0–L4 permission evaluation, SQLite-backed out-of-band `ApprovalBroker`, workspace-scoped path resolution, and the safe AST calculator (`calculate`) + `zoneinfo` clock (`get_current_time`).
- Preserved `ultimate_mcp_server.py` and all 56 Phase 00 characterization tests untouched and passing.

## Implemented
- **Packaging & CLI (`pyproject.toml`, `uv.lock`, `src/ultimate_mcp/cli.py`, `src/ultimate_mcp/app.py`):**
  - `ultimate-mcp serve [--profile default|research|coding|documents|browser|full]`
  - `ultimate-mcp doctor [--profile <p>] [--json]`
  - `ultimate-mcp catalog [--profile <p>]`
  - `ultimate-mcp approvals list|approve|reject`
- **Configuration & Redaction (`core/config.py`, `core/redaction.py`):**
  - Validated `Settings` via `pydantic-settings` (`populate_by_name=True`).
  - Conservative root validation rejecting drive roots (`C:\`, `/`) and user home directory unless `ULTIMATE_MCP_ALLOW_BROAD_ROOTS=true`.
  - Automatic secret redaction for known secret values, sensitive keys, and token patterns (`Bearer`, `tvly-`, `AIza`, `sk-`).
  - Structured JSON logging strictly to `stderr` (and optional log file), keeping `stdout` clean for MCP stdio.
- **Result/Error Contract (`core/models.py`):**
  - `ToolResult` (`success`, `partial`, `needs_approval`, `error`), `ErrorDetail` with 18 stable `ErrorCode` values, `Provenance`, `ResultMetadata`.
  - Native `CallToolResult(is_error=...)` adaptation so MCP hosts receive structured JSON payloads and accurate `is_error` flags without double-wrapping.
  - Response byte-budget enforcement (`max_output_bytes`) saving full payloads to `output_dir` and returning bounded previews with explicit warnings and `artifact_path` / `next_offset`.
- **Execution Manager (`core/execution.py`):**
  - Per-category timeouts (`local`, `network`, `subprocess`), global and per-provider concurrency semaphores, cancellation propagation, and exponential backoff + jitter retry restricted strictly to idempotent L0/L1 operations.
  - SQLite `audit_events` recording per tool call.
- **Permissions, Path Policy & Approval Broker (`core/permissions.py`, `core/filesystem.py`, `storage/db.py`, `core/approvals.py`):**
  - L0–L4 permission evaluation that fails closed on unknown or `L4_DENIED` levels.
  - `resolve_scoped_path`, `read_text_bounded`, and `atomic_write_text` blocking path traversal (`..`), symlink escapes outside roots, UNC paths, NUL bytes, and Windows reserved device names (`CON`, `NUL`, etc.).
  - SQLite WAL-mode `ApprovalBroker` with canonical SHA-256 payload digest, TTL expiry, target file pre-state hash verification, atomic single-use `APPROVED -> CONSUMED` transition, and out-of-band CLI decision flow. No MCP tool can approve an action.
- **Core Tools (`modules/utilities/`, `modules/documents/reader.py`, `modules/system/actions.py`):**
  - `calculate`: strict AST allowlist with length, node count, recursion depth, exponent, factorial, and magnitude bounds.
  - `get_current_time`: IANA timezone clock via stdlib `zoneinfo` + `tzdata`.
  - `read_text_file`: workspace-scoped bounded UTF-8 file reader with offset pagination.
  - `prepare_write_file` & `execute_approved_action`: two-phase approval-gated atomic file write.

## Files added/changed
- Added: `pyproject.toml`, `uv.lock`, `mcp.json.example`
- Updated: `.gitignore`, `.env.example`, `README.md`
- Added source (`src/ultimate_mcp/`):
  - `__init__.py`, `__main__.py`, `app.py`, `cli.py`
  - `core/__init__.py`, `core/approvals.py`, `core/bootstrap.py`, `core/config.py`, `core/execution.py`, `core/filesystem.py`, `core/models.py`, `core/permissions.py`, `core/redaction.py`, `core/registry.py`
  - `storage/__init__.py`, `storage/db.py`
  - `modules/__init__.py`, `modules/utilities/__init__.py`, `modules/utilities/calculator.py`, `modules/utilities/clock.py`
  - `modules/documents/__init__.py`, `modules/documents/reader.py`
  - `modules/system/__init__.py`, `modules/system/actions.py`
  - `providers/__init__.py`, `workflows/__init__.py`
- Added tests:
  - `tests/unit/test_config_models_registry.py`
  - `tests/unit/test_execution_manager.py`
  - `tests/security/test_security_phase1.py`
  - `tests/integration/test_v2_stdio_server.py`
- Added docs:
  - `docs/reports/phase-01-report.md`
  - `docs/HANDOFF.md`

## Tests and commands actually executed
| Command | Exit/result | Notes |
|---|---|---|
| `uv sync --extra dev` | 0 | Resolved and locked 51 packages in `uv.lock` (`mcp==2.3.0`) |
| `uv run ultimate-mcp doctor` | 0 | Verified zero-key startup, SQLite FTS5=True, 5 default tools |
| `uv run pytest -p no:cacheprovider --cov=ultimate_mcp --cov-report=term-missing` | 0 | **57 passed** in 6.75s, **88% total coverage** |
| `uv run ruff check src tests` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 26 source files` (`strict = true`) |
| `.venv-baseline\Scripts\python -m pytest tests/characterization -p no:cacheprovider -q` | 0 | **56 passed** in 7.01s (legacy characterization intact) |
| `.venv\Scripts\python scratch/measure_v2.py` | 0 | Startup 1.41–1.46s, idle working set 69.6 MiB, 5 tools = 3,029 chars |

## Security review
- Threats checked:
  - Arbitrary code execution / DoS via calculator expressions (`(1).__class__`, `__import__`, `9**9**9`, `2**5000`, `factorial(500)`).
  - Path traversal (`..`), outside-root absolute paths, symlink escapes, UNC shares (`\\server\share`), Windows device names (`CON`, `NUL`), and NUL bytes.
  - Approval bypass, pending execution, replay after consumption, expired approval, payload tampering, and post-approval target file modification.
  - Model self-approval (verified no approval-granting tool is registered in MCP).
  - Secret leakage in settings dumps, structured logs, and unhandled exception responses.
- Tests passed/failed: 57/57 v2 unit, security, and stdio integration tests passed.
- Remaining risks:
  - R1 (unpinned deps), R2 (eval calculator), R5 (eager Tavily crash), R6 (silent error strings), R8 (silent truncation), R11 (heavy eager imports), and R12 (exception leakage) are **closed in the v2 runtime**.
  - Legacy `ultimate_mcp_server.py` still exists for incremental migration (R3, R4, R7, R9, R10, R13, R14 remain open until Phases 02–03 migrate research, market, weather, YouTube, and document tools).

## Dependencies
- Added/removed:
  - Core: `mcp[cli]>=2.3,<3` (locked at `2.3.0`), `pydantic>=2.10,<3`, `pydantic-settings>=2.6,<3`, `platformdirs>=4.2,<5`, `tzdata>=2024.1`.
  - Dev extra: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`.
- Why: Required for official MCP SDK v2 protocol handling, typed configuration, cross-platform state paths, and Windows IANA timezone support without `pytz`.
- Core vs optional: Heavy libraries (`httpx`, `beautifulsoup4`, `lxml`, `tavily-python`, `ddgs`, `yfinance`, `google-api-python-client`, `youtube-transcript-api`, `playwright`, `pypdf`, `python-docx`, `openpyxl`, `python-pptx`, `pandas`, `numpy`, `psutil`, `Pillow`) are strictly isolated in optional extras.

## Compatibility
- Python: 3.12.10 in `.venv` (compatible with `>=3.11`).
- MCP SDK: `mcp 2.3.0` (`from mcp.server import MCPServer`, `CallToolResult` / `input_schema` snake_case verified over real stdio).
- LM Studio version/host test: LM Studio `0.4.28.0` installed locally; real stdio client handshake verified via SDK `stdio_client`. Live LM Studio UI test remains a manual user step.
- Models tested: None in Phase 01 (protocol and deterministic tool tests only).
- Not tested: Live LM Studio UI interaction, Streamable HTTP transport under load.

## Performance
- Startup: `create_gateway()` warm startup **1.41–1.46 s** across 3 runs (vs. 2.52–2.68 s in legacy baseline — **~44% faster**).
- Idle memory: **69.6 MiB** working set (+8.4 MiB venv launcher) (vs. 128.8 MiB in legacy baseline — **~46% lower RAM**).
- Peak memory: **69.6 MiB** during idle stdio session.
- Tool schema footprint (`default` profile, 5 tools): **3,029 chars (~757 estimated tokens)** vs. 10,134 chars (~2,533 tokens) in legacy baseline.
- Measurement environment: Windows, Intel Core i7-12700H, 16 GB RAM, `.venv` Python 3.12.10, `mcp 2.3.0`.

## Acceptance criteria
| Criterion | Result | Evidence |
|---|---|---|
| Core server starts without Tavily/YouTube keys and without optional deps | Pass | `test_doctor_and_profiles_without_api_keys`, `test_v2_stdio_protocol_handshake_and_tool_calls` |
| Profiles expose only enabled/implemented tools | Pass | `test_registry_should_filter_by_profile_and_missing_dependencies`, `test_doctor_and_profiles_without_api_keys` |
| Protected action cannot execute without out-of-band approval | Pass | `test_approval_lifecycle_and_security_invariants`, `test_v2_stdio_protocol_handshake_and_tool_calls` |
| Approval is exact-payload, expiring, one-time, cannot be self-approved | Pass | `test_approval_lifecycle_and_security_invariants` (7 invariants verified) |
| Unsafe calculator evaluation is absent | Pass | `test_calculator_should_reject_unsafe_or_dos_expressions` (19 adversarial cases) |
| Filesystem helper blocks traversal and symlink escapes | Pass | `test_filesystem_should_block_traversal_and_outside_paths`, `test_filesystem_should_block_symlink_escape` |
| Timeouts/output limits/error taxonomy work in tests | Pass | `test_execution_manager.py`, `test_tool_result_output_budget_should_bound_and_save_artifact` |
| Legacy server remains available until tool-by-tool migration is complete | Pass | 56 characterization tests in `.venv-baseline` pass |

## Known limitations / follow-up
- Research, market, weather, and YouTube tools are scheduled for Phase 02 (`phases/PHASE-02_RESEARCH_UTILITIES.md`).
- Rich document formats (PDF/DOCX/PPTX/XLSX) and Playwright browser automation are scheduled for Phase 03.

## Recommendation
- Proceed to next phase? `yes`
- Reason: All Phase 01 acceptance criteria, security invariants, coverage (88%), strict type checks, and lint checks pass with measured startup and memory improvements.
