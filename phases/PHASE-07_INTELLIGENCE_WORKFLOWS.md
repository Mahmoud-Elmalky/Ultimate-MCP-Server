# Phase 07 — AI Intelligence Helpers and Deterministic Workflows

## Objective

Improve how local models discover and coordinate tools without embedding a second autonomous LLM agent inside the MCP server. Provide deterministic bounded workflows, task state, context-cost helpers, validation, and tool guidance.

## Prerequisites

Stable modules from Phases 02–06 and reliable core from Phase 01.

## A. Tool intelligence

1. Build searchable registry metadata by task/domain/capability and permission level.
2. Implement `tool_catalog_search` to return only concise relevant metadata.
3. Implement `tool_recommend` based on explicit task description and registry tags; state that it is a recommendation, not automatic execution.
4. Build profile guidance docs/prompt snippets for default/research/coding/documents/browser/full profiles.
5. Keep tool descriptions distinct and short. Remove synonym tools that confuse weaker tool-calling models.

## B. Task and plan validation

1. Define a simple typed Plan model with steps, required tools, dependencies, expected outputs, risk levels, and max budgets.
2. Implement `task_plan_validate` to detect missing inputs, unavailable modules, dependency cycles, excessive steps, unapproved side effects, and missing verification steps.
3. Do not claim this validates reasoning truth; it validates structural and policy properties.
4. If a plan includes an L2/L3 operation, require an approval checkpoint before that step.

## C. Context utilities

1. Implement `context_estimate` using configured model tokenizer when available; otherwise report a heuristic estimate and confidence.
2. Do not assume one tokenizer works for every model family.
3. Implement `context_compress_preview` as a deterministic extractive/structure-preserving draft where possible; preserve citations, paths, IDs, dates, and uncertainty markers.
4. Never silently discard facts or evidence. Return original-to-summary traceability where feasible.
5. Keep all context helpers optional from default profile if they significantly increase tool count.

## D. Output validation

1. Implement strict JSON parsing and schema validation using safe, supported JSON Schema subset.
2. Set recursion, object, array, string, and payload-size limits.
3. Implement deterministic output checks (required fields, citations present, file exists, arithmetic matches) only where objective checks are possible.
4. Name `output_evaluate` accurately: it is a checklist/validator, not a truth detector.

## E. Deterministic workflows

Implement a small set of high-value workflows:

- `deep_research`: search → deduplicate → read selected pages → compare evidence → assemble result.
- `document_analysis`: inspect → extract → search relevant passages → return cited findings.
- `coding_audit`: inspect repository → check status → run safe static checks → report; do not edit.
- `patch_workflow`: prepare diff → request approval → apply exact approved patch → run allowed tests.
- `data_analysis`: profile → propose cleaning → calculate summaries → create artifact.
- `website_audit`: bounded crawl → extract page metadata/links → run defined checks → report.

Each workflow must have:
- strict max steps, duration, result size, pages/files/rows;
- persisted task ID and status;
- cancellation and safe checkpoint/recovery;
- approval at the point of side effect;
- idempotency and duplicate prevention;
- trace of tools/providers invoked;
- explicit partial/failure outcome.

Do not create an unlimited autonomous agent loop. Do not call an LLM behind the user's back. The host model remains the planner/reasoner unless a separately approved future architecture adds an explicit model provider with user-controlled cost and privacy settings.

## F. Tests

- registry relevance and profile filtering tests;
- plan cycle/budget/permission validation;
- tokenizer-vs-heuristic labeling;
- JSON/schema adversarial limits;
- workflow step timeout, cancellation, partial result, restart recovery, approval interruption, and idempotency tests;
- ensure workflow cannot bypass core permission layer.


## Recommended tooling and optional dependencies

- Pydantic for plan/result schemas.
- Registry metadata and deterministic Python validators; no agent framework required.
- Optional tokenizer libraries only when exact model/tokenizer mapping is known. Otherwise provide a clearly labeled heuristic estimate.
- SQLite task-state tables and existing execution/approval services.
- Do not add LangChain, LangGraph, CrewAI, or another orchestration framework unless a specific measured need and ADR justify the additional footprint.

## Acceptance criteria

- Relevant tools can be found without exposing every tool to every model.
- Plans are checked for structural/policy errors.
- Workflows are finite, observable, cancellable, and permission-aware.
- No hidden model calls or unbounded loops exist.
- Context estimates disclose whether exact or heuristic.
