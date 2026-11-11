# Phase 03 — Browser Automation and Document Intelligence

## Objective

Add optional Playwright-based browser automation and multi-format document reading/search/extraction while keeping default startup lightweight and all state-changing interactions approval-controlled.

## Prerequisites

Phase 01 core/security/approvals and Phase 02 URL/provenance primitives accepted.

## A. Browser module

1. Add a `browser` optional extra and a doctor check for Playwright package/browser binaries.
2. Create a dedicated browser context manager with explicit lifecycle, per-task sessions, bounded tabs, and cleanup.
3. Do not connect to the user's personal browser profile by default.
4. Implement `browser_open`, `browser_back`, `browser_tabs`, `browser_snapshot`, `browser_screenshot`, `browser_scroll`, and `browser_wait`.
5. Implement `browser_click`, `browser_type`, and `browser_select` with element identification, ambiguity checks, and bounded waits.
6. Separate navigation/read interactions from sensitive actions. Form submission, upload, external send, purchase, deletion, and account changes require L3 approval.
7. Downloads require approved destination, file type/size checks, and approval policy. Store files only under configured output roots.
8. Uploads require exact path/content hash, destination origin, and one-time approval.
9. Block `file://` and local-network access by default; allow a dedicated local-development profile only with explicit configuration.
10. Do not bypass CAPTCHA, auth, paywalls, anti-bot, or access-control systems.
11. Add screenshot artifact references; do not include huge encoded images in ordinary tool responses.

## B. Document extraction architecture

1. Create format-specific extractor interfaces with a shared document metadata/provenance model.
2. Start with text/Markdown/HTML/CSV/PDF. Add DOCX/XLSX/PPTX through optional dependencies.
3. Use lazy imports and explicit feature availability.
4. Return structured chunks with document ID, page/slide/sheet, paragraph/table/cell coordinates where available, and extraction warnings.
5. Define limits for file size, PDF pages, workbook sheets/rows/cells, archive expansion, slide count, and extraction time.
6. Reject or safely handle encrypted, malformed, unsupported, or suspicious archive documents.
7. Preserve tables instead of flattening all values into an undifferentiated text block.
8. OCR is optional and invoked only when text extraction is absent/poor or user asks for it; enforce image/page limits.

## C. Document tools

Implement, where supported:
- `document_read`
- `document_search`
- `document_extract_tables`
- `document_qa` (retrieval of passages with provenance, not unsupported generation)
- `document_compare`
- `document_convert` for explicit format pairs
- `document_create` and `document_edit` behind L2 approval
- `image_ocr` optional

Document writing/editing must preserve original files by creating a new output or backup. Never overwrite silently.

## D. Tests

- Playwright local fixture pages for navigation, snapshot, element ambiguity, download, and blocked submission.
- Test upload/form-submit approval and tampered approval payload.
- Test browser context isolation and cleanup.
- Fixtures for every document format including malformed and large files.
- PDF page provenance, DOCX paragraph/table, XLSX sheet/cell, PPTX slide provenance.
- Zip/decompression bomb and resource-limit tests.
- OCR disabled/missing dependency behavior.


## Recommended tooling and optional dependencies

- Playwright Python package plus browser binaries installed explicitly by the developer; do not auto-download at normal server startup.
- `pypdf` for PDF text extraction.
- `python-docx` for DOCX.
- `openpyxl` for XLSX.
- `python-pptx` for PPTX.
- `beautifulsoup4` for HTML extraction (shared with research extra where possible).
- OCR provider such as Tesseract only as a separately installed optional capability; document executable path and licensing.
- Use test fixtures and local static pages; no external live site is needed for normal tests.

## Acceptance criteria

- Core install works without Playwright or document extras.
- Browser tools are absent or clearly disabled when Playwright is unavailable.
- No browser upload/submit occurs without exact out-of-band approval.
- No arbitrary local/private network navigation occurs by default.
- Documents report extraction limits and precise provenance.
- Original documents are never silently overwritten.
