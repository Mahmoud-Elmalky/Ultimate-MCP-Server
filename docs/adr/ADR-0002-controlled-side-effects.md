# ADR-0002: Controlled Side Effects

- Status: Accepted
- Decision: Read-only and prepare/preview operations may run automatically within configured scope. Persistent writes, deletes, uploads, external submissions, and arbitrary command execution require out-of-band approval by default.
- Context: The local model is not a trusted authorization authority, and LM Studio host approval UI behavior must not be assumed.
- Consequences: Implement a local approval broker/CLI, exact payload digest, expiry, one-time atomic consumption, and revalidation. When approval is unavailable, deny the action.
