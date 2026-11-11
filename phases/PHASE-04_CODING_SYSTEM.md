# Phase 04 — Coding Intelligence and System Operations

## Objective

Give local models safe, project-aware code inspection, Git visibility, testing/quality workflows, and constrained system diagnostics. Code edits and command execution remain controlled.

## Prerequisites

Phase 01 workspace permissions/approval/execution; Phase 03 filesystem/document primitives where applicable.

## A. Project workspace model

1. Define named workspaces with canonical root paths and read/write policy.
2. Keep project discovery bounded by file count, file size, depth, ignored folders, and symlink policy.
3. Respect `.gitignore` where safe, but do not treat it as a security boundary.
4. Exclude `.env`, keys, caches, model weights, build output, and binaries from default content search.

## B. Code intelligence

1. Add `project_scan` to identify languages/frameworks/manifests without executing project code.
2. Add `list_project_files` with limits and ignore rules.
3. Add `search_codebase` using `rg` if installed, with safe Python fallback or clear diagnostic.
4. Add `read_code_file` with line ranges and size limits.
5. Add Tree-sitter as optional grammar-specific parsing; unsupported languages fall back to text search.
6. Implement `read_symbol`, `find_references`, and dependency summary only when parser coverage supports it.
7. Do not claim semantic understanding beyond the parser/search evidence.

## C. Git integration

1. Use Git CLI with argument arrays and fixed workspace CWD.
2. Read-only tools: status, diff, log, branches, changed files.
3. Branch creation/checkout/commit/push are separate side-effect operations with policy and approval.
4. Never auto-commit or push.
5. Handle untracked files and binary diff safely; redact secrets from displayed diffs where possible.

## D. Tests and quality tools

1. Implement configurable test runner with allowlisted executable and argument templates.
2. Add wrappers for pytest, Ruff, mypy/pyright where installed, and language-specific tools only when configured.
3. Do not `pip install` or modify the project's environment automatically.
4. Capture bounded stdout/stderr and exit status; expose a saved artifact for long logs.
5. Validate command, CWD, environment, timeout, and process tree before run.
6. Running user project code is a code-execution risk; require explicit policy/approval unless an audited sandbox exists.

## E. Patch workflow

1. `propose_patch` returns unified diff and affected files without writing.
2. Validate patch paths stay in write roots and do not touch protected paths.
3. Present file list, diff summary, and digest.
4. Obtain out-of-band approval.
5. Apply exact patch atomically where feasible; reject if the base files changed after approval.
6. Run configured tests only after policy allows; report failures honestly.
7. Never overwrite user changes or auto-resolve conflicts destructively.

## F. System diagnostics

1. Add `system_info`, `resource_monitor`, `process_list`, `process_inspect`, `package_inspect`, and `log_analyze`.
2. Use `psutil` as optional dependency and `nvidia-smi` only when available.
3. Redact command-line arguments/environment values that may contain secrets.
4. Do not expose unrelated user process content or credentials.
5. Add safe file copy/move/delete preview workflows; deletion is L3 and disabled by default.

## G. Tests

- Workspace root and symlink escape tests, ignored/secrets files tests.
- Git repo fixture with modified/untracked files; confirm no mutation from read tools.
- Shell metacharacters and executable allowlist tests.
- Timeout/process-tree and output-cap tests.
- Patch digest/base-file-changed/replay/approval tests.
- Process metadata redaction tests.


## Recommended tooling and optional dependencies

- Git CLI for repository operations.
- `ripgrep` (`rg`) for fast text search; provide a clear fallback or diagnostic if absent.
- Tree-sitter Python bindings and grammars as optional language-aware parsers.
- `pytest`, Ruff, mypy/Pyright as user-project tools only when installed/configured.
- `psutil` optional for process/resource reporting; `nvidia-smi` optional for NVIDIA telemetry.
- OS-level isolation (container/Windows sandbox/Job Objects as applicable) must be verified before advertising code execution as sandboxed.

## Acceptance criteria

- The agent can inspect a codebase without running its code.
- Git read tools do not mutate the repository.
- Patches are proposed before application and applied only after explicit approval.
- No shell command is interpreted by a shell.
- Arbitrary execution is disabled unless a real tested sandbox exists.
- System monitoring is optional and redacted.
