# Security Review Prompt — Ultimate MCP Server

Act as an independent application-security reviewer. Do not rely on implementation comments or tool descriptions as proof of safety.

Review the current diff and relevant surrounding code for:
- path traversal, symlink/UNC/drive escape, and workspace boundary failures;
- SSRF, DNS rebinding, redirect-to-private-address, unsafe URL parsing, and oversized responses;
- shell/command injection, unsafe subprocess usage, arbitrary code execution, and false sandbox claims;
- approval bypass, self-approval, stale/replayed/tampered approvals, TOCTOU, and non-atomic execution;
- unsafe writes/deletes/uploads/form submissions;
- prompt injection and treating external content as trusted instructions;
- secret exposure in logs, errors, docs, tests, state, and Git;
- resource exhaustion, decompression bombs, unbounded parsing, concurrency exhaustion;
- SQL injection, archive extraction paths, unsafe deserialization, and dependency risks;
- browser profile leakage and local network access.

For every finding provide severity, exact file/line, exploit preconditions, impact, reproducible test, and concrete remediation. Do not report speculative concerns as confirmed vulnerabilities. Run or add tests where feasible. A critical/high issue blocks phase completion until fixed or explicitly disabled.
