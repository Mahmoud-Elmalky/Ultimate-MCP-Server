# Architecture Specification

## 1. Architecture style

Use a **hybrid modular monolith**:
- One MCP server process and one gateway by default.
- Domain modules are separate Python packages with explicit registration functions.
- Provider adapters isolate external services.
- Shared infrastructure owns validation, permissions, approval, execution policies, logging, caching, storage, and result contracts.
- Separate worker processes are introduced only where needed for isolation, blocking libraries, resource-heavy tasks, or crash containment.

Do not split every domain into a microservice. That adds process management, ports, serialization, and memory overhead without a demonstrated benefit on the target laptop.

## 2. Request flow

1. MCP host sends a tool call.
2. MCP SDK validates protocol envelope and dispatches to a thin handler.
3. Handler validates the typed arguments and attaches a request/correlation ID.
4. Policy layer checks module availability, workspace scope, rate/size limits, and permission level.
5. For protected actions, handler prepares a pending operation and returns `needs_approval`; no side effect occurs.
6. Execution manager invokes the domain service/provider with timeout, cancellation, bounded concurrency, and idempotency rules.
7. Output validator checks the domain result.
8. Provenance and metadata are attached.
9. MCP adapter returns concise text and, where reliably supported, structured content.
10. Audit log stores the event without secrets or unnecessary content.

## 3. Package boundaries

- `core`: app bootstrap, config, tool registry, permission policies, approvals, execution manager, typed errors/results, logging, telemetry.
- `modules`: user-facing tool wrappers grouped by domain.
- `services`: testable business logic; no MCP-specific types in core domain logic.
- `providers`: external/local implementation adapters behind small interfaces.
- `storage`: SQLite repositories, filesystem paths, cache metadata.
- `workflows`: deterministic compositions of services and tools; no hidden model orchestration.
- `schemas`: stable input/output models.

Tool handlers must be thin. Do not mix network I/O, business logic, and MCP response construction in one large function.

## 4. MCP SDK and transport

- Target Python 3.11+ and the official MCP Python SDK v2 after Phase 0 verifies the stable release, API, transitive dependencies, and LM Studio compatibility.
- The current SDK v2 quick-start documents `from mcp.server import MCPServer`; pin and test the exact release rather than copying imports from an unpinned web snippet.
- Use stdio as the default local LM Studio transport. Never print diagnostics/logging to stdout when stdout is the MCP protocol stream; write logs to stderr or a file.
- Streamable HTTP is optional, disabled by default, and must have authentication/bind-address controls. Do not expose a local tool server to the LAN by default.
- Do not assume LM Studio dynamically refreshes an already-running tool list. Use startup-selected profiles and restart to change tool availability unless proven otherwise.

## 5. Tool registry and profiles

Each tool registration includes:
- stable name and short description;
- domain/module;
- typed input schema;
- permission classification;
- timeout and output-size policy;
- optional dependency/provider requirements;
- idempotency/retry policy;
- provenance requirements;
- tests and documentation references.

Profiles are declarative allowlists:
- `default`, `research`, `coding`, `documents`, `browser`, `full`.
- Disabled optional modules are not registered as callable tools.
- The `full` profile prints a diagnostic warning about token/context footprint.
- Keep tool names distinct; do not expose multiple aliases for the same action without a compatibility reason.

## 6. Provider interfaces

Use small Protocol/ABC interfaces for SearchProvider, MarketDataProvider, WeatherProvider, VideoProvider, BrowserProvider, EmbeddingProvider, and DocumentExtractor where multiple implementations exist.

Provider results must identify provider, retrieval timestamp, source URL/identifier, freshness, and limitations. A provider fallback must not silently mix inconsistent data; state which provider supplied each item.

Missing API keys or optional packages disable only the provider. Bootstrap must not instantiate a paid provider eagerly if its key is absent.

## 7. Storage

Default local storage:
- SQLite for settings, approvals, task state, memory metadata, audit metadata, and cache indexes.
- SQLite FTS5 for keyword search when available; verify SQLite build capability at startup.
- Local files under a configurable application-state directory for artifacts and extracted content.
- Use separate repositories/transactions; use migrations (e.g. explicit SQL migration scripts or a lightweight migration utility) and backup support.

Vector search is an optional adapter. Start with FTS5 and a simple interface. Do not require a separate Qdrant/Chroma service for default operation.

## 8. Resource management

- Lazy-import optional modules.
- Bound concurrency globally and per provider.
- Set connection, read, total operation, and subprocess timeouts.
- Cap download, document, page, row, transcript, and tool-output sizes.
- Use pagination or saved artifacts when results exceed tool response budgets.
- Avoid background polling and automatic model loading/unloading.
- Offer `doctor` output describing enabled modules and missing optional dependencies.

## 9. Compatibility boundaries

Do not rely on MCP host UI-specific features for core safety. The approval broker is out-of-band and locally controlled. Tool definitions should remain usable by hosts that support ordinary MCP tools and standard schema types. Keep optional richer outputs separate from essential text output.
