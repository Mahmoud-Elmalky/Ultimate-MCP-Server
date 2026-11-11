"""Standardized error taxonomy, result envelopes, and MCP response adaptation.

Follows ``docs/RELIABILITY_STANDARD.md``:
- Status values: ``success``, ``partial``, ``needs_approval``, ``error``.
- Never disguise a failure as ordinary successful text.
- Enforce response byte budgets without silently dropping tail content.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ultimate_mcp.core.redaction import redact_text, redact_value


class ResultStatus(StrEnum):
    """Allowed top-level tool result statuses."""

    SUCCESS = "success"
    PARTIAL = "partial"
    NEEDS_APPROVAL = "needs_approval"
    ERROR = "error"


class ErrorCode(StrEnum):
    """Stable error taxonomy across all domains."""

    INVALID_INPUT = "INVALID_INPUT"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    NOT_FOUND = "NOT_FOUND"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    UPSTREAM_INVALID_RESPONSE = "UPSTREAM_INVALID_RESPONSE"
    OUTPUT_LIMIT_EXCEEDED = "OUTPUT_LIMIT_EXCEEDED"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    PATH_OUTSIDE_WORKSPACE = "PATH_OUTSIDE_WORKSPACE"
    SSRF_BLOCKED = "SSRF_BLOCKED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(BaseModel):
    """Typed, safe error representation."""

    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None


class Provenance(BaseModel):
    """Source and freshness metadata for evidence-bearing items."""

    provider: str
    retrieved_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    source_url: str | None = None
    source_id: str | None = None
    published_at: str | None = None
    freshness_note: str | None = None
    untrusted_content: bool = True


class ResultMetadata(BaseModel):
    """Execution metadata attached to every tool result."""

    tool: str
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    duration_ms: int = 0
    retrieved_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    attempts: int = 1
    truncated: bool = False
    artifact_path: str | None = None
    next_offset: int | None = None


class ToolResult(BaseModel):
    """Standard internal result envelope returned by domain services and execution layer."""

    status: ResultStatus
    summary: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    error: ErrorDetail | None = None
    warnings: list[str] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)
    metadata: ResultMetadata

    @property
    def is_error(self) -> bool:
        """Return True when the result represents a tool failure."""
        return self.status == ResultStatus.ERROR

    @classmethod
    def ok(
        cls,
        tool: str,
        summary: str,
        data: dict[str, Any] | None = None,
        *,
        provenance: list[Provenance] | None = None,
        warnings: list[str] | None = None,
        request_id: str | None = None,
        duration_ms: int = 0,
        status: ResultStatus = ResultStatus.SUCCESS,
    ) -> ToolResult:
        """Construct a successful or partial result."""
        meta = ResultMetadata(
            tool=tool,
            request_id=request_id or uuid.uuid4().hex[:12],
            duration_ms=duration_ms,
        )
        return cls(
            status=status,
            summary=summary,
            data=data or {},
            warnings=warnings or [],
            provenance=provenance or [],
            metadata=meta,
        )

    @classmethod
    def pending_approval(
        cls,
        tool: str,
        approval_id: str,
        summary: str,
        digest: str,
        expires_at: str,
        target: str | None = None,
        *,
        request_id: str | None = None,
        duration_ms: int = 0,
    ) -> ToolResult:
        """Construct a result indicating out-of-band human approval is required."""
        meta = ResultMetadata(
            tool=tool,
            request_id=request_id or uuid.uuid4().hex[:12],
            duration_ms=duration_ms,
        )
        return cls(
            status=ResultStatus.NEEDS_APPROVAL,
            summary=summary,
            data={
                "approval_id": approval_id,
                "digest": digest,
                "expires_at": expires_at,
                "target": target,
                "instructions": (
                    f"Run `ultimate-mcp approvals approve {approval_id}` in a local terminal "
                    "to authorize this operation, then call `execute_approved_action`."
                ),
            },
            error=ErrorDetail(
                code=ErrorCode.APPROVAL_REQUIRED,
                message=summary,
                retryable=False,
                details={"approval_id": approval_id, "digest": digest, "expires_at": expires_at},
            ),
            metadata=meta,
        )

    @classmethod
    def fail(
        cls,
        tool: str,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
        duration_ms: int = 0,
        attempts: int = 1,
        known_secrets: Iterable[str] = (),
    ) -> ToolResult:
        """Construct a safe, redacted error result."""
        safe_msg = redact_text(message, known_secrets)
        safe_details = redact_value(details, known_secrets) if details else None
        meta = ResultMetadata(
            tool=tool,
            request_id=request_id or uuid.uuid4().hex[:12],
            duration_ms=duration_ms,
            attempts=attempts,
        )
        return cls(
            status=ResultStatus.ERROR,
            summary=f"[{code.value}] {safe_msg}",
            error=ErrorDetail(
                code=code,
                message=safe_msg,
                retryable=retryable,
                details=safe_details,
            ),
            metadata=meta,
        )

    def to_mcp_text(
        self,
        max_bytes: int = 24_000,
        output_dir: Path | None = None,
        known_secrets: Iterable[str] = (),
    ) -> str:
        """Serialize the result into concise, budget-bounded JSON text for MCP hosts.

        If the serialized envelope exceeds ``max_bytes``, the full payload is optionally
        saved as a local artifact in ``output_dir`` and a bounded preview with an explicit
        warning and ``artifact_path`` / ``next_offset`` is returned. Tail content is never
        dropped silently.
        """
        payload = redact_value(self.model_dump(mode="json"), known_secrets)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        encoded = serialized.encode("utf-8")
        if len(encoded) <= max_bytes:
            return serialized

        artifact_ref: str | None = None
        if output_dir is not None:
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{self.metadata.tool}_{self.metadata.request_id}.json"
                artifact_file = output_dir / filename
                artifact_file.write_bytes(encoded)
                artifact_ref = str(artifact_file)
            except OSError:
                artifact_ref = None

        overhead_budget = max(512, max_bytes // 2)
        preview_chars = max(128, max_bytes - overhead_budget)
        summary_preview = redact_text(self.summary, known_secrets)[:preview_chars]

        warning_msg = (
            f"Output exceeded budget ({len(encoded)} bytes > {max_bytes} bytes). "
            "Returned bounded preview."
        )
        if artifact_ref:
            warning_msg += f" Full output saved to {artifact_ref}."

        bounded_payload: dict[str, Any] = {
            "status": (
                ResultStatus.PARTIAL.value
                if self.status == ResultStatus.SUCCESS
                else self.status.value
            ),
            "summary": summary_preview,
            "data": {"preview": serialized[:preview_chars]},
            "error": payload.get("error"),
            "warnings": [*payload.get("warnings", []), warning_msg],
            "provenance": payload.get("provenance", []),
            "metadata": {
                **payload.get("metadata", {}),
                "truncated": True,
                "artifact_path": artifact_ref,
                "next_offset": preview_chars,
                "original_bytes": len(encoded),
            },
        }
        return json.dumps(bounded_payload, ensure_ascii=False, indent=2)


class UltimateMCPError(Exception):
    """Domain exception carrying a structured ``ErrorCode`` and retryability flag."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details
