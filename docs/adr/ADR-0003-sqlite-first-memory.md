# ADR-0003: SQLite-First Storage and Memory

- Status: Accepted
- Decision: Use SQLite and FTS5 for local structured state and lexical search. Provide optional embedding/vector interfaces later.
- Context: Keep default installation light and avoid persistent background services on the target laptop.
- Consequences: Great default portability; advanced semantic retrieval requires an optional provider and must be measured.
