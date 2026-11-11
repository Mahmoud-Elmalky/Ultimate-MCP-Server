# Phase 00 Report — Baseline Audit and Characterization

## Status
- Status: `complete` (with documented manual gap: LM Studio UI test)
- Commit/branch: `feat/v2-phase-0` (base `main` @ `aafcce6`)
- Date: 2026-11-11

## Summary
- Brought the real repository into the pack workspace (user-approved), audited code, deps, environment and host config.
- Pinned current legacy behavior with 56 offline characterization tests, including a real stdio protocol test.
- Found that fresh installs no longer start (mcp 2.x), startup hard-requires a Tavily key, and the YouTube transcript path is broken.

## Implemented
- `tests/characterization/` suite + baseline pin file mirroring the user's live LM Studio interpreter.
- Baseline audit, risk register, SDK recommendation, migration sequence, legacy tool inventory.
- `.gitignore`: ignore local virtualenvs and tool caches.

## Files added/changed
- Added: `docs/reports/phase-00-baseline.md`, `docs/reports/legacy-tool-inventory.md`, `docs/reports/phase-00-report.md`
- Added: `tests/__init__.py`, `tests/characterization/{__init__,conftest,test_server_surface,test_search_tools,test_data_tools,test_youtube_and_calc,test_local_file,test_stdio_protocol}.py`, `tests/characterization/requirements-baseline.txt`
- Changed: `.gitignore`
- Untouched: `ultimate_mcp_server.py`, `requirements.txt`, `README.md`, `.env.example`

## Tests and commands actually executed
| Command | Exit/result | Notes |
|---|---|---|
| `git ls-remote <origin>` | 0 | `main` @ `aafcce6` |
| `git init` / `remote add` / `fetch` / `checkout -b feat/v2-phase-0` | 0 | no file collisions |
| `uv pip install -r requirements.txt` (fresh venv) | 0 | resolved `mcp 2.3.0` |
| `python -c "import ultimate_mcp_server"` (unpinned env) | 1 | `No module named 'mcp.server.fastmcp'` |
| `python -c "from mcp.server import MCPServer"` (mcp 2.3.0) | 0 | also `mcp.server.mcpserver.MCPServer`, same class |
| `uv pip install -r tests/characterization/requirements-baseline.txt` | 0 | mcp 1.27.0 baseline |
| `.venv-baseline\Scripts\python -m pytest tests/characterization -q` | 0 | **56 passed**, 2 warnings (PyPDF2 deprecation, pydantic-settings) |
| `scratch/measure_baseline.py` | 0 | see Performance |
| `lms version`, `lms ls`, LM Studio exe version | 0 | LM Studio 0.4.28.0, 3 models |

## Security review
- Threats checked: eval/exec/shell, file path scope, SSRF/redirects, response size, secrets handling, error leakage, eager provider init.
- Tests passed/failed: risk behaviors are *characterized* (tests assert the current unsafe behavior so fixes are deliberate and visible).
- Remaining risks: R1–R15 in `phase-00-baseline.md` §4; none fixed in Phase 0 by design.

## Dependencies
- Added/removed: none to the project. Dev-only baseline pin file for characterization. `uv` 0.13.7 installed on the machine.
- Why: reproduce the legacy server deterministically; the unpinned set is broken.
- Core vs optional: n/a this phase.

## Compatibility
- Python: 3.12.7 system, 3.12.10 (uv-managed) in `.venv-baseline`.
- MCP SDK: legacy on 1.27.0 verified; 2.3.0 import paths verified; v2 target recommended (`mcp>=2.3,<3`).
- LM Studio version/host test: 0.4.28.0 installed; legacy server configured in `mcp.json`. **UI test not performed by agent.**
- Models tested: none (no model-driven tests in Phase 0).
- Not tested: LM Studio UI, MCP Inspector, live provider calls, tool latency.

## Performance
- Startup: warm import 2.52–2.68 s (3 runs); cold first import 22.9 s.
- Idle memory: 128.8 MiB working set (+8.3 MiB launcher).
- Peak memory: 128.8 MiB at 15 s idle (no tool calls).
- Latency: not measured.
- Measurement environment: i7-12700H, 16 GB, Windows, `.venv-baseline` (Python 3.12.10, mcp 1.27.0).

## Acceptance criteria
| Criterion | Result | Evidence |
|---|---|---|
| Git state and user modifications documented and preserved | Pass | baseline §1; pack files untracked, live copy untouched |
| All current tools and dependencies inventoried | Pass | `legacy-tool-inventory.md` |
| No destructive repository action | Pass | only init/fetch/branch; no reset/clean/overwrite |
| Existing behavior covered by characterization tests | Pass | 56 passed; live-network paths mocked |
| SDK/LM Studio claims verified against runtime | Partial | SDK verified at runtime; LM Studio version/config verified, UI test pending (manual) |
| Clear incremental migration sequence | Pass | baseline §7–8 |

## Known limitations / follow-up
- LM Studio UI test must be done manually by the user (or with a future automation path).
- Pack files are untracked — user to decide whether to commit them on this branch.
- `.venv-baseline` must be recreated per machine from `requirements-baseline.txt`.

## Recommendation
- Proceed to next phase? `yes`
- Reason: baseline reproducible and pinned by tests; only gap is a manual host test that does not block foundation work.
