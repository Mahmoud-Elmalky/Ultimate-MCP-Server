# Official Technical References

Verify these references again at implementation time because SDKs, host behavior, and dependencies evolve.

## MCP
- Official Python SDK v2 docs: https://py.sdk.modelcontextprotocol.io/v2/
- Official Python SDK migration guide: https://py.sdk.modelcontextprotocol.io/migration/
- Official Python SDK releases: https://github.com/modelcontextprotocol/python-sdk/releases
- MCP specification: https://modelcontextprotocol.io/specification
- MCP Inspector: https://github.com/modelcontextprotocol/inspector

## LM Studio
- Use MCP Servers: https://lmstudio.ai/docs/app/mcp
- Developer REST API: https://lmstudio.ai/docs/developer/rest
- Server settings/security: https://lmstudio.ai/docs/developer/core/server/settings

## Suggested integrations
- Playwright MCP: https://github.com/microsoft/playwright-mcp
- Playwright docs: https://playwright.dev/docs/intro
- Tree-sitter: https://tree-sitter.github.io/tree-sitter/
- ripgrep: https://github.com/BurntSushi/ripgrep
- SQLite FTS5: https://www.sqlite.org/fts5.html
- Qdrant (optional future vector provider): https://qdrant.tech/documentation/
- uv: https://docs.astral.sh/uv/
- Pydantic Settings: https://docs.pydantic.dev/latest/concepts/pydantic_settings/

## Current facts checked while preparing this pack
- Official MCP Python SDK documentation describes v2 as the current stable line and requires Python 3.11+.
- The v2 quick-start shows `from mcp.server import MCPServer`.
- LM Studio's MCP Host docs say MCP Host support began in version 0.3.17 and that local/remote servers can be configured via `mcp.json`.
- LM Studio's docs warn that MCP servers with excessively token-heavy tool definitions can overwhelm local model context.

These are reference facts, not permission to skip runtime verification. Pin exact dependencies only after an install/test on the target environment.
