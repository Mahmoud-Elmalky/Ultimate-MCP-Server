# ADR-0005: No Hidden LLM Orchestration in the MCP Server

- Status: Accepted
- Decision: MCP server workflows are deterministic bounded compositions of services. The host model remains the planner/reasoner. Do not call another LLM automatically.
- Context: Hidden model calls add latency, VRAM/RAM use, costs/privacy concerns, and confusing responsibility boundaries.
- Consequences: Workflows provide execution primitives and verifiable outputs, not a separate autonomous agent loop.
