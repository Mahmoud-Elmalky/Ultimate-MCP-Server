# Target Tool Catalog

This document is the target inventory, not a claim that every tool is already implemented. Tools must be registered only when implemented, tested, enabled, and available in the selected profile.

## Naming and description rules

- Use lower_snake_case names, one task per tool, and concise verbs.
- Avoid near-duplicates and vague tools such as `do_everything`.
- Keep inputs simple: strings, numbers, booleans, enums, and bounded arrays/objects.
- Use a small number of required fields; document defaults.
- Every description says when to use the tool, what it does, what it does not do, and any material limits.
- Every side-effecting tool declares a permission level and approval workflow.

## Permission legend

- **L0 Read** — read/search/inspect within a configured safe boundary.
- **L1 Prepare** — compute, preview, draft, or propose; no external side effect.
- **L2 Write** — local persistent write/modify; explicit approval required.
- **L3 Sensitive** — delete, upload, submit externally, arbitrary process/command; explicit approval and stricter policy required.
- **L4 Denied by default** — prohibited or not implemented unless a separately approved security design permits a narrow use case.

## Research and web

| Tool | Purpose | Permission / notes |
|---|---|---|
| `web_search` | Search the configured primary web provider | L0; returns ranked sources and metadata |
| `search_news` | Search dated news items | L0; publication/event dates kept distinct |
| `search_academic` | Search scholarly metadata and papers | L0; provider and coverage disclosed |
| `search_official_docs` | Search official product/library docs | L0; prefer vendor documentation |
| `multi_search` | Query selected providers and merge/dedupe results | L0; preserve per-result provider |
| `read_webpage` | Fetch and extract a known public page | L0; SSRF protections mandatory |
| `extract_links` | Extract links from a retrieved page | L0 |
| `extract_tables` | Extract tables from a retrieved page | L0 |
| `crawl_website` | Bounded same-domain crawl | L0; max pages/depth/bytes and domain policy |
| `compare_sources` | Compare claims/coverage across supplied sources | L1; comparison must retain citations |
| `verify_claim` | Check a claim against independent sources | L1; state evidence strength and uncertainty |
| `deep_research` | Run a bounded research workflow | L1; no unlimited autonomous loop |
| `research_report` | Assemble evidence and findings into a report | L1; cite source IDs/URLs |

## Utilities and live data

| Tool | Purpose | Permission / notes |
|---|---|---|
| `get_weather` | Current conditions and optional forecast | L0; provider timestamp and location returned |
| `get_market_price` | Latest available quote for a supported symbol | L0; never promise exchange real-time unless provider confirms |
| `get_exchange_rate` | Currency conversion/rate lookup | L0; rate timestamp and source |
| `get_current_time` | Current time in a named timezone | L0 |
| `calculate` | Safe arithmetic and approved math functions | L0; strict AST parser, never eval |
| `convert_units` | Unit conversion | L0 |
| `youtube_search` | Search YouTube videos | L0; API key optional provider |
| `youtube_transcript` | Retrieve transcript when available | L0; language and availability reported |
| `youtube_comments` | Retrieve permitted public comment data | L0; quota and access restrictions |
| `rss_search` | Discover/read RSS or Atom feeds | L0; URL safety applies |

## Browser automation

| Tool | Purpose | Permission / notes |
|---|---|---|
| `browser_open` | Open an allowed URL in a dedicated context | L0; navigation policy applies |
| `browser_back` | Navigate back | L0 |
| `browser_tabs` | List/switch/close managed tabs | L0; close may be L2 if state loss matters |
| `browser_snapshot` | Return accessible page structure/text | L0; limit output |
| `browser_screenshot` | Capture current managed page | L0; local artifact path only |
| `browser_click` | Click a non-submitting/non-destructive UI element | L0/L1; classify target; protected actions require approval |
| `browser_type` | Type into a field | L1; do not submit automatically |
| `browser_select` | Select an option | L1 |
| `browser_scroll` | Scroll the page | L0 |
| `browser_wait` | Wait for a condition with timeout | L0 |
| `browser_download` | Download a file to approved folder | L2; approval/policy and size/type checks |
| `browser_upload` | Upload a local file | L3; explicit approval and exact file/destination digest |
| `browser_submit_form` | Submit a form | L3; explicit approval |

Use Playwright through an optional extra. No CAPTCHA bypass, access-control bypass, or default attachment to a personal browser profile.

## Coding and Git

| Tool | Purpose | Permission / notes |
|---|---|---|
| `project_scan` | Summarize a project within workspace roots | L0 |
| `list_project_files` | Bounded file tree | L0 |
| `search_codebase` | Search source with ripgrep | L0 |
| `read_code_file` | Read bounded source ranges | L0 |
| `read_symbol` | Extract a symbol via Tree-sitter where supported | L0 |
| `find_references` | Search symbol usages | L0 |
| `analyze_project` | Inspect manifests, languages, structure | L0/L1 |
| `dependency_graph` | Summarize known dependency relations | L0/L1 |
| `git_status` | Read working tree status | L0 |
| `git_diff` | Read diff | L0 |
| `git_log` | Read commit history | L0 |
| `git_branch_list` | List branches | L0 |
| `git_create_branch` | Create branch | L2; approval or explicit policy |
| `run_tests` | Run configured test command | L1/L3 depending on command and project policy |
| `run_linter` | Run configured linter | L1 |
| `run_type_checker` | Run configured checker | L1 |
| `analyze_error` | Explain traceback/build/test failure | L1 |
| `propose_patch` | Produce a patch without applying it | L1 |
| `apply_patch` | Apply a reviewed patch | L2; exact patch digest approval |
| `run_project_command` | Run an allowlisted command in project root | L3; command policy and approval |
| `run_code_sandboxed` | Execute code only inside a verified OS sandbox | L3; disabled if genuine isolation unavailable |
| `review_code` | Static code review findings | L1; distinguish heuristic from verified findings |
| `dependency_audit` | Audit dependency vulnerabilities/config | L1; provider and data freshness reported |

## Documents

| Tool | Purpose | Permission / notes |
|---|---|---|
| `document_read` | Read PDF/DOCX/PPTX/XLSX/TXT/MD/HTML | L0; format-specific extraction limits |
| `document_search` | Search one or more indexed documents | L0 |
| `document_qa` | Retrieve supporting passages for a question | L1; page/slide/sheet/cell provenance |
| `document_extract_tables` | Extract tables | L0 |
| `document_compare` | Compare documents or versions | L1 |
| `document_convert` | Convert supported formats | L1/L2 depending on output |
| `document_create` | Create a new document | L2; approved output location |
| `document_edit` | Modify an existing document | L2; backup and approval |
| `image_ocr` | OCR an image/PDF page | L0/L1; optional OCR dependency |

## Data and databases

| Tool | Purpose | Permission / notes |
|---|---|---|
| `csv_analyze` | Profile and summarize CSV data | L0 |
| `data_profile` | Missing values/types/distributions | L0 |
| `data_clean_preview` | Propose cleaning transformations | L1 |
| `data_transform` | Transform in-memory data or write artifact | L1/L2 |
| `spreadsheet_analyze` | Analyze workbook sheets/formulas | L0 |
| `spreadsheet_edit_preview` | Preview workbook changes | L1 |
| `spreadsheet_apply` | Apply workbook modifications | L2; copy/backup first |
| `create_chart` | Create chart artifact | L1/L2 |
| `database_connect` | Register a configured database connection | L2; secrets protected |
| `database_schema` | Inspect tables/columns | L0 |
| `database_query` | Execute read-only SQL by default | L0; SELECT-only unless explicit policy |
| `database_export` | Export query results | L2; approved path |

## Memory and knowledge

| Tool | Purpose | Permission / notes |
|---|---|---|
| `memory_save` | Save an explicitly selected memory | L2; consent and scope required |
| `memory_search` | Search saved memories | L0 within current user/project scope |
| `memory_update` | Update a memory | L2 |
| `memory_delete` | Delete memory | L3 or explicit confirmation; verify scope |
| `memory_list` | List saved memory items | L0 |
| `knowledge_ingest` | Index user-selected documents | L2; local processing and retention disclosed |
| `knowledge_search` | Search indexed content | L0; provenance mandatory |
| `knowledge_remove` | Remove indexed content | L3; exact document ID |
| `project_context_load` | Load scoped project facts | L0 |
| `conversation_summarize` | Summarize supplied text/context | L1; only context actually supplied to the tool |

Do not assume the MCP server can read all host conversation history. It only receives data explicitly passed to tools or available through a documented integration.

## System operations

| Tool | Purpose | Permission / notes |
|---|---|---|
| `system_info` | OS/runtime summary | L0; avoid unnecessary identifiers |
| `resource_monitor` | CPU/RAM/GPU usage | L0; optional GPU library |
| `process_list` | List visible processes | L0; restrict sensitive details |
| `process_inspect` | Inspect a process | L0; protected fields redacted |
| `directory_list` | List configured workspace | L0 |
| `file_search` | Search within configured workspace | L0 |
| `file_copy_preview` | Preview copy operation | L1 |
| `file_copy` | Copy files | L2 |
| `file_move` | Move files | L2 |
| `file_delete` | Delete files | L3; no recursive delete by default |
| `terminal_command` | Execute policy-allowed command | L3; disabled by default beyond allowlist |
| `package_inspect` | Inspect installed packages | L0 |
| `log_analyze` | Read configured app logs | L0; redact secrets |
| `backup_create` | Create backup | L2 |

## AI intelligence helpers

| Tool | Purpose | Permission / notes |
|---|---|---|
| `tool_catalog_search` | Find tools by domain/capability | L0; searchable registry metadata |
| `tool_recommend` | Recommend likely tools for a stated task | L1; guidance, not automatic execution |
| `task_plan_validate` | Validate supplied task plan for missing steps/permissions | L1; no LLM call required |
| `context_estimate` | Estimate token cost using configured tokenizer or heuristic | L0; label estimates |
| `context_compress_preview` | Propose a concise evidence-preserving summary | L1; never silently discard citations |
| `json_validate` | Parse/validate JSON | L0 |
| `schema_validate` | Validate data against an allowed JSON Schema subset | L0 |
| `output_evaluate` | Apply deterministic quality checks | L1; not a truth oracle |
| `workflow_execute` | Execute a named bounded workflow | L1/L2/L3 depending on steps; approvals per side effect |
| `task_status` | Inspect a persisted workflow/task | L0 |
| `task_cancel` | Cancel an active task | L1 |

## Media and integrations

Optional media adapters may provide image metadata/resize/convert, audio metadata/transcription, video metadata/frame extraction/FFmpeg jobs, and ComfyUI job submission. File generation is L2; remote uploads or externally visible actions are L3. Heavy libraries and model runtimes must never be core dependencies.

## Current legacy tool migration map

| Legacy name | Target | Migration notes |
|---|---|---|
| `web_search` | `web_search` | Preserve name; add provider metadata, bounded results, typed errors |
| `limited_web_search` | `multi_search` or DuckDuckGo provider | Keep compatibility alias only during deprecation window |
| `get_market_price` | `get_market_price` | Preserve; disclose latest available quote and timestamp |
| `get_weather` | `get_weather` | Preserve; return structured conditions and provider time |
| `read_article` | `read_webpage` | Keep alias temporarily; enforce URL security and size limits |
| `youtube_expert` | `youtube_search`, `youtube_transcript`, `youtube_comments` | Split one multi-purpose tool; document legacy alias/deprecation |
| `calculate_or_time` | `calculate`, `get_current_time` | Split tool; AST safe calculator |
| `read_local_file` | `document_read` | Preserve bounded, workspace-scoped compatibility wrapper |
