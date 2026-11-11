# Ultimate MCP Server v2 — Project Brief

## Product vision

Build a dependable, modular, lightweight MCP server that gives local language models connected through LM Studio practical access to research, browsing, coding, documents, data, memory, system, AI-support, media, and utility capabilities. The platform should improve task completion and factual reliability without pretending tools can replace a model's underlying reasoning ability.

## User and environment

- Primary user: technically capable individual using local models and developer tooling.
- Primary host: LM Studio desktop application.
- Primary development OS: Windows.
- Cross-platform objective: keep Python package logic portable across Windows, macOS, and Linux where reasonable.
- Known target hardware: Intel Core i7-12700H, NVIDIA RTX 3050 4 GB VRAM, 16 GB system RAM. Verify before performance tuning.

## Approved decisions

| Decision | Approved choice |
|---|---|
| Scope | Full capability scope, phased implementation |
| Architecture | Hybrid modular monolith, one gateway/process by default |
| Automation | Controlled; human approval for material side effects |
| Compatibility | LM Studio first, broader MCP hosts later |
| Default footprint | Lightweight core, optional extras, lazy loading |
| Storage | SQLite + FTS5 initially; vector adapter optional |
| Transport | stdio default; Streamable HTTP optional and secured |
| Packaging | Python 3.11+, `pyproject.toml`, `uv`, lockfile |
| Quality bar | Reliability, safety, evidence, compatibility, measured performance |

## Existing baseline (verify at Phase 0)

The last inspected repository version had:
- `ultimate_mcp_server.py` as the main single-file implementation.
- `README.md`, `requirements.txt`, `.env.example`.
- Eight tools: `web_search`, `limited_web_search`, `get_market_price`, `get_weather`, `read_article`, `youtube_expert`, `calculate_or_time`, `read_local_file`.
- Tavily, DuckDuckGo, yfinance, wttr.in, YouTube Data API, YouTube transcript API, pandas, PyPDF2, BeautifulSoup, HTTPX, pytz, and dotenv integrations.

The baseline may have changed. Inspect the actual checked-out files and current commit before relying on these details.

## Product outcomes

1. Local models can discover and call tools with high schema/argument accuracy.
2. Tool failures are typed, bounded, explainable, and recoverable where safe.
3. Evidence-bearing answers include source provenance and freshness metadata.
4. File and system access are constrained to user-configured scopes.
5. Side effects require explicit, human-controlled approval.
6. Optional features do not make the core server heavy or fragile.
7. Model compatibility is measured per exact model/runtime configuration.
8. A repeatable benchmark suite demonstrates changes in task completion, accuracy, safety, tokens, and latency.

## Explicit non-goals

- Do not train or fine-tune local models as part of this repository.
- Do not promise Claude/ChatGPT-level reasoning from tools alone.
- Do not build a giant always-running multi-agent framework.
- Do not require Qdrant, Docker, a GPU, cloud API keys, or paid services for core operation.
- Do not expose arbitrary shell execution, unrestricted browser control, or unrestricted filesystem access.
- Do not automatically collect every conversation or infer what to save in long-term memory.
- Do not bundle image/video generation models; integrate with local providers such as ComfyUI optionally.

## Architecture in one sentence

A lightweight MCP gateway exposes profile-selected tools; thin MCP handlers call modular, testable domain services and providers; shared infrastructure enforces configuration, permission, approval, execution, validation, provenance, storage, logging, and resource limits.

## Supported operating modes

- `default`: small daily-use set with utilities, basic research, safe file/document inspection, diagnostics.
- `research`: research/search/source-reading tools.
- `coding`: project inspection, code search, git read operations, test/lint tools, approval-gated patching.
- `documents`: document extraction, search, tables, spreadsheet inspection.
- `browser`: browser automation tools, with writes/uploads/submissions protected.
- `full`: all installed/enabled modules; emits a context-size warning.

Profiles are chosen at server startup. Dynamic tool-list refresh must not be assumed unless verified with the actual host.

## Required evidence for claims

Every statement that a module is supported must be backed by tests and a documented optional dependency/provider state. Every benchmark or resource claim must include environment, model, config, command, and measured result. Clearly distinguish implemented, experimental, disabled, and planned features.
