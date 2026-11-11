# Phase 00 — Baseline Audit and Characterization

## Objective

Establish the verified current state of the repository, protect existing user work, characterize legacy tool behavior, and produce an evidence-based migration plan. No broad refactor or feature expansion in this phase.

## Inputs

- Current repository working tree.
- `PROJECT_BRIEF.md`, `IMPLEMENTATION_ROADMAP.md`, `docs/MIGRATION_PLAN.md`, `docs/LM_STUDIO_COMPATIBILITY.md`.
- User's current Python/uv/LM Studio installation if available.


## Required tools and environment

- Git CLI and repository access.
- Python 3.11+ available in an isolated environment.
- `uv` recommended for reproducible environments; `pip` may be used for inspection only.
- Current LM Studio desktop app for a real-host test, if available.
- Node.js/npx only if MCP Inspector is used.
- No new runtime dependency is required in this phase.

## Detailed steps

### A. Repository and Git safety
1. Run `pwd`/equivalent and identify repository root.
2. Inspect `git status --short --branch`, current branch, remotes, latest commits, tracked/untracked files, and ignored state.
3. Preserve all existing changes. Never reset, clean, or overwrite user files.
4. If clean and permitted, create a feature branch such as `refactor/v2-foundation`; if not clean, document changes and do not stash/commit user work without permission.
5. Record commit hash and tree inventory in `docs/reports/phase-00-baseline.md`.

### B. Code and dependency inventory
1. Read all Python source, README, requirements, env example, config, and any tests.
2. Inventory every MCP tool: name, signature, description, default values, provider, dependencies, side effects, error behavior, output shape, and limits.
3. Search for unsafe constructs: `eval`, `exec`, `shell=True`, subprocess, path reads/writes, URL fetching, redirects, temp files, secrets, network listeners, global client initialization, unbounded loops, and broad exception handling.
4. Inventory package versions and Python requirement. Identify stale/deprecated APIs, especially MCP SDK and YouTube transcript API.
5. Confirm current host configuration and LM Studio version if available.

### C. Reproduce current behavior
1. Create a clean isolated environment without modifying the user's active environment.
2. Install the current declared dependencies as safely as possible; record failures and unpinned-version risk.
3. Add characterization tests for each existing tool using mocks/fixtures. Do not make live API calls by default.
4. Test missing/invalid API keys, network timeouts, empty results, malformed responses, invalid symbols, bad time zones, unsupported file extensions, oversized files, and missing paths.
5. Capture expected legacy behavior and any behavior that should intentionally change for safety.

### D. SDK and host compatibility research
1. Verify the official MCP Python SDK current stable line, release tag, Python minimum, migration guide, import paths, transport APIs, and test tooling.
2. Verify the exact installed LM Studio version and current `mcp.json` format/documentation.
3. Build a tiny throwaway compatibility server only if needed; do not migrate the main server yet.
4. Test MCP Inspector/SDK client and the actual LM Studio Host if the environment is available.
5. Record host/model gaps separately. Do not infer model tool-calling quality from protocol initialization.

### E. Risk register and baseline scorecard
1. Rank risks by severity and likelihood.
2. Record baseline startup behavior, import-time dependencies, default tool count, tool description footprint (estimate if exact tokenizer unavailable), and resource use if measurable.
3. Classify each legacy tool as preserve, split, replace, or deprecate.
4. Document target module mapping and migration sequencing.

## Required outputs

- `docs/reports/phase-00-baseline.md`.
- `docs/reports/legacy-tool-inventory.md`.
- Characterization tests under `tests/characterization/`.
- Initial risk register.
- Confirmed dependency and SDK recommendation.
- LM Studio compatibility notes and any blocker.
- Phase report using `prompts/PHASE_REPORT_TEMPLATE.md`.

## Acceptance criteria

- Git state and user modifications are documented and preserved.
- All current tools and dependencies are inventoried.
- No destructive repository action occurred.
- Existing behavior is covered by characterization tests or any untestable path is explicitly documented.
- SDK and LM Studio compatibility claims are verified against current official docs and actual runtime where available.
- A clear, incremental migration sequence exists.

## Do not do

- Do not delete `ultimate_mcp_server.py`.
- Do not rename or remove legacy tools in this phase.
- Do not introduce Qdrant, Docker, a dashboard, or heavyweight dependencies.
- Do not add new functionality before the baseline is understood.
- Do not claim LM Studio works unless actually tested.

## Exit report

List exact commands/tests executed, failures, risks, compatibility matrix, and the first implementation slice for Phase 01.
