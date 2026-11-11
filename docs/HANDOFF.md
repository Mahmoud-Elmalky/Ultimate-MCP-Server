# Ultimate MCP Server v2 — Model-Independent Handoff (`docs/HANDOFF.md`)

> **Authoritative state document for model switches.** Always verify against `git status`, `git log`, and test execution before making changes.

## 1. Current Repository & Git State

- **Last updated:** 2026-11-12T01:38+02:00
- **Branch:** `feat/v2-phase-3` (branched from finalized Phase 02 checkpoint `1a97327` on `feat/v2-phase-2`)
- **Latest commit on `feat/v2-phase-3`:** `ffe558d` — `feat(phase3): harden browser egress security and implement browser engine` (preceded by `38da44c`; Phase 03 post-milestone hardening + Document Intelligence changes are uncommitted awaiting user review and commit authorization)
- **Finalized Phase 02 checkpoint commit (`feat/v2-phase-2`):**
  - `1a97327` (`1a973272e32cc97b477810353465fd2c9b96fab7`) — `fix(phase2): finalize audit hardening and cache expiration`
- **Untouched legacy files & live configurations:**
  - `ultimate_mcp_server.py` and `requirements.txt` remain untouched at their `aafcce6` baseline.
  - Separate live LM Studio server directory `D:\Coding\projects\Python\mcp search server for LM Studio\Ultimate MCP Server\` was **never modified**.
  - User's live `%USERPROFILE%\.lmstudio\mcp.json` was **never modified**; portable side-by-side configuration (`Search-Tools` + `Ultimate-MCP-v2`) is in `mcp.json.example` (`2385d3e`).

## 2. Phase 03 Completion Verification

- **Pytest V2 suite (`.venv`, Python 3.12.10):** **169 passed**, **85% total statement coverage** (5,511 statements, 820 missed) — covers all unit, browser egress, browser mutation approval, live Chromium security, browser engine, and document extractor/workflow/security tests.
- **Ruff (`uv run --no-sync ruff check src tests`):** `All checks passed!`
- **MyPy (`uv run --no-sync mypy`, `strict = true`):** `Success: no issues found in 75 source files`.
- **Legacy baseline suite (`.\.venv-baseline\Scripts\python.exe -m pytest tests/characterization -p no:cacheprovider -q`):** `56 passed, 4 warnings`.
- **CLI Doctor profiles:** `default` = 5 tools, `browser` = 15 tools (14 browser + `get_current_time`), `documents` = 11 tools (9 enabled document tools + 2 utilities; `image_ocr` disabled without `pytesseract`), `research` = 23 tools, `coding` = 6 tools, `full` = 52 enabled tools.

## 3. LM Studio Host & UI Verification Status

- **Installed LM Studio version:** `0.4.28.0` (`lms` CLI commit `43d4b26`).
- **UI Verification Limitation:** Interactive LM Studio desktop UI testing remains **`pending` manual verification** (`tests/integration/test_v2_stdio_server.py` verifies protocol-level stdio startup, tool discovery, and tool calls via `mcp.client.stdio`).

## 4. Phase 03 Architecture Summary (`modules/browser/` & `modules/documents/`)

All modules in `src/ultimate_mcp/modules/browser/` and `src/ultimate_mcp/modules/documents/` strictly comply with `<= 300` lines per file.

1. **Browser Engine (`src/ultimate_mcp/modules/browser/`):**
   - 14 optional tools gated on `optional_packages=("playwright",)` under `ProfileName.BROWSER` and `ProfileName.FULL`.
   - Configurable resource caps (`Settings.browser_max_sessions=2`, `browser_max_tabs=5`, `browser_max_download_bytes=2 MiB`, `browser_max_response_bytes=2 MiB`, `browser_navigation_timeout_ms=15000`, `browser_headless=True`, `browser_disable_webrtc=True`).
   - Three-layer egress defense (`STRICT_CHROMIUM_ARGS`, Playwright HTTP + WebSocket routing pinned to `fetch_safe_url`, and `LoopbackValidatingProxy` with zero `EXCLUDE 127.0.0.1`), single-use `L3_SENSITIVE` canonical `ApprovalBroker` bindings (`MutationApprovalGate`, `now >= expires_at`), and explicit Windows isolation limitation documentation in `docs/SECURITY_MODEL.md` §7.

2. **Document Intelligence (`src/ultimate_mcp/modules/documents/`):**
   - Shared bounded models & security (`models.py`, `security.py`): `ExtractedDocument`, `DocumentChunk`, `ExtractedTable`, `SourceLocation`, `validate_document_file`, `inspect_zip_archive_safety`, `UNTRUSTED_DOC_WARNING`, `untrusted_content=True`.
   - Extractors (`text_formats.py`, `html_formats.py`, `office_formats.py`, `spreadsheet_slides.py`): UTF-8 `.txt`/`.md`/`.json`, `.html`/`.htm` (scripts/styles stripped, tables preserved), `.csv`/`.tsv`, `.pdf` (`pypdf`, page provenance), `.docx` (`python-docx`, paragraphs + tables), `.xlsx` (`openpyxl` read-only preserving both `=formula` and cached values), and `.pptx` (`python-pptx`, slides + speaker notes + tables).
   - Tools (`service.py`, `analysis.py`, `compare.py`, `convert.py`, `mutations.py`, `writers.py`, `ocr.py`, `tools.py`, `action_tools.py`): `read_text_file`, `document_read`, `document_search`, `document_extract_tables`, `document_qa` (deterministic lexical retrieval, zero LLM), `document_compare`, `document_convert`, `document_create`, `document_edit` (L2 approval + TOCTOU SHA-256 check + timestamped `.bak` backups), and optional `image_ocr` (`pytesseract` + `PIL`).

## 5. Next Steps

1. Await user review and commit authorization for the completed Phase 03 changes on `feat/v2-phase-3`.
2. Proceed to **Phase 04 — Coding & System** (`phases/PHASE-04_CODING_SYSTEM.md`) after checkpoint commit authorization.
