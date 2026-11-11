# Phase 00 — Baseline Audit

Date: 2026-11-11 · Branch: `feat/v2-phase-0` (from `main` @ `aafcce6`)

## 1. Repository and Git state

| Item | Finding |
|---|---|
| Workspace | `d:\Coding\projects\Ultimate-MCP-Server-v2-Agent-Pack` initially contained **only the agent pack** (no `.git`, no server code). |
| Action taken (user-approved) | `git init -b main`, `git remote add origin https://github.com/Mahmoud-Elmalky/Ultimate-MCP-Server.git`, `git fetch origin main`, tracked `main`, created `feat/v2-phase-0`. No pack file was overwritten (no path collisions). |
| Remote | `origin/main` = `aafcce60cde6e8df74b1931428f056fadca0a839`; single branch. |
| History | `aafcce6 Update ultimate_mcp_server.py` · `9698196 Update README.md` · `123c5ff Feat: Initial release…` |
| Tracked tree | `.env.example`, `.gitignore`, `README.md`, `requirements.txt`, `ultimate_mcp_server.py` |
| Untracked (user's pack) | `AGENTS.md`, `AGENT_PROMPT.md`, `IMPLEMENTATION_ROADMAP.md`, `PACK_MANIFEST.json`, `PROJECT_BRIEF.md`, `SOURCES_AND_TECH_REFERENCES.md`, `START_HERE_AR.md`, `docs/`, `phases/`, `prompts/`, `scaffold/` — **left untracked; not committed without user permission.** |
| Live LM Studio copy | `D:\Coding\projects\Python\mcp search server for LM Studio\Ultimate MCP Server\` (separate clone at `123c5ff` with a staged change). Its `ultimate_mcp_server.py` differs from `aafcce6` **only by a missing trailing newline** → behaviorally identical. Contains a real `.env` (not read). Not modified. |
| Existing tests | None. |

## 2. Environment

| Item | Value |
|---|---|
| OS | Windows (PowerShell 5 shell) |
| CPU / RAM / GPU | i7-12700H · 15.7 GB visible · RTX 3050 Laptop GPU + Intel UHD (matches brief) |
| System Python | 3.12.7 (`C:\Program Files\Python312`) — the interpreter LM Studio launches |
| uv | 0.13.7 (installed this phase via official installer, user-approved) |
| Node | v24.15.0 (MCP Inspector possible) |
| LM Studio | **0.4.28.0** (`%LOCALAPPDATA%\Programs\LM Studio`), `lms` CLI present (commit `43d4b26`) |
| Local models | `gemma-4-e2b-it-uncensored` (4.6B), `qwen3.5-4b-uncensored-hauhaucs-aggressive` (4B), `text-embedding-nomic-embed-text-v1.5` |
| LM Studio `mcp.json` | One server `Search-Tools`: `command: python`, `args: [<live copy>/ultimate_mcp_server.py]`, no `env` block (keys come from that folder's `.env`). |

## 3. Critical findings

1. **Fresh installs are broken.** `requirements.txt` is unpinned; on 2026-11-11 it resolves `mcp==2.3.0`, and the server
   fails at import: `ModuleNotFoundError: No module named 'mcp.server.fastmcp'. This is mcp 2.x, where FastMCP was renamed
   to MCPServer`. The user's working install only works because it still has `mcp 1.27.0`.
2. **Startup requires `TAVILY_API_KEY`.** `TavilyClient` is constructed at import; without a key the whole server
   (all 8 tools) fails to start. Pinned by `test_should_fail_import_when_tavily_key_missing`.
3. **`youtube_expert` transcript path is broken** in both the live env (1.2.4) and latest (1.2.5): the static
   `YouTubeTranscriptApi.get_transcript` no longer exists. Returns `"YouTube error: …get_transcript…"`.
4. **Unsafe `eval`** in `calculate_or_time` (L336). `__builtins__=None` does not stop attribute traversal:
   `(1).__class__.__name__` evaluates. Large exponents (`9**9**9`) can hang/exhaust memory (not executed).
5. **Unrestricted file read** in `read_local_file`: any absolute path, `..` traversal followed, no size limit on PDF text.
6. **SSRF** in `read_article`: any scheme/host accepted (e.g. `169.254.169.254`), `follow_redirects=True` with no per-hop
   validation, full body downloaded with no size cap, HTTP status ignored.
7. **Errors returned as successes**: all tools swallow exceptions into plain text (`isError=false`).

## 4. Risk register

| ID | Risk | Severity | Likelihood | Evidence | Fix phase |
|---|---|---|---|---|---|
| R1 | Unpinned deps → server does not start on fresh install (mcp 2.x) | Critical | Certain | import traceback above | P1 (pyproject + uv.lock) |
| R2 | `eval` code-execution/DoS surface in calculator | Critical | Medium | `test_calculator_eval_allows_attribute_access` | P1 (AST calculator) |
| R3 | Arbitrary local file read / traversal | High | Medium | `test_should_allow_path_traversal_outside_any_root` | P1 (path policy) → P3 |
| R4 | SSRF / redirect / unbounded download in `read_article` | High | Medium | `test_read_article_should_not_validate_destination` | P2/P3 (safe fetch) |
| R5 | Missing Tavily key crashes startup of all tools | High | High | `test_should_fail_import_when_tavily_key_missing` | P1 (lazy providers) |
| R6 | Failures reported as success text; model cannot detect errors | High | Certain | every `except Exception` | P1 (result/error contract) |
| R7 | YouTube transcript feature non-functional | Medium | Certain | `test_youtube_transcript_is_broken_on_current_library` | P2 |
| R8 | Silent truncation at 4000 chars (search, txt, transcript) | Medium | Certain | `test_limited_search_should_silently_truncate`, txt test | P1 (output budget) → P2 |
| R9 | "Live price" claim without timestamp/currency/market status | Medium | Certain | `get_market_price` output | P2 |
| R10 | Deprecated/renamed deps (`duckduckgo-search`→`ddgs`, `PyPDF2`→`pypdf`, `pytz`) | Medium | Certain | RuntimeWarning / DeprecationWarning | P2/P3 |
| R11 | Heavy eager imports (pandas, yfinance, googleapiclient) slow startup and raise RAM | Medium | Certain | §6 measurements | P1 (lazy imports) |
| R12 | Raw exception text returned to model (possible internal detail/secret leakage) | Medium | Medium | `f"...: {e}"` everywhere | P1 (safe error mapping) |
| R13 | Keyword heuristic forces news search even in `general` mode | Low | High | `test_limited_search_should_trigger_news_on_keywords` | P2 |
| R14 | Heuristic ticker rewriting (`usd-egp`→`USDEGP=X`) may resolve wrong asset | Low | Medium | parametrized test | P2 |
| R15 | Tool definitions ≈2.5k tokens for 8 tools — heavy for 4B models | Medium | Certain | footprint test | P1/P2 (concise descriptions) |

## 5. MCP SDK and LM Studio compatibility

- **SDK (verified at runtime):** `mcp 2.3.0` installs on Python 3.12; both `from mcp.server import MCPServer` and
  `from mcp.server.mcpserver import MCPServer` resolve to the same class; `mcp.server.fastmcp` raises a guided
  `ModuleNotFoundError`. The pack's documented import (`from mcp.server import MCPServer`) is confirmed.
- **Recommendation:** pin `mcp>=2.3,<3` in `pyproject.toml`, lock the exact version in `uv.lock`, use
  `from mcp.server import MCPServer`. Keep the legacy script on its own pinned 1.x baseline
  (`tests/characterization/requirements-baseline.txt`) until each tool is migrated.
- **Protocol test (legacy, mcp 1.27.0):** SDK stdio client → initialize, `list_tools` (8), `calculate_or_time`
  call returns `Result: 42`, invalid enum returns `isError=true`; no stdout corruption
  (`test_stdio_handshake_and_offline_call`).
- **LM Studio 0.4.28:** installed and configured with the legacy server; `mcp.json` format matches the documented
  `mcpServers` / `command` / `args` (+ optional string-valued `env`) shape. **Not tested by the agent in the LM Studio UI**
  — no UI automation is available. A real-host test is a required manual step before any "works in LM Studio" claim for v2.
- **Hot reload:** not verified; profiles will be startup-only (ADR-0004).

## 6. Baseline measurements (legacy server, `.venv-baseline`, this laptop)

| Metric | Value | Method |
|---|---|---|
| Cold first import | 22.9 s | first import after venv creation (includes `.pyc` compilation) |
| Warm import | 2.68 / 2.52 / 2.64 s | 3 runs, `python -c "import ultimate_mcp_server"` |
| Idle working set | 128.8 MiB (+8.3 MiB venv launcher) | `GetProcessMemoryInfo` 15 s after stdio start |
| Tool count / footprint | 8 tools · 10,134 chars (~2.5k tokens, chars/4) | `mcp.list_tools()` |

Script: `scratch/measure_baseline.py` (agent scratch, not part of the repo). Tool-call latency against live providers
was not measured (no live API calls in Phase 0).

## 7. Migration sequence (target module mapping)

| Legacy tool | Target | Module | Phase |
|---|---|---|---|
| `calculate_or_time` | `calculate`, `get_current_time` (+ wrapper) | `modules/utilities` | P1 primitives → P2 tools |
| `read_local_file` | `document_read` (+ wrapper) | `modules/documents` | P1 path policy → P3 |
| `web_search` | `web_search` (Tavily provider) | `modules/research`, `providers/search` | P2 |
| `limited_web_search` | wrapper over DDG (`ddgs`) provider / `multi_search` | `modules/research` | P2 |
| `read_article` | `read_webpage` (+ wrapper) | `modules/research` | P2 (safe fetch) |
| `get_market_price` | `get_market_price` | `providers/finance` | P2 |
| `get_weather` | `get_weather` | `providers/weather` | P2 |
| `youtube_expert` | `youtube_search`/`_transcript`/`_comments` (+ adapter) | `providers/youtube` | P2 |

Order: (1) package skeleton + pinned SDK v2 + server that starts with zero keys; (2) result/error contract, settings,
registry/profiles, doctor; (3) security primitives (AST calculator, path resolver, approval broker); (4) migrate
utilities first (no network), then providers one at a time behind characterization parity tests.

## 8. First Phase 01 slice

`pyproject.toml` + `uv.lock` (mcp 2.x pinned), `src/ultimate_mcp/` skeleton, settings with secret redaction,
`ultimate-mcp serve --profile default` starting over stdio with **no API keys**, `ultimate-mcp doctor`, and an
integration test proving startup + clean stdout. Legacy `ultimate_mcp_server.py` stays untouched and runnable.
