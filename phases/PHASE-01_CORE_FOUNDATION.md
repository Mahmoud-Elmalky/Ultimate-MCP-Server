# Phase 01 — Core Foundation, Reliability, Security, and Packaging

## Objective

Create a maintainable Python package and a lightweight MCP gateway with shared configuration, tool registration, result/error contracts, execution policies, approvals, logging, and diagnostics. Keep the server startable without optional API keys.

## Prerequisites

Phase 00 accepted; working tree protected; SDK and LM Studio target versions documented.


## Recommended tooling and dependencies

- `uv` for environment and lock management.
- Official `mcp[cli]` SDK v2 (exact version selected after compatibility verification).
- `pydantic` and `pydantic-settings` for typed models/configuration.
- `platformdirs` for platform-aware state paths.
- Python standard library `sqlite3`, `ast`, `pathlib`, `subprocess`, `logging`, and `asyncio` where appropriate.
- Development: `pytest`, `pytest-asyncio` if needed, `pytest-cov`, `ruff`, and `mypy` or Pyright.
- Do not add a separate queue, cache server, or vector database in this phase.

## Detailed implementation

### A. Package and developer tooling
1. Create `pyproject.toml` with Python >=3.11, project metadata, CLI entry point, core dependency group, optional extras, and dev dependencies.
2. Use `uv` and generate a lockfile from tested versions. Pin the MCP SDK major and resolve to a tested exact lock.
3. Create `src/ultimate_mcp/` package layout according to `docs/ARCHITECTURE.md`.
4. Add `.gitignore`, `.env.example`, package CLI skeleton, and developer commands.
5. Preserve old source until parity migration is complete.

### B. Configuration
1. Implement one validated settings object.
2. Define default state/output directories using a platform-aware location.
3. Support explicit read and write roots; do not default to the full user profile or drive root.
4. Validate profile, log level, output limits, concurrency, timeouts, provider keys, and optional feature flags.
5. Ensure missing provider keys do not stop core startup.
6. Redact secret fields from diagnostics and repr/log output.

### C. MCP bootstrap and registry
1. Create `app.py` and a bootstrap module that creates the MCP server using the exact pinned SDK API.
2. Keep stdio as default transport and ensure stdout is reserved for protocol messages.
3. Build a registry model for tools with name, module, description, input model, permission, timeout, output budget, idempotency, optional dependencies, and provenance behavior.
4. Implement startup profiles: `default`, `research`, `coding`, `documents`, `browser`, `full`.
5. Register only implemented/enabled tools. Missing optional dependencies must generate a diagnostic and omit affected tools.
6. Add a catalog/doctor output that lists tool availability and reason for disabled modules.

### D. Standard results and errors
1. Define Pydantic models/enums for internal results, provenance, warnings, and typed errors.
2. Add safe MCP response adaptation: concise text always; structured content only where verified.
3. Map unexpected errors to safe `INTERNAL_ERROR` while logging a redacted traceback locally.
4. Distinguish `partial`, `needs_approval`, provider unavailable, timeout, invalid input, and successful empty results.
5. Enforce response byte limits and pagination/artifact references.

### E. Execution manager
1. Add per-category timeout configuration.
2. Add global/per-provider concurrency semaphores.
3. Add cancellation propagation where SDK allows.
4. Add retry policy with bounded exponential backoff + jitter for transient idempotent operations only.
5. Add idempotency/request IDs for side-effecting operations.
6. Add response-size guard and content-length checks.
7. Do not retry writes/submissions/deletes without proven idempotency.

### F. Security policy and approval broker
1. Implement permission enum L0–L4.
2. Build policy evaluation that fails closed.
3. Build immutable prepared-action record with canonicalized payload digest.
4. Implement pending/approved/rejected/expired/consumed states in SQLite.
5. Implement one-time approval consumption in a transaction.
6. Implement external local CLI command to approve/reject by ID. Do not expose a model-callable approval function.
7. Implement `prepare_action`/`execute_approved_action` flow or a tightly typed equivalent.
8. Revalidate policy, payload digest, file state, path, and expiry immediately before side effects.
9. If the human approval channel cannot be reached, deny execution.

### G. Safe calculator and filesystem primitives
1. Implement AST calculator allowlist; no `eval`.
2. Bound expression length, numeric magnitude, exponentiation, function names, and recursion/operation count.
3. Implement path resolver constrained to approved roots; resolve symlinks and Windows path edge cases.
4. Add bounded file read helpers and atomic write helper for later modules.
5. Add secret redaction utility and structured logs to stderr/file.

### H. Tests
1. Unit-test settings, registry/profile filtering, errors/results, path policy, AST calculator, approval digest/expiry/replay, retry, output caps, and secret redaction.
2. Integration-test server startup without API keys and with each profile.
3. Test stdio protocol output has no logs on stdout.
4. Test approval CLI uses the same state DB and cannot be invoked by the model through MCP.
5. Run type checking, linting, formatting, and the characterization suite.

## Deliverables

- Working package skeleton and CLI.
- `pyproject.toml`, lockfile, `.env.example`, `.gitignore`.
- Core models, registry, policies, approvals, execution manager, diagnostics.
- Unit/integration/security tests.
- Updated architecture/configuration docs.

## Acceptance criteria

- Core server starts without Tavily/YouTube keys and without optional browser/document/media dependencies.
- Profiles expose only enabled/implemented tools.
- Protected action cannot execute without out-of-band approval.
- Approval is exact-payload, expiring, one-time, and cannot be self-approved by model.
- Unsafe calculator evaluation is absent.
- Filesystem helper blocks traversal and symlink escapes in tested cases.
- Timeouts/output limits/error taxonomy work in tests.
- Legacy server remains available until tool-by-tool migration is complete.

## Do not do

- Do not register placeholder tools with fake success.
- Do not rely on tool descriptions as permission enforcement.
- Do not claim pure Python execution is a sandbox.
- Do not add a vector database or browser binaries to core dependencies.
