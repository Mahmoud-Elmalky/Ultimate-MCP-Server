# Migration Plan from the Existing Single-File Server

## Principles

- Preserve the current default branch and all user work.
- Refactor behind characterization tests.
- Preserve existing public tool names where practical during a deprecation window.
- Split multi-purpose tools only with a clear mapping and compatibility note.
- Do not combine SDK migration and all domain feature changes into one unreviewable patch.

## Migration order

1. Inventory existing code, current dependency versions, and environment configuration.
2. Add tests around current successful inputs and failure cases.
3. Create package structure and a minimal server that starts with no API keys.
4. Move one legacy tool at a time into a domain service and a thin MCP handler.
5. Keep old names as wrappers where useful (with deprecation docs) until the new names are verified in LM Studio.
6. Remove wrappers only in a deliberate breaking release and document replacement names.
7. Remove obsolete dependencies only after import/use search and tests prove they are unused.

## Legacy tools

### `web_search`
Preserve the name. Move Tavily into a provider adapter, initialize lazily, validate key presence, add provider/time/provenance and output limits.

### `limited_web_search`
Move DuckDuckGo into an optional provider adapter. Check current package naming/API at implementation time. Do not assume old `duckduckgo_search` import is current. Decide whether this remains a compatibility wrapper around `multi_search`.

### `get_market_price`
Preserve the name. Remove fragile ticker rewriting; accept a symbol exactly as documented or normalize only through explicit tested mappings. Return last available quote, currency, provider timestamp, market status if available, and freshness caveat.

### `get_weather`
Preserve the name. Use a provider adapter, handle HTTP errors/status, parse and validate response, set timeouts, and identify units/location/observation time.

### `read_article`
Migrate to `read_webpage`. Keep a compatibility wrapper temporarily. Add strict SSRF/redirect/size/content-type controls.

### `youtube_expert`
Split into `youtube_search`, `youtube_transcript`, and `youtube_comments`. Keep an adapter temporarily if old callers need it. Test current YouTube transcript library API; do not assume deprecated calls still work.

### `calculate_or_time`
Split into `calculate` and `get_current_time`. Replace eval with a strict AST evaluator. Validate time zone identifiers and handle unknown zones.

### `read_local_file`
Migrate into `document_read`/format extractors. Keep bounded and workspace-scoped. Add page/row/size metadata and page/cell provenance.

## Database and state migration

No prior persistent database is assumed. If one exists in the current working tree, inspect and back it up before schema changes. Use versioned migrations and preserve old state. Do not overwrite existing `.env` or local config files.

## Compatibility and release notes

Document old-to-new tool mappings, changed argument schemas, deprecation status, and host reconfiguration steps. Keep the default profile stable. If the SDK's protocol or tool schema output changes, test both MCP Inspector and LM Studio.
