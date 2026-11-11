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

## A.1 Browser Security & Network Egress Architecture (Mandatory)

> **Critical Invariant:** Playwright URL interception (`page.route` / `context.route`) inspecting only the request URL string is **not** sufficient by itself to prevent DNS rebinding, and `src/ultimate_mcp/core/http_transport.py` does **not** automatically govern Chromium's native C++ network stack (`net::URLRequest`). Phase 03 must implement an enforceable browser egress control architecture.

1. **Main Navigation and Redirects:**
   - Every navigation (`browser_open`, link clicks, `window.location` changes, meta-refresh, and HTTP `301/302/303/307/308` redirects) must be validated hop-by-hop against `validate_and_resolve_url` (`http`/`https` only, allowed ports, public IP validation across all A/AAAA records).
2. **Subresource Requests (`image`, `script`, `stylesheet`, `font`, `media`, `iframe`/`frame`):**
   - `context.route("**/*", ...)` must intercept **all** resource types, not only top-level `document` requests. Disallowed schemes (`file://`, `ftp://`, `ws://`, `wss://`, `chrome://`, `view-source:`) and private/internal hosts are aborted before transmission. Subframes (`frame` / `iframe`) are subject to the exact same origin and IP policy as main frames.
3. **JavaScript `fetch` / `XMLHttpRequest` (`xhr`, `fetch`, `eventsource`, `ping`):**
   - All asynchronous in-page requests (`fetch`, `xhr`, `eventsource`, `beacon`/`ping`) are intercepted and validated through the same pinned-IP egress policy; `ping`/`beacon` requests are aborted by default.
4. **WebSockets (`ws://`, `wss://`):**
   - Blocked by default in `context.route` (`resource_type == "websocket" -> route.abort("accessdenied")`) because long-lived WebSocket frames bypass HTTP response size limits and standard route fulfillment.
5. **Service Workers and Browser Background Requests:**
   - Service workers must be blocked at context creation (`browser.new_context(service_workers="block")`) so pages cannot register background workers that outlive page navigation or bypass page-level route handlers.
   - Chromium must launch with background networking disabled: `--disable-background-networking`, `--disable-sync`, `--disable-component-update`, `--disable-domain-reliability`, `--no-pings`, and `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` (preventing WebRTC STUN/UDP local-IP discovery and direct UDP egress).
6. **DNS Rebinding & Enforceable Pinned-IP Egress Design:**
   - Because Chromium resolves DNS independently if allowed to connect directly (`route.continue_()`), Phase 03 must prevent unvalidated Chromium DNS resolution using a two-layer defense:
     - **Layer 1 (Chromium Host-Resolver Lockdown):** Launch Chromium with `--host-resolver-rules="MAP * ~NOTFOUND , EXCLUDE 127.0.0.1"` when running in strict egress mode so Chromium's native resolver cannot resolve or dial arbitrary external/internal hostnames directly.
     - **Layer 2 (Pinned-IP Route Fulfillment / Loopback Validating Proxy):** Fulfill permitted HTTP(S) requests in `context.route("**/*", ...)` via `route.fulfill(...)` backed by Ultimate MCP's SSRF-hardened, pinned-IP `fetch_safe_url` (`src/ultimate_mcp/core/http.py`)—or route Chromium through a loopback validating proxy bound to `127.0.0.1:0` that performs `validate_and_resolve_url` + `verify_connected_peer_ip` before forwarding any bytes.
7. **Private, Loopback, Link-Local, CGNAT, and Cloud Metadata Addresses:**
   - Reject all IPv4/IPv6 loopback (`127.0.0.0/8`, `::1`), RFC 1918 private (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local and cloud metadata (`169.254.0.0/16`, `fe80::/10`, `metadata.google.internal`), ULA (`fc00::/7`), CGNAT (`100.64.0.0/10`), and IPv4-mapped IPv6 (`::ffff:127.0.0.1`, `::ffff:169.254.169.254`) addresses across main navigations, redirects, subresources, and XHR/fetch calls.
8. **Browser Downloads and Uploads:**
   - **Downloads:** Disabled by default (`accept_downloads=False`). When explicitly invoked via `browser_download`, require L2/L3 approval, validate the source URL and final redirect target, enforce a strict byte limit (`max_download_bytes`), reject executable extensions (`.exe`, `.bat`, `.ps1`, `.cmd`, `.msi`, `.dll`, `.scr`), and save exclusively inside the configured `output_dir`.
   - **Uploads:** `browser_upload` requires the source file to reside within `effective_read_roots`, computes its SHA-256 hash, binds `(file_path, sha256, target_origin, selector)` into an L3 approval request (`prepare_action`), and executes only with a valid one-time approval token.
9. **Browser Session Isolation:**
   - Every browser session uses a fresh, ephemeral `BrowserContext` (`browser.new_context()`) with isolated cookies, `localStorage`, and `IndexedDB`. Never attach to the user's personal Chrome/Edge profile directory (`user_data_dir` is never pointed at `%LOCALAPPDATA%\Google\Chrome` or `%LOCALAPPDATA%\Microsoft\Edge`), and enforce a strict cap on concurrent contexts and open tabs (`max_tabs=5`).
10. **Documented Windows Platform Limitations:**
    - Unlike Linux, standard unprivileged Windows processes do not have kernel network namespaces (`CLONE_NEWNET`), `seccomp-bpf` syscall filters, or per-process `iptables` rules without Administrator-level Windows Filtering Platform (WFP) drivers.
    - Therefore, on Windows, network isolation relies on Chromium process command-line lockdown (`--host-resolver-rules`, `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`, `--disable-background-networking`), `service_workers="block"`, and `context.route("**/*", ...)` fulfillment via `fetch_safe_url` / loopback proxy rather than OS kernel sandboxing. This limitation must be disclosed in `docs/SECURITY_MODEL.md` and the Phase 03 report.

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
