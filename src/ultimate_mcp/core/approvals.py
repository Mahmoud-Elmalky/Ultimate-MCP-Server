"""Out-of-band human approval broker backed by SQLite (ADR-0002, SECURITY_MODEL.md §3).

Invariants:
1. ``prepare_action`` validates and stores an immutable pending request with a SHA-256
   digest over the canonicalized ``(action_type, payload)``. No side effect is performed.
2. Approval or rejection can ONLY be performed out-of-band via the local CLI
   (``ultimate-mcp approvals approve <id>``). No MCP tool is registered to approve requests.
3. ``consume_and_execute`` atomically transitions ``APPROVED -> CONSUMED`` inside an
   ``IMMEDIATE`` SQLite transaction after verifying status, TTL expiry, exact canonical
   digest, target file state (if tracked), and current policy.
4. Any replay attempt, expired record, rejected record, or payload tampering raises a
   typed ``UltimateMCPError``.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from ultimate_mcp.core.models import ErrorCode, UltimateMCPError
from ultimate_mcp.core.permissions import PermissionLevel, evaluate_permission
from ultimate_mcp.core.redaction import redact_text, redact_value
from ultimate_mcp.storage.db import open_db

POLICY_VERSION = "v2.0-phase1"


class ApprovalStatus(StrEnum):
    """Lifecycle states of a prepared side-effecting operation."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


@dataclass(frozen=True)
class ApprovalRecord:
    """Stored approval record."""

    approval_id: str
    action_type: str
    permission_level: PermissionLevel
    payload: dict[str, Any]
    payload_digest: str
    summary: str
    target_path: str | None
    target_pre_hash: str | None
    requester_id: str
    policy_version: str
    status: ApprovalStatus
    created_at: float
    expires_at: float
    decided_at: float | None
    decided_by: str | None
    decision_reason: str | None
    consumed_at: float | None
    result_ref: str | None

    @property
    def expires_at_iso(self) -> str:
        return datetime.fromtimestamp(self.expires_at, tz=UTC).isoformat()

    @property
    def created_at_iso(self) -> str:
        return datetime.fromtimestamp(self.created_at, tz=UTC).isoformat()


def compute_canonical_digest(action_type: str, payload: dict[str, Any]) -> str:
    """Return a deterministic SHA-256 hex digest of ``action_type`` and ``payload``."""
    canonical = json.dumps(
        {"action_type": action_type, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_file_state_hash(path: Path | None) -> str | None:
    """Return a state fingerprint for a target file (``MISSING`` or ``sha256:<hex>``)."""
    if path is None:
        return None
    resolved = path.resolve(strict=False)
    if not resolved.exists():
        return "MISSING"
    if resolved.is_dir():
        return "DIR"
    hasher = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return f"sha256:{hasher.hexdigest()}"


class ApprovalBroker:
    """SQLite-backed broker for preparing, approving, rejecting, and consuming actions."""

    def __init__(
        self,
        db_path: Path,
        *,
        default_ttl_seconds: int = 600,
        known_secrets: Iterable[str] = (),
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db_path = db_path
        self._default_ttl = default_ttl_seconds
        self._secrets = tuple(known_secrets)
        self._clock = clock

    def prepare_action(
        self,
        action_type: str,
        permission_level: PermissionLevel,
        payload: dict[str, Any],
        summary: str,
        *,
        target_path: Path | None = None,
        requester_id: str = "mcp-client",
        ttl_seconds: int | None = None,
    ) -> ApprovalRecord:
        """Validate policy and persist a new ``PENDING`` approval record without side effects."""
        evaluate_permission(permission_level, has_valid_approval=False)
        sanitized_payload = redact_value(payload, self._secrets)
        if not isinstance(sanitized_payload, dict):
            raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Approval payload must be a dict.")

        digest = compute_canonical_digest(action_type, sanitized_payload)
        safe_summary = redact_text(summary, self._secrets)
        now = self._clock()
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        if ttl <= 0:
            raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Approval TTL must be positive.")
        expires_at = now + float(ttl)

        approval_id = f"apr_{uuid.uuid4().hex[:16]}"
        target_str = str(target_path.resolve(strict=False)) if target_path else None
        pre_hash = compute_file_state_hash(target_path)

        with open_db(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                INSERT INTO approvals (
                    approval_id, action_type, permission_level, payload_json,
                    payload_digest, summary, target_path, target_Pre_hash,
                    requester_id, policy_version, status, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    approval_id,
                    action_type,
                    permission_level.value,
                    json.dumps(sanitized_payload, sort_keys=True),
                    digest,
                    safe_summary,
                    target_str,
                    pre_hash,
                    requester_id,
                    POLICY_VERSION,
                    ApprovalStatus.PENDING.value,
                    now,
                    expires_at,
                ),
            )
            conn.execute("COMMIT;")

        return self.get_record(approval_id)

    def get_record(self, approval_id: str) -> ApprovalRecord:
        """Fetch an approval record by ID, marking it ``EXPIRED`` if past its TTL."""
        now = self._clock()
        with open_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM approvals WHERE approval_id = ?;",
                (approval_id,),
            ).fetchone()
            if row is None:
                raise UltimateMCPError(
                    ErrorCode.NOT_FOUND,
                    f"Approval ID '{approval_id}' does not exist.",
                )
            status = ApprovalStatus(row["status"])
            if status in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED) and now > float(
                row["expires_at"]
            ):
                conn.execute("BEGIN IMMEDIATE;")
                conn.execute(
                    "UPDATE approvals SET status = ? WHERE approval_id = ?;",
                    (ApprovalStatus.EXPIRED.value, approval_id),
                )
                conn.execute("COMMIT;")
                row = conn.execute(
                    "SELECT * FROM approvals WHERE approval_id = ?;",
                    (approval_id,),
                ).fetchone()
        return self._row_to_record(row)

    def list_records(self, status: ApprovalStatus | None = None) -> list[ApprovalRecord]:
        """List approval records (auto-expiring stale records first)."""
        now = self._clock()
        with open_db(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                UPDATE approvals
                   SET status = ?
                 WHERE status IN (?, ?) AND expires_at < ?;
                """,
                (
                    ApprovalStatus.EXPIRED.value,
                    ApprovalStatus.PENDING.value,
                    ApprovalStatus.APPROVED.value,
                    now,
                ),
            )
            conn.execute("COMMIT;")
            if status is not None:
                rows = conn.execute(
                    "SELECT * FROM approvals WHERE status = ? ORDER BY created_at DESC;",
                    (status.value,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM approvals ORDER BY created_at DESC;"
                ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def decide_out_of_band(
        self,
        approval_id: str,
        *,
        approve: bool,
        decided_by: str = "local-cli-user",
        reason: str | None = None,
    ) -> ApprovalRecord:
        """Approve or reject a ``PENDING`` record from the local human CLI."""
        now = self._clock()
        with open_db(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            row = conn.execute(
                "SELECT * FROM approvals WHERE approval_id = ?;",
                (approval_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK;")
                raise UltimateMCPError(
                    ErrorCode.NOT_FOUND,
                    f"Approval ID '{approval_id}' does not exist.",
                )

            current_status = ApprovalStatus(row["status"])
            if now > float(row["expires_at"]) and current_status == ApprovalStatus.PENDING:
                conn.execute(
                    "UPDATE approvals SET status = ? WHERE approval_id = ?;",
                    (ApprovalStatus.EXPIRED.value, approval_id),
                )
                conn.execute("COMMIT;")
                raise UltimateMCPError(
                    ErrorCode.APPROVAL_EXPIRED,
                    f"Approval '{approval_id}' expired before a decision was recorded.",
                )

            if current_status != ApprovalStatus.PENDING:
                conn.execute("ROLLBACK;")
                raise UltimateMCPError(
                    ErrorCode.APPROVAL_INVALID,
                    f"Approval '{approval_id}' cannot be decided because its status is "
                    f"'{current_status.value}' (expected 'pending').",
                )

            new_status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
            conn.execute(
                """
                UPDATE approvals
                   SET status = ?, decided_at = ?, decided_by = ?, decision_reason = ?
                 WHERE approval_id = ? AND status = ?;
                """,
                (
                    new_status.value,
                    now,
                    decided_by,
                    redact_text(reason or "", self._secrets) if reason else None,
                    approval_id,
                    ApprovalStatus.PENDING.value,
                ),
            )
            conn.execute("COMMIT;")

        return self.get_record(approval_id)

    def consume_approved(
        self,
        approval_id: str,
        expected_action_type: str | None = None,
        expected_payload: dict[str, Any] | None = None,
    ) -> ApprovalRecord:
        """Atomically verify and consume an ``APPROVED`` record once.

        Rechecks:
        - Record existence
        - TTL expiry
        - Status is ``APPROVED`` (not ``PENDING``, ``REJECTED``, or already ``CONSUMED``)
        - Policy version and permission evaluation
        - Exact canonical digest (both against stored payload and caller-supplied payload)
        - Target file state hash matches the state captured at prepare time
        """
        now = self._clock()
        with open_db(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            row = conn.execute(
                "SELECT * FROM approvals WHERE approval_id = ?;",
                (approval_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK;")
                raise UltimateMCPError(
                    ErrorCode.APPROVAL_INVALID,
                    f"Approval ID '{approval_id}' does not exist.",
                )

            record = self._row_to_record(row)
            if now > record.expires_at:
                conn.execute(
                    "UPDATE approvals SET status = ? WHERE approval_id = ?;",
                    (ApprovalStatus.EXPIRED.value, approval_id),
                )
                conn.execute("COMMIT;")
                raise UltimateMCPError(
                    ErrorCode.APPROVAL_EXPIRED,
                    f"Approval '{approval_id}' has expired.",
                )

            if record.status == ApprovalStatus.CONSUMED:
                conn.execute("ROLLBACK;")
                raise UltimateMCPError(
                    ErrorCode.APPROVAL_INVALID,
                    f"Approval '{approval_id}' has already been consumed (one-time use only).",
                )

            if record.status == ApprovalStatus.PENDING:
                conn.execute("ROLLBACK;")
                raise UltimateMCPError(
                    ErrorCode.APPROVAL_REQUIRED,
                    f"Approval '{approval_id}' is still pending human authorization.",
                )

            if record.status != ApprovalStatus.APPROVED:
                conn.execute("ROLLBACK;")
                raise UltimateMCPError(
                    ErrorCode.APPROVAL_INVALID,
                    f"Approval '{approval_id}' is in status '{record.status.value}' and "
                    "cannot be executed.",
                )

            evaluate_permission(record.permission_level, has_valid_approval=True)

            recomputed_digest = compute_canonical_digest(record.action_type, record.payload)
            if recomputed_digest != record.payload_digest:
                conn.execute("ROLLBACK;")
                raise UltimateMCPError(
                    ErrorCode.APPROVAL_INVALID,
                    "Stored approval payload digest mismatch (tampered record).",
                )

            if expected_action_type is not None and expected_action_type != record.action_type:
                conn.execute("ROLLBACK;")
                raise UltimateMCPError(
                    ErrorCode.APPROVAL_INVALID,
                    f"Approval '{approval_id}' is bound to action '{record.action_type}', "
                    f"not '{expected_action_type}'.",
                )

            if expected_payload is not None:
                sanitized_expected = redact_value(expected_payload, self._secrets)
                caller_digest = compute_canonical_digest(record.action_type, sanitized_expected)
                if caller_digest != record.payload_digest:
                    conn.execute("ROLLBACK;")
                    raise UltimateMCPError(
                        ErrorCode.APPROVAL_INVALID,
                        "Requested arguments do not match the approved payload digest.",
                    )

            if record.target_path is not None and record.target_pre_hash is not None:
                current_hash = compute_file_state_hash(Path(record.target_path))
                if current_hash != record.target_pre_hash:
                    conn.execute("ROLLBACK;")
                    raise UltimateMCPError(
                        ErrorCode.APPROVAL_INVALID,
                        "Target file changed after approval was prepared; prepare a new approval.",
                    )

            cursor = conn.execute(
                """
                UPDATE approvals
                   SET status = ?, consumed_at = ?
                 WHERE approval_id = ? AND status = ?;
                """,
                (
                    ApprovalStatus.CONSUMED.value,
                    now,
                    approval_id,
                    ApprovalStatus.APPROVED.value,
                ),
            )
            if cursor.rowcount != 1:
                conn.execute("ROLLBACK;")
                raise UltimateMCPError(
                    ErrorCode.APPROVAL_INVALID,
                    f"Concurrent consumption detected for approval '{approval_id}'.",
                )
            conn.execute("COMMIT;")

        return self.get_record(approval_id)

    @staticmethod
    def _row_to_record(row: Any) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=str(row["approval_id"]),
            action_type=str(row["action_type"]),
            permission_level=PermissionLevel(str(row["permission_level"])),
            payload=json.loads(str(row["payload_json"])),
            payload_digest=str(row["payload_digest"]),
            summary=str(row["summary"]),
            target_path=row["target_path"],
            target_pre_hash=row["target_Pre_hash"],
            requester_id=str(row["requester_id"]),
            policy_version=str(row["policy_version"]),
            status=ApprovalStatus(str(row["status"])),
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
            decided_at=float(row["decided_at"]) if row["decided_at"] is not None else None,
            decided_by=row["decided_by"],
            decision_reason=row["decision_reason"],
            consumed_at=float(row["consumed_at"]) if row["consumed_at"] is not None else None,
            result_ref=row["result_ref"],
        )
