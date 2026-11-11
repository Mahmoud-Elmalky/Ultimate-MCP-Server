# ADR-0004: Startup-Selected Tool Profiles

- Status: Accepted
- Decision: Register only the tools for the selected startup profile. Do not assume host-side dynamic tool list refresh.
- Context: Large MCP tool catalogs consume context and can overwhelm weaker local models; host caching/update behavior varies.
- Consequences: Users restart/reconnect to change profile. Keep `default` concise and `full` explicit.
