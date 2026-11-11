# Phase 08 — Evaluation, Hardening, Compatibility, and Release

## Objective

Demonstrate reliable operation with real local models and LM Studio, close security/performance gaps, publish complete documentation, and prepare a versioned release without overstating capability.

## Prerequisites

Phases 00–07 implemented and phase reports available.

## A. Full regression

1. Run format, lint, type, unit, integration, security, and deterministic provider tests.
2. Test every profile and every registered tool schema.
3. Verify missing optional dependencies/keys disable only their module.
4. Verify stdio stdout protocol cleanliness.
5. Verify error semantics, output caps, pagination, timeout, cancellation, retry and audit behavior.
6. Verify no protected action can execute without a valid one-time approval.

## B. LM Studio compatibility

1. Record exact LM Studio version and OS.
2. Install the server through documented `mcp.json` config.
3. Test actual Host tool discovery and calls; capture logs and configuration with secrets removed.
4. Test default profile first, then focused profiles and full profile.
5. Test at least one simple and one multi-step task for each available target model.
6. Test permission denied and approval flow, including CLI sidecar.
7. Separate protocol issues from model tool-calling limitations by repeating deterministic calls through MCP Inspector/SDK client.
8. Record models that fail and whether issue is schema, template, context, or reasoning behavior.

## C. Benchmark suite

1. Version JSONL task sets by domain and difficulty.
2. Run repeated trials where model outputs are nondeterministic.
3. Record tool selection, argument correctness, execution success, task completion, factual accuracy, source quality, recovery, safety, tokens, latency, and resource usage.
4. Compare against Phase 0 baseline where applicable.
5. Report per-model and per-profile results; do not average away a safety failure.
6. Identify the tool descriptions and schemas that cause the most errors/context overhead.

## D. Performance and hardware

1. Measure startup and idle memory for default profile.
2. Measure peak memory for representative PDF, XLSX, browser, and media operations separately.
3. Measure concurrency and provider timeout behavior.
4. Test on target hardware if available; otherwise clearly mark measurements as unavailable.
5. Recommend settings for 16 GB RAM / 4 GB VRAM conservatively; do not use the GPU for core MCP workloads unnecessarily.

## E. Security hardening

1. Run security test matrix from `docs/SECURITY_MODEL.md`.
2. Review all external input sinks (URL, file path, browser action, subprocess, SQL, archive parser, provider response).
3. Run dependency vulnerability audit against locked dependencies.
4. Review secret redaction and local state permissions.
5. Verify no sensitive logs/artifacts are tracked.
6. Verify defaults bind HTTP only to loopback and stdio remains normal mode.

## F. Packaging and documentation

1. Build wheel/sdist and install in clean environment.
2. Test CLI entry points.
3. Update README: capabilities, profiles, install, LM Studio setup, optional extras, security, troubleshooting, limitations.
4. Update tool catalog to match actual registered tools.
5. Document exact dependency extras and version lock policy.
6. Add changelog and migration guide for old tool names/config.
7. Confirm `.env.example` has placeholders only and all local files are ignored.

## G. Release decision

Prepare release candidate only if:
- no critical security failure is open;
- all deterministic tests pass;
- compatibility and performance test results are attached;
- known limitations are documented;
- all implemented tools have descriptions, schemas, permissions, and tests;
- install/config/run/uninstall/upgrade paths are reproducible.

Do not push, publish, tag, or create a GitHub release without explicit user approval.

## Acceptance criteria

- All deterministic unit, integration, protocol, and security tests pass, with any exclusions justified and recorded.
- No critical/high security finding remains unresolved; all L2/L3 actions are approval-gated and replay-resistant.
- LM Studio integration is tested on a recorded app version, and model-specific results are reported separately.
- Every enabled tool has a schema, permission classification, timeout/output budget, tests, and documentation.
- Default installation remains lightweight and optional features fail gracefully when dependencies are missing.
- Package build/install and CLI commands work in a clean environment.
- Performance and compatibility claims are supported by recorded measurements; unavailable measurements are labeled as such.
- No release is published without explicit user authorization.

## Required outputs

- `docs/reports/phase-08-evaluation.md`.
- Per-model compatibility matrix.
- Performance report.
- Security review report.
- Release checklist and changelog.
- Updated README and tool catalog.
## Recommended tooling and environment

- `pytest`, `pytest-cov`, Ruff, mypy/Pyright, and a dependency audit tool such as `pip-audit` or an equivalent current tool.
- MCP Inspector (requires Node.js/npx) for protocol inspection.
- Actual LM Studio desktop app and representative local model files for host/model tests.
- A fixed JSONL evaluation dataset with mock provider fixtures.
- GitHub Actions or another CI runner for deterministic tests; real-model/LM Studio tests remain local/manual unless runners have the necessary resources and licenses.


