# Code Review Prompt — Ultimate MCP Server

Review the proposed diff for correctness, maintainability, backwards compatibility, resource footprint, API/tool schema quality, and test coverage.

Verify:
- handlers are thin and services are independently testable;
- types, schemas, errors, and MCP response semantics are consistent;
- no optional dependency is imported eagerly by core;
- timeouts/output limits/cancellation/retry policies are correct;
- no state-changing action bypasses the approval broker;
- tool descriptions are concise, non-overlapping, and suitable for local models;
- provenance/freshness/uncertainty are retained;
- profile registration matches actual capability availability;
- docs and catalog match code;
- tests prove behavior, not merely mock the function under test.

Report findings sorted by severity and distinguish defects from suggestions. Do not approve a phase solely because tests pass if critical behavior is untested.
