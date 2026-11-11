# Reliability, Result Contracts, and Execution Standard

## 1. Internal result envelope

Use typed internal results. A recommended shape:

```json
{
  "status": "success",
  "data": {},
  "error": null,
  "warnings": [],
  "provenance": [],
  "metadata": {
    "tool": "web_search",
    "request_id": "...",
    "duration_ms": 0,
    "retrieved_at": "..."
  }
}
```

Allowed status values: `success`, `partial`, `needs_approval`, and `error`.

Error fields should include a stable code, concise safe message, retryable boolean, and optional safe details. Do not expose raw stack traces, tokens, local secrets, or full provider exceptions to the model.

## 2. MCP response adaptation

The internal envelope is not a reason to break MCP semantics. The adapter should:
- return concise text content for broad host compatibility;
- include structured content only when the pinned SDK and host support it reliably;
- represent genuine tool failures with MCP's tool error semantics where appropriate;
- never return an error as a normal successful result just to keep the model moving;
- keep the response within configured size limits.

## 3. Error taxonomy

Use stable codes such as:

`INVALID_INPUT`, `AUTH_REQUIRED`, `PERMISSION_DENIED`, `APPROVAL_REQUIRED`, `APPROVAL_EXPIRED`, `APPROVAL_INVALID`, `NOT_FOUND`, `UNSUPPORTED_FORMAT`, `RATE_LIMITED`, `TIMEOUT`, `CANCELLED`, `PROVIDER_UNAVAILABLE`, `UPSTREAM_INVALID_RESPONSE`, `OUTPUT_LIMIT_EXCEEDED`, `RESOURCE_LIMIT_EXCEEDED`, `PATH_OUTSIDE_WORKSPACE`, `SSRF_BLOCKED`, `INTERNAL_ERROR`.

## 4. Timeout policy

Timeouts must be configurable by category and have safe defaults:
- local deterministic operations: short;
- network connect/read/total: separate bounded timeouts;
- browser waits: bounded and condition-based;
- document extraction: bounded by file/page/row limits;
- subprocesses: hard timeout with process-tree termination where possible.

Every long-running operation must have cancellation handling where the SDK/host supports it. If cancellation cannot be guaranteed, make the tool time-bounded and document the limitation.

## 5. Retry policy

- Retry only transient network failures, rate limits (respect Retry-After), and known transient provider errors.
- Use exponential backoff with jitter and a small configured retry cap.
- Never retry unsafe writes, form submissions, deletes, uploads, or commands unless an idempotency key and provider semantics prove replay is safe.
- Return attempt count and final provider status without leaking sensitive details.

## 6. Idempotency

For operations with side effects, use a stable operation ID/idempotency key. Approval payload digest should include the operation type and exact normalized arguments. Mark approvals consumed atomically. Detect duplicate requests where possible.

## 7. Output limits and pagination

- Every tool has a documented maximum response size.
- Prefer concise summaries plus stable references and pagination over giant outputs.
- Provide continuation cursors or artifact IDs for large results.
- Never silently drop tail content. Emit a warning and expose a way to retrieve more.
- Keep provenance with each result item; do not detach citations when truncating.

## 8. Freshness and provenance

For external/current data include:
- provider name;
- fetched/retrieved time;
- publication/event time when applicable;
- source URL or identifier;
- freshness caveat and known coverage limits.

Do not label market quotes “real-time” unless the provider contract and timestamp justify it. Do not infer current state from stale cached results without marking them stale.

## 9. Observability

Use structured logs with request IDs and tool names. Record start/end, duration, provider status, error code, retry count, output byte count, and approval status. Avoid logging secrets or full file content. Provide a local diagnostics command and a safe audit view.
