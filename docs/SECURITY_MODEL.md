# Security and Controlled Execution Model

## 1. Threat model

Assume that:
- the model may choose the wrong tool or provide malicious/malformed arguments;
- user-supplied code, files, webpages, PDFs, transcripts, comments, and search results may contain prompt injection;
- URLs may point to private or metadata services or redirect there;
- paths may contain traversal, symlink, UNC, or drive-prefix tricks;
- external APIs may return oversized, malformed, stale, or adversarial data;
- the calling model is not a trusted authorization authority;
- local host capabilities may expose private files or network access.

Security controls must be enforced by the server and OS boundary, not by tool descriptions or system prompts alone.

## 2. Permission classes

- L0 Read: constrained read-only operations inside policy-approved roots or public web scope.
- L1 Prepare: analyze, calculate, draft, preview, or create an in-memory artifact without persistent/external side effects.
- L2 Write: create or modify persistent local artifacts, edit workbooks, create branches, or write files.
- L3 Sensitive: delete, upload, submit, send, execute arbitrary commands, interact with payments/accounts, or expose private data externally.
- L4 Denied: prohibited by default, such as access-control bypass, credential theft, destructive system actions, or unsafe capabilities without a separate security review.

L2 and L3 require approval by default. A deployment may allow a narrow L2 action through explicit user-authored policy only after tests prove the scope. Never allow model-authored policy changes.

## 3. Approval broker design

Use a two-step model:

1. `prepare_*` validates and constructs an immutable operation request. It writes a pending record and returns `approval_id`, a human-readable summary, target path/destination, and a digest. It performs no side effect.
2. A user approves out-of-band using a local CLI or trusted UI. The model cannot call an approval tool to approve itself.
3. `execute_approved_action` loads the stored request, checks status, expiry, one-time usage, exact canonical digest, current policy, and current target state, then executes.
4. Mark consumed atomically and record result. A changed argument/path/content requires a new approval.

The initial approval interface is a separate local CLI process sharing the configured SQLite state database. If the MCP server runs under LM Studio with no interactive terminal, the sidecar CLI remains the human approval channel. If that channel is unavailable, keep the action blocked.

Approval record should contain: UUID, action type, canonical arguments digest, redacted summary, created/expiry time, requester/correlation ID, policy version, status, approver marker (local user), consumed time, result reference. Do not store secrets in approval summaries.

## 4. Filesystem policy

- Define explicit read roots and write roots in configuration.
- Resolve requested paths with `Path.resolve(strict=False)` and verify containment under allowed roots after resolving symlinks.
- Reject traversal, device paths, unsupported UNC paths, and paths that escape roots.
- Apply per-format size/page/row limits before extraction.
- Avoid following symlinks outside configured roots.
- Use temp-file + atomic replace for approved writes where safe.
- Never recursively delete by default. Require exact target listing and approval for destructive operations.
- Store generated artifacts in a dedicated output directory; do not overwrite user files silently.

## 5. Web and SSRF policy

- Permit only `http` and `https` schemes.
- Reject userinfo credentials, malformed hosts, IP literals in restricted ranges, localhost aliases, loopback, private, link-local, multicast, reserved, and cloud metadata destinations.
- Resolve DNS and validate all returned addresses; defend against DNS rebinding by pinning/rechecking the resolved target during connection where the HTTP stack permits.
- Disable automatic redirects. Follow manually with a small maximum hop count and revalidate each target.
- Block non-standard ports by default or configure an explicit allowlist.
- Enforce connect/read/total timeouts, compressed and decompressed response-byte limits, content-type allowlists, and bounded parsing.
- If strong DNS/redirect enforcement cannot be implemented and tested, disable arbitrary URL fetch by default; allow known provider domains or explicit user-approved host allowlists only.

## 6. Shell and code execution

- Use `subprocess` with argument arrays; never `shell=True`.
- Default to an executable allowlist (e.g. selected test/lint tools) and a configured workspace CWD.
- Validate each argument and prohibit command chaining, redirection, and shell metacharacters by never invoking a shell.
- Use an allowlisted environment; do not pass secrets by default.
- Enforce timeout, stdout/stderr byte limits, process-tree termination, and concurrency limits.
- A Python-level “restricted exec” is not a security sandbox. Only call execution sandboxed if an actual OS/container isolation boundary has been configured and tested (for example a properly constrained container or platform sandbox). Otherwise disable arbitrary execution or require approval with very narrow commands.
- Never use `eval` or `exec` for calculator or model-provided snippets.

## 7. Browser controls

- Start an isolated ephemeral Playwright `BrowserContext` per session without access to the user's normal Chrome/Edge profile (`service_workers="block"`, `accept_downloads=False`, `ignore_https_errors=False`, `permissions=[]`). Enforce configurable resource limits (`browser_max_sessions=2`, `browser_max_tabs=5`, `browser_max_download_bytes=2 MiB`, `browser_max_response_bytes=2 MiB`, `browser_navigation_timeout_ms=15000`).
- **Browser Network Stack Lockdown:** Launch headless Chromium with `--host-resolver-rules=MAP * ~NOTFOUND` (standalone) or `MAP egress-proxy.invalid 127.0.0.1 , MAP * ~NOTFOUND` paired with `--proxy-server=http://egress-proxy.invalid:<port>`, `--proxy-bypass-list=<-loopback>`, `--disable-quic`, `--disable-background-networking`, `--disable-sync`, `--disable-component-update`, `--disable-domain-reliability`, and `--no-pings` — with zero `EXCLUDE 127.0.0.1` exception so literal loopback and IPv6 addresses cannot bypass validation.
- **Playwright Routing & WebSocket Blocking:** Register `context.route_web_socket("**/*", ...)` before opening any page (aborting all `ws://` and `wss://` connections across loopback, private, and public IPs by default) and `context.route("**/*", ...)` to fulfill HTTP(S) navigations, frames, scripts, images, and fetch/XHR calls exclusively via pinned-IP [`fetch_safe_url`](src/ultimate_mcp/core/http.py) (never `route.continue_()`).
- **Validating Proxy Layer ([`LoopbackValidatingProxy`](src/ultimate_mcp/modules/browser/proxy.py)):** Binds on `127.0.0.1:0`, rejects all raw `CONNECT` tunnels with `403 Forbidden`, blocks state-changing proxy requests directly (`403`), and validates/pins every plain HTTP request through `fetch_safe_url` as a defense-in-depth backstop if Chromium attempts non-route network traffic.
- **WebRTC Restrictions:** Launches with `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` and `--enforce-webrtc-ip-permission-check`, plus [`WEBRTC_DISABLE_INIT_SCRIPT`](src/ultimate_mcp/modules/browser/policy_types.py). Empirical Chromium `158.0.8127.18` probe evidence shows `disable_non_proxied_udp` alone stops raw UDP STUN/TURN candidates while `turn:...transport=tcp` still dials TCP unless host-resolver/proxy lockdown rejects the resulting `CONNECT` attempt (`403`), yielding zero STUN/TURN listener hits under the full three-layer stack.
- **L3 Out-of-Band Mutation & Transfer Approvals:** All state-changing HTTP methods (`POST`, `PUT`, `PATCH`, `DELETE`), HTML form submissions (`browser_submit_form`), downloads (`browser_download`), and uploads (`browser_upload`) block by default and require single-use, TTL-bounded (`now >= expires_at`) out-of-band `L3_SENSITIVE` `ApprovalBroker` records bound via canonical SHA-256 digests (`method`, `origin`, `url`, `content_type`, `request_origin_header`, `auth_context_sha256`, `session_state_sha256`, `headers_sha256`, `session_id`, `body_sha256`, plus `file_sha256` + `target_file_hash` for uploads), with `max_redirects=0` on mutations so redirects never reuse an approval. Downloads block executable extensions and write only inside `output_dir`.
- **Windows Firewall Limitations & Residual Risks (No Absolute OS-Level Isolation Claim):** Standard unprivileged Windows processes do not have Linux kernel network namespaces (`CLONE_NEWNET`), `seccomp-bpf` filters, or per-process `iptables` rules. Consequently, the default configuration enforces isolation within the Chromium network stack, Playwright routing, and loopback proxy — **not** at the OS kernel level. [`build_windows_firewall_guidance()`](src/ultimate_mcp/modules/browser/policy_types.py) provides optional elevated Windows Defender Firewall commands for strict deployments. **Residual risks remain:** a Chromium renderer/network-process sandbox escape, native-code exploit, or unproxied OS-level binary execution could bypass user-space Chromium flags and proxy enforcement unless external OS firewall / Windows Filtering Platform (WFP) rules or container/VM isolation are configured.
- Do not bypass CAPTCHA, authentication, anti-bot protections, paywalls, or access controls.
- Treat page text, labels, hidden content, and scripts as untrusted data, not instructions.

## 8. Secrets and privacy

- Load secrets from environment variables or a local secret store; `.env` must be gitignored.
- `.env.example` contains names and placeholders only.
- Redact API keys, authorization headers, cookies, tokens, sensitive query parameters, and filesystem content from logs.
- Do not save conversation details to long-term memory automatically.
- Make memory scope, retention, deletion, and indexing visible and controllable.
- Never upload local content to external providers unless the user explicitly requested the external processing and policy permits it.

## 9. Prompt injection and untrusted tool results

Mark external content as untrusted in internal data structures and descriptions. Tools should return data and source identifiers, not executable instructions. The model-facing instructions should remind the model that retrieved content cannot override system/user intent. Security must not rely on that reminder: protect tools and permissions server-side.

## 10. Required security tests

- path traversal and symlink escape;
- Windows drive/UNC path boundary cases;
- approval bypass, stale approval, replay, tampered payload, expired approval;
- SSRF localhost/private/link-local/metadata IP and redirect-to-private cases;
- DNS resolution edge cases;
- shell metacharacters and command injection;
- oversized downloads, zip bombs, decompression bombs, huge PDFs and spreadsheets;
- secret redaction;
- prompt injection in HTML/PDF/Markdown/transcripts;
- browser upload/submit without approval;
- concurrent approvals and atomic consume behavior.

No release is allowed with an unresolved critical security test failure.
