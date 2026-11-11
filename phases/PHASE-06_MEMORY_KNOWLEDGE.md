# Phase 06 — Memory and Knowledge Management

## Objective

Build user-controlled local memory and document knowledge retrieval with clear scopes, explicit retention/deletion, source provenance, and low resource use.

## Prerequisites

Phase 01 SQLite/config/security; Phase 03 document extraction; Phase 05 data/storage output patterns.

## A. Scope model

Use explicit scopes:
- session/task: temporary state with TTL;
- project: knowledge associated with a named workspace/project;
- user: explicitly saved long-term facts/preferences;
- knowledge collection: user-selected documents indexed for retrieval.

Do not infer that every conversation is available. Tools can only store/search information explicitly supplied or indexed.

## B. SQLite schema and repository

1. Create versioned schema migrations.
2. Add tables for memories, projects, documents, chunks, metadata, retention, and audit events.
3. Add indexes by scope, project, type, and creation/update time.
4. Add FTS5 virtual tables where supported; detect capability and fail gracefully.
5. Add repository methods with parameterized SQL and transaction handling.
6. Add export/backup and deletion paths.

## C. Memory tools

1. Implement `memory_save` only for explicit user-selected content; enforce L2 consent/policy.
2. Implement `memory_search` and `memory_list` within explicit scope.
3. Implement `memory_update` and `memory_delete` with confirmation and exact item IDs.
4. Implement retention/expiry and a purge preview.
5. Do not save credentials, sensitive personal categories, or arbitrary private content automatically.

## D. Knowledge ingestion and retrieval

1. Implement `knowledge_ingest` for explicitly selected local files or artifacts.
2. Extract through existing document extractors.
3. Chunk by document structure (heading/page/paragraph/table) rather than naive fixed characters where possible.
4. Preserve provenance: file ID/path alias, hash, page/slide/sheet/row/cell, chunk ID, extraction time.
5. Use FTS5 lexical retrieval first.
6. Define `EmbeddingProvider` and `VectorStore` interfaces; embeddings/vector DB are optional extras.
7. If local embedding endpoint/provider is used, require explicit configuration and verify it does not send data to an unintended remote host.
8. Return relevant excerpts and provenance, not unsupported synthesized answers.
9. Implement deletion of source documents and all derived chunks/embeddings with verifiable cleanup.

## E. Project context

Implement `project_context_load` for scoped project metadata, approved files, and summaries. Do not automatically read every file. Respect `.gitignore` only as an additional selection hint, never as security boundary.

## F. Tests

- scope isolation between users/projects/collections;
- FTS5 unavailable fallback;
- indexing, deduplication by content hash, update, delete, and retention;
- provenance for PDF pages, DOCX structures, spreadsheet cells, and text sections;
- embedding provider failure and no-network guarantee in local-only mode;
- sensitive-content and secret exclusions;
- backup restore validation.


## Recommended tooling and optional dependencies

- Python `sqlite3` and SQLite FTS5 for mandatory local storage/search.
- `platformdirs` for application-state directories.
- Optional embedding client/provider selected by configuration. A local LM Studio embedding endpoint is allowed only after verifying endpoint/model support and local-only routing.
- Optional vector backend (e.g., Qdrant or an embedded alternative) only after a benchmark demonstrates material retrieval benefit.
- Avoid `sentence-transformers` and GPU embedding stacks in the core installation; they can consume substantial RAM/VRAM.

## Acceptance criteria

- Memory is opt-in and explicitly scoped.
- No automatic ingestion of conversation history occurs.
- Search results include traceable source provenance.
- Deleting a memory/document removes all related indexed records.
- FTS5 provides useful retrieval without requiring a vector service.
- Semantic search is optional and resource/network behavior is explicit.
