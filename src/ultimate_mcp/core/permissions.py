"""Permission classification and fail-closed policy evaluation.

Follows ``docs/SECURITY_MODEL.md``:
- L0 Read: constrained read-only operations inside policy-approved roots or public web scope.
- L1 Prepare: analyze, calculate, draft, preview, or create an in-memory/pending record.
- L2 Write: create or modify persistent local artifacts or files (requires approval).
- L3 Sensitive: delete, upload, submit externally, execute commands (requires approval).
- L4 Denied: prohibited by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ultimate_mcp.core.models import ErrorCode, UltimateMCPError


class PermissionLevel(StrEnum):
    """Tool permission classification."""

    L0_READ = "L0_READ"
    L1_PREPARE = "L1_PREPARE"
    L2_WRITE = "L2_WRITE"
    L3_SENSITIVE = "L3_SENSITIVE"
    L4_DENIED = "L4_DENIED"


@dataclass(frozen=True)
class PolicyDecision:
    """Result of evaluating a tool's permission policy."""

    allowed_immediately: bool
    requires_approval: bool
    reason: str


def evaluate_permission(
    level: PermissionLevel | object,
    *,
    has_valid_approval: bool = False,
) -> PolicyDecision:
    """Evaluate a permission level, failing closed on unknown or L4 levels."""
    if not isinstance(level, PermissionLevel):
        raise UltimateMCPError(
            ErrorCode.PERMISSION_DENIED,
            f"Unknown permission level '{level}'. Policy fails closed.",
        )

    if level == PermissionLevel.L4_DENIED:
        raise UltimateMCPError(
            ErrorCode.PERMISSION_DENIED,
            "Operation is classified as L4_DENIED and is prohibited by security policy.",
        )

    if level in (PermissionLevel.L0_READ, PermissionLevel.L1_PREPARE):
        return PolicyDecision(
            allowed_immediately=True,
            requires_approval=False,
            reason=f"{level.value} permitted within configured boundaries.",
        )

    if level in (PermissionLevel.L2_WRITE, PermissionLevel.L3_SENSITIVE):
        if has_valid_approval:
            return PolicyDecision(
                allowed_immediately=True,
                requires_approval=True,
                reason=f"{level.value} authorized via verified out-of-band approval.",
            )
        return PolicyDecision(
            allowed_immediately=False,
            requires_approval=True,
            reason=f"{level.value} requires out-of-band human approval before execution.",
        )

    raise UltimateMCPError(
        ErrorCode.PERMISSION_DENIED,
        f"Unhandled permission level '{level}'. Policy fails closed.",
    )
