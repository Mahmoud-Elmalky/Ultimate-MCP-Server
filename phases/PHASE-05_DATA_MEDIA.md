# Phase 05 — Data, Spreadsheet, Database, and Media Engines

## Objective

Add useful local data and media capabilities through optional modules, without turning the default installation into a heavyweight data-science or media workstation.

## Prerequisites

Phase 01 core, Phase 03 document/file abstractions, and Phase 04 workspace permissions.

## A. CSV and tabular analysis

1. Use Python standard `csv` for light/basic operations.
2. Add Pandas/NumPy only in a `data` optional extra.
3. Implement `csv_analyze` and `data_profile` with row/column limits, type inference disclosure, missing values, duplicate detection, and sample preview.
4. Avoid reading arbitrarily large files fully into RAM; use chunked processing where possible.
5. Implement `data_clean_preview` that describes proposed transformations without writing.
6. Apply transforms to a new output artifact by default; persistent replacement requires approval.

## B. Spreadsheets

1. Use `openpyxl` for workbook-level read/edit where possible; keep Pandas optional for analytics.
2. Inspect sheet names, dimensions, formulas, cached values (where available), hidden sheets/rows, merged cells, and named ranges as appropriate.
3. Preserve formulas and formatting when editing; document library limitations.
4. Provide `spreadsheet_analyze`, `spreadsheet_edit_preview`, `spreadsheet_apply`, and `create_chart`.
5. Generate a copy/backup before changes. Do not overwrite originals without L2 approval.
6. Validate output by reopening the saved workbook and comparing expected sheets/cells/formulas.

## C. SQL and databases

1. Start with SQLite using read-only connections where possible.
2. `database_schema` is read-only.
3. `database_query` defaults to SELECT-only; parse/validate SQL rather than relying solely on keyword substring checks.
4. Add row/time/output caps and parameterized query guidance.
5. Add PostgreSQL/MySQL only through optional provider extras and explicit connection configuration.
6. Never expose passwords in logs or model-visible errors.
7. Database writes require separate explicitly approved operations; do not add write-capable SQL by default.

## D. Charts and artifacts

1. Produce charts as files (PNG/SVG/HTML) in output directory.
2. Return concise metadata and artifact paths/IDs.
3. Keep chart rendering optional; do not add plotting stack to core.
4. Do not hard-code visual colors unless requested by user.

## E. Media

1. Add image metadata and transformations with Pillow optional.
2. Add FFmpeg adapter for video/audio metadata, frame extraction, conversion, and extraction workflows; detect external binary.
3. Add speech-to-text only as optional provider; model weights must not be downloaded automatically.
4. Add ComfyUI adapter as an optional integration for local generation workflows; never embed large generation models in this MCP package.
5. Apply file size, duration, frame count, and output limits.
6. Media processing that writes artifacts is L2; remote upload or public sharing is L3.

## F. Tests

- CSV huge/malformed/encoding/quoted field fixtures.
- Spreadsheet formulas, formatting, hidden tabs, merged cells, corrupted workbook, and edit-preview validation.
- SQL read-only enforcement, SQL injection/DDL/DML rejection, query time/row caps.
- Artifact path policy and backup verification.
- FFmpeg/Pillow missing dependency behavior and resource limits.


## Recommended tooling and optional dependencies

- Python standard `csv` and `sqlite3` for the lightweight baseline.
- `openpyxl` for XLSX, `pandas`/`numpy` for optional advanced analysis.
- `Pillow` for image operations.
- FFmpeg executable for audio/video transforms; detect its presence and version rather than bundling it.
- Optional database drivers (for example PostgreSQL/MySQL) only if a real requirement exists; place each in a separate extra.
- ComfyUI integration should use its documented API and remain optional; no model weights are bundled or downloaded.

## Acceptance criteria

- Core package still installs and starts without Pandas, NumPy, openpyxl, Pillow, or FFmpeg.
- Spreadsheet edits preserve original files and validate their output.
- Database tools are read-only by default and bounded.
- Media features are optional and never auto-download huge models.
- Resource measurements exist for representative file sizes.
