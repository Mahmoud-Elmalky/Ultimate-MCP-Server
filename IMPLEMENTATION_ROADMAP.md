# Implementation Roadmap

## Phase sequence and outcomes

| Phase | Name | Primary outcome | Gate | Status |
|---|---|---|---|---|
| 00 | Baseline & Audit | Verified baseline, characterization tests, risk register | Current tools and working-tree state documented; baseline can be reproduced | `complete` (commit `61a9d45`) |
| 01 | Core Foundation | Package structure, config, registry, results, permissions, approvals, logging | Core starts without optional keys; security tests pass | `complete` (commit `36166b3`, checkpoint `2385d3e`) |
| 02 | Research & Utilities | Provider-based research, current data, safe calculator, legacy utility migration | Provider failures isolated; outputs have provenance/freshness | `complete` (branch `feat/v2-phase-2`) |
| 03 | Browser & Documents | Playwright adapter and bounded multi-format document processing | Hostile URLs/files and unapproved side effects blocked | `next` |
| 04 | Coding & System | Project-aware code tools, Git, tests, diagnostics, constrained commands | Workspace boundary and execution approval tests pass | `planned` |
| 05 | Data & Media | Spreadsheet/database/data/media support via optional extras | Core install remains lightweight; large file limits verified | `planned` |
| 06 | Memory & Knowledge | Explicit local memory, FTS5 search, optional embeddings/vector adapter | Scope isolation, deletion, retention, and provenance tests pass | `planned` |
| 07 | Intelligence & Workflows | Tool guidance and deterministic multi-step workflows | Bounded steps, no hidden LLM loop, recovery and approval gates work | `planned` |
| 08 | Evaluation & Release | Model compatibility, security/performance evaluation, packaging and docs | Release checklist met; limitations are transparent | `planned` |

## Dependency graph

- Phase 00 is prerequisite for all phases.
- Phase 01 is prerequisite for all domain modules.
- Phases 02–05 can be developed in parallel only after stable Phase 01 interfaces; merge serially and run the full suite.
- Phase 06 depends on Phase 01 and document interfaces from Phase 03; it may be prototyped earlier but final integration follows Phase 05.
- Phase 07 depends on stable interfaces from Phases 02–06.
- Phase 08 continuously tests every phase but final release evaluation follows Phase 07.

## Cross-phase rules

- Each phase must include code, tests, documentation, sample configuration, and a report.
- Do not add placeholder tools that return “not implemented” to a user-facing profile.
- Feature modules with missing optional dependencies must be clearly disabled with an actionable diagnostic.
- No phase can weaken the security invariants in `docs/SECURITY_MODEL.md`.
- No tool may silently lose source references or silently truncate a result.
- Use the smallest practical dependency set and measure the impact of new dependencies.

## Initial prioritization

P0: core safety and reliability, LM Studio host integration, current tools migration, research, browser/document basics, coding inspection, and benchmark foundation.

P1: rich data workflows, memory/knowledge, broader media, advanced providers, advanced workflows.

P2: extra integrations, optional dashboards, additional embedding engines, convenience features.

All approved product areas remain in the full scope. Priority controls sequencing, not permanent exclusion.

## Phase completion report

Use `prompts/PHASE_REPORT_TEMPLATE.md`. A phase is complete only when its acceptance criteria are evidenced by executed tests or explicitly marked as blocked/not applicable with rationale.
