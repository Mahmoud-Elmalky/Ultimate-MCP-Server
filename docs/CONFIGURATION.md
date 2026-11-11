# Configuration Reference

Exact variable names may be adjusted during implementation, but they must be centralized, validated, documented, and covered by tests.

## Core configuration

- `ULTIMATE_MCP_PROFILE` — `default`, `research`, `coding`, `documents`, `browser`, or `full`.
- `ULTIMATE_MCP_STATE_DIR` — writable application state directory.
- `ULTIMATE_MCP_READ_ROOTS` — configured roots allowed for read access.
- `ULTIMATE_MCP_WRITE_ROOTS` — configured roots allowed for approved writes.
- `ULTIMATE_MCP_OUTPUT_DIR` — artifact output directory.
- `ULTIMATE_MCP_LOG_LEVEL` — log level.
- `ULTIMATE_MCP_LOG_FILE` — optional log path.
- `ULTIMATE_MCP_MAX_OUTPUT_BYTES` — global maximum tool output.
- `ULTIMATE_MCP_MAX_CONCURRENCY` — global concurrency limit.
- `ULTIMATE_MCP_APPROVAL_TTL_SECONDS` — pending approval expiry.
- `ULTIMATE_MCP_TRANSPORT` — `stdio` default; `streamable-http` optional.
- `ULTIMATE_MCP_HTTP_HOST` and `ULTIMATE_MCP_HTTP_PORT` — only for HTTP mode; secure bind/auth required.

## Optional providers

- `TAVILY_API_KEY` — Tavily search provider.
- `YOUTUBE_API_KEY` — YouTube Data API provider.
- Other provider keys only when a selected adapter requires them.

Missing optional keys disable only their adapters. Never fail core startup due solely to an optional key.

## Configuration rules

- Use Pydantic Settings or an equivalent single validated config object.
- `.env.example` contains placeholders only.
- `.env`, state, caches, downloaded files, artifacts, and local benchmark outputs must be ignored by Git unless deliberately curated fixtures.
- Avoid logging resolved secrets or complete connection strings.
- Validate directories at startup and explain missing permissions.
- Default read/write roots must be conservative. Do not grant the entire home directory or drive by default.
