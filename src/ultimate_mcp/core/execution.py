"""Bounded execution manager: timeouts, concurrency semaphores, retry, and audit logging.

Follows ``docs/RELIABILITY_STANDARD.md``:
- Configurable timeouts by category (local, network, subprocess).
- Global and per-provider concurrency semaphores.
- Cancellation propagation (`asyncio.CancelledError` mapped to `CANCELLED` and re-raised
  or returned cleanly).
- Exponential backoff + jitter retry ONLY for idempotent operations when a transient
  ``UltimateMCPError(retryable=True)`` occurs.
- Never retries side-effecting (`L2_WRITE`, `L3_SENSITIVE`) or non-idempotent tools.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ultimate_mcp.core.config import Settings
from ultimate_mcp.core.models import (
    ErrorCode,
    ResultMetadata,
    ToolResult,
    UltimateMCPError,
)
from ultimate_mcp.core.permissions import PermissionLevel, evaluate_permission
from ultimate_mcp.storage.db import open_db

logger = logging.getLogger("ultimate_mcp.execution")


class TimeoutCategory(StrEnum):
    """Standard operation timeout classes."""

    LOCAL = "local"
    NETWORK = "network"
    SUBPROCESS = "subprocess"


@dataclass(frozen=True)
class RetryPolicy:
    """Retry configuration for transient, idempotent operations."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.05
    max_delay_seconds: float = 1.0
    jitter_fraction: float = 0.2


class ExecutionManager:
    """Coordinates bounded, policy-checked, audited execution of tool handlers."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._global_semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._provider_semaphores: dict[str, asyncio.Semaphore] = {}

    def _get_provider_semaphore(self, provider: str | None) -> asyncio.Semaphore | None:
        if not provider:
            return None
        sem = self._provider_semaphores.get(provider)
        if sem is None:
            limit = max(1, min(2, self._settings.max_concurrency))
            sem = asyncio.Semaphore(limit)
            self._provider_semaphores[provider] = sem
        return sem

    def resolve_timeout(
        self,
        category: TimeoutCategory = TimeoutCategory.LOCAL,
        override_seconds: float | None = None,
    ) -> float:
        """Return effective timeout in seconds for a tool execution."""
        if override_seconds is not None and override_seconds > 0:
            return override_seconds
        if category == TimeoutCategory.NETWORK:
            return self._settings.network_timeout_seconds
        if category == TimeoutCategory.SUBPROCESS:
            return self._settings.subprocess_timeout_seconds
        return self._settings.local_timeout_seconds

    async def execute(
        self,
        tool_name: str,
        func: Callable[..., ToolResult | Awaitable[ToolResult]],
        kwargs: dict[str, Any],
        *,
        permission: PermissionLevel = PermissionLevel.L0_READ,
        timeout_category: TimeoutCategory = TimeoutCategory.LOCAL,
        timeout_seconds: float | None = None,
        idempotent: bool = True,
        retry_policy: RetryPolicy | None = None,
        provider: str | None = None,
        has_valid_approval: bool = False,
        request_id: str | None = None,
    ) -> ToolResult:
        """Execute a domain function under policy, concurrency, timeout, and retry guards."""
        req_id = request_id or uuid.uuid4().hex[:12]
        start_perf = time.perf_counter()
        secrets = self._settings.known_secret_values()

        try:
            decision = evaluate_permission(permission, has_valid_approval=has_valid_approval)
        except UltimateMCPError as exc:
            duration_ms = int((time.perf_counter() - start_perf) * 1000)
            res = ToolResult.fail(
                tool_name,
                exc.code,
                exc.message,
                details=exc.details,
                request_id=req_id,
                duration_ms=duration_ms,
                known_secrets=secrets,
            )
            self._record_audit(res)
            return res

        if decision.requires_approval and not decision.allowed_immediately:
            duration_ms = int((time.perf_counter() - start_perf) * 1000)
            res = ToolResult.fail(
                tool_name,
                ErrorCode.APPROVAL_REQUIRED,
                decision.reason,
                retryable=False,
                request_id=req_id,
                duration_ms=duration_ms,
                known_secrets=secrets,
            )
            self._record_audit(res)
            return res

        can_retry = (
            idempotent
            and permission in (PermissionLevel.L0_READ, PermissionLevel.L1_PREPARE)
            and retry_policy is not None
            and retry_policy.max_attempts > 1
        )
        max_attempts = retry_policy.max_attempts if (can_retry and retry_policy) else 1
        effective_timeout = self.resolve_timeout(timeout_category, timeout_seconds)
        provider_sem = self._get_provider_semaphore(provider)

        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._global_semaphore:
                    if provider_sem is not None:
                        async with provider_sem:
                            result = await self._invoke_once(func, kwargs, effective_timeout)
                    else:
                        result = await self._invoke_once(func, kwargs, effective_timeout)

                duration_ms = int((time.perf_counter() - start_perf) * 1000)
                result.metadata = ResultMetadata(
                    tool=tool_name,
                    request_id=req_id,
                    duration_ms=duration_ms,
                    attempts=attempt,
                    truncated=result.metadata.truncated,
                    artifact_path=result.metadata.artifact_path,
                    next_offset=result.metadata.next_offset,
                )
                self._record_audit(result)
                return result

            except asyncio.CancelledError:
                duration_ms = int((time.perf_counter() - start_perf) * 1000)
                res = ToolResult.fail(
                    tool_name,
                    ErrorCode.CANCELLED,
                    "Operation was cancelled.",
                    retryable=False,
                    request_id=req_id,
                    duration_ms=duration_ms,
                    attempts=attempt,
                    known_secrets=secrets,
                )
                self._record_audit(res)
                raise

            except TimeoutError:
                if can_retry and retry_policy and attempt < max_attempts:
                    await self._sleep_backoff(retry_policy, attempt)
                    continue
                duration_ms = int((time.perf_counter() - start_perf) * 1000)
                res = ToolResult.fail(
                    tool_name,
                    ErrorCode.TIMEOUT,
                    f"Operation timed out after {effective_timeout:.2f}s.",
                    retryable=idempotent,
                    request_id=req_id,
                    duration_ms=duration_ms,
                    attempts=attempt,
                    known_secrets=secrets,
                )
                self._record_audit(res)
                return res

            except UltimateMCPError as exc:
                if can_retry and exc.retryable and retry_policy and attempt < max_attempts:
                    await self._sleep_backoff(retry_policy, attempt)
                    continue
                duration_ms = int((time.perf_counter() - start_perf) * 1000)
                res = ToolResult.fail(
                    tool_name,
                    exc.code,
                    exc.message,
                    retryable=exc.retryable,
                    details=exc.details,
                    request_id=req_id,
                    duration_ms=duration_ms,
                    attempts=attempt,
                    known_secrets=secrets,
                )
                self._record_audit(res)
                return res

            except Exception as exc:
                logger.exception(
                    "Unhandled exception in tool %s",
                    tool_name,
                    extra={"request_id": req_id, "tool": tool_name},
                )
                duration_ms = int((time.perf_counter() - start_perf) * 1000)
                res = ToolResult.fail(
                    tool_name,
                    ErrorCode.INTERNAL_ERROR,
                    f"Internal tool error ({type(exc).__name__}).",
                    retryable=False,
                    request_id=req_id,
                    duration_ms=duration_ms,
                    attempts=attempt,
                    known_secrets=secrets,
                )
                self._record_audit(res)
                return res

    @staticmethod
    async def _invoke_once(
        func: Callable[..., ToolResult | Awaitable[ToolResult]],
        kwargs: dict[str, Any],
        timeout_seconds: float,
    ) -> ToolResult:
        if inspect.iscoroutinefunction(func):
            coro_func: Callable[..., Awaitable[ToolResult]] = func
            return await asyncio.wait_for(coro_func(**kwargs), timeout=timeout_seconds)
        sync_func: Callable[..., ToolResult] = func  # type: ignore[assignment]
        return await asyncio.wait_for(
            asyncio.to_thread(sync_func, **kwargs),
            timeout=timeout_seconds,
        )

    @staticmethod
    async def _sleep_backoff(policy: RetryPolicy, attempt: int) -> None:
        raw_delay = min(
            policy.max_delay_seconds,
            policy.base_delay_seconds * (2 ** (attempt - 1)),
        )
        jitter = raw_delay * policy.jitter_fraction * random.random()
        await asyncio.sleep(raw_delay + jitter)

    def _record_audit(self, result: ToolResult) -> None:
        error_code = result.error.code.value if result.error else None
        approval_id = (
            str(result.data.get("approval_id"))
            if isinstance(result.data, dict) and result.data.get("approval_id")
            else None
        )
        logger.info(
            "tool_completed",
            extra={
                "request_id": result.metadata.request_id,
                "tool": result.metadata.tool,
                "status": result.status.value,
                "error_code": error_code,
                "duration_ms": result.metadata.duration_ms,
                "approval_id": approval_id,
            },
        )
        try:
            with open_db(self._settings.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE;")
                conn.execute(
                    """
                    INSERT INTO audit_events (
                        request_id, tool_name, status, error_code,
                        duration_ms, attempts, approval_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        result.metadata.request_id,
                        result.metadata.tool,
                        result.status.value,
                        error_code,
                        result.metadata.duration_ms,
                        result.metadata.attempts,
                        approval_id,
                    ),
                )
                conn.execute("COMMIT;")
        except Exception:
            logger.debug("Audit DB write skipped", exc_info=True)
