"""Unit tests for the ExecutionManager (timeouts, retries, concurrency, non-idempotent guard)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ultimate_mcp.core.config import Settings
from ultimate_mcp.core.execution import ExecutionManager, RetryPolicy, TimeoutCategory
from ultimate_mcp.core.models import ErrorCode, ResultStatus, ToolResult, UltimateMCPError
from ultimate_mcp.core.permissions import PermissionLevel


@pytest.fixture
def exec_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ExecutionManager:
    monkeypatch.chdir(tmp_path)
    cfg = Settings(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "out",
        local_timeout_seconds=0.25,
        max_concurrency=2,
    )
    cfg.ensure_directories()
    return ExecutionManager(cfg)


async def test_should_timeout_slow_operation(exec_manager: ExecutionManager) -> None:
    async def slow_op() -> ToolResult:
        await asyncio.sleep(2.0)
        return ToolResult.ok("slow", "done")

    res = await exec_manager.execute(
        "slow",
        slow_op,
        {},
        timeout_category=TimeoutCategory.LOCAL,
        timeout_seconds=0.15,
    )
    assert res.status == ResultStatus.ERROR
    assert res.error is not None
    assert res.error.code == ErrorCode.TIMEOUT


async def test_should_retry_transient_idempotent_read(exec_manager: ExecutionManager) -> None:
    calls = 0

    async def flaky() -> ToolResult:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise UltimateMCPError(
                ErrorCode.RATE_LIMITED,
                "429 rate limit",
                retryable=True,
            )
        return ToolResult.ok("flaky", "recovered")

    res = await exec_manager.execute(
        "flaky",
        flaky,
        {},
        permission=PermissionLevel.L0_READ,
        idempotent=True,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.01, max_delay_seconds=0.05),
    )
    assert res.status == ResultStatus.SUCCESS
    assert calls == 3
    assert res.metadata.attempts == 3


async def test_should_never_retry_write_even_if_retry_policy_passed(
    exec_manager: ExecutionManager,
) -> None:
    calls = 0

    async def write_op() -> ToolResult:
        nonlocal calls
        calls += 1
        raise UltimateMCPError(ErrorCode.PROVIDER_UNAVAILABLE, "transient", retryable=True)

    res = await exec_manager.execute(
        "write_op",
        write_op,
        {},
        permission=PermissionLevel.L2_WRITE,
        idempotent=True,
        has_valid_approval=True,
        retry_policy=RetryPolicy(max_attempts=4, base_delay_seconds=0.01),
    )
    assert res.status == ResultStatus.ERROR
    assert calls == 1
    assert res.metadata.attempts == 1


async def test_should_block_unapproved_l2_write(exec_manager: ExecutionManager) -> None:
    called = False

    def write_op() -> ToolResult:
        nonlocal called
        called = True
        return ToolResult.ok("write_op", "wrote")

    res = await exec_manager.execute(
        "write_op",
        write_op,
        {},
        permission=PermissionLevel.L2_WRITE,
        has_valid_approval=False,
    )
    assert called is False
    assert res.status == ResultStatus.ERROR
    assert res.error is not None
    assert res.error.code == ErrorCode.APPROVAL_REQUIRED


async def test_should_map_unexpected_exception_to_safe_internal_error(
    exec_manager: ExecutionManager,
) -> None:
    def buggy() -> ToolResult:
        raise RuntimeError("sensitive internal detail tvly-1234567890abcdef")

    res = await exec_manager.execute("buggy", buggy, {})
    assert res.status == ResultStatus.ERROR
    assert res.error is not None
    assert res.error.code == ErrorCode.INTERNAL_ERROR
    assert "tvly-1234567890abcdef" not in res.error.message
