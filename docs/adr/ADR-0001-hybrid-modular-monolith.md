# ADR-0001: Hybrid Modular Monolith

- Status: Accepted
- Decision: One MCP gateway/process by default, with independently packaged domain modules and providers. Split into worker processes only for isolation, blocking workloads, or resource containment.
- Context: Target hardware is resource-constrained relative to local LLM inference. A microservice per domain adds memory and operational overhead.
- Consequences: Simpler startup and debugging; strong module boundaries are still required. Heavy modules must remain optional/lazy. Some high-risk execution may need a separate process/sandbox.
