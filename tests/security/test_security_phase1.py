"""Security tests: AST calculator, workspace path containment, and out-of-band approvals."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ultimate_mcp.cli import main as cli_main
from ultimate_mcp.core.approvals import ApprovalBroker, ApprovalStatus
from ultimate_mcp.core.config import Settings
from ultimate_mcp.core.filesystem import atomic_write_text, read_text_bounded, resolve_scoped_path
from ultimate_mcp.core.models import ErrorCode, ResultStatus, UltimateMCPError
from ultimate_mcp.core.permissions import PermissionLevel, evaluate_permission
from ultimate_mcp.modules.documents.reader import read_workspace_text_file_service
from ultimate_mcp.modules.system.actions import (
    execute_approved_action_service,
    prepare_write_file_service,
)
from ultimate_mcp.modules.utilities.calculator import evaluate_math_expression
from ultimate_mcp.modules.utilities.clock import get_current_time_service


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("2 + 3 * 4", 14),
        ("(10 - 2) / 4", 2.0),
        ("17 // 5", 3),
        ("17 % 5", 2),
        ("2 ** 10", 1024),
        ("sqrt(144) + cbrt(27)", 15.0),
        ("round(pi, 4)", 3.1416),
        ("gcd(48, 18)", 6),
        ("factorial(5)", 120),
        ("-5 + +3", -2),
    ],
)
def test_calculator_should_evaluate_safe_expressions(expr: str, expected: int | float) -> None:
    assert evaluate_math_expression(expr) == expected


@pytest.mark.parametrize(
    "malicious_expr",
    [
        "(1).__class__.__name__",
        "__import__('os').system('echo pwn')",
        "open('secret.txt').read()",
        "lambda x: x",
        "[x for x in range(10)]",
        "{'a': 1}['a']",
        "'hello'",
        "True + 1",
        "unknown_var + 1",
        "sin(x=1)",
        "eval('1+1')",
        "9 ** 9 ** 9",
        "2 ** 5000",
        "factorial(500)",
        "factorial(-1)",
        "1 / 0",
        "1 // 0",
        "1 % 0",
        "1 + " * 200 + "1",
    ],
)
def test_calculator_should_reject_unsafe_or_dos_expressions(malicious_expr: str) -> None:
    with pytest.raises(UltimateMCPError) as excinfo:
        evaluate_math_expression(malicious_expr)
    assert excinfo.value.code in (
        ErrorCode.INVALID_INPUT,
        ErrorCode.RESOURCE_LIMIT_EXCEEDED,
    )


def test_clock_should_validate_iana_timezones() -> None:
    res = get_current_time_service("UTC")
    assert res.status == ResultStatus.SUCCESS
    assert res.data["timezone"] == "UTC"

    with pytest.raises(UltimateMCPError) as excinfo:
        get_current_time_service("Mars/Phobos")
    assert excinfo.value.code == ErrorCode.INVALID_INPUT


def test_filesystem_should_block_traversal_and_outside_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_secret = tmp_path / "secret.txt"
    outside_secret.write_text("top-secret", encoding="utf-8")

    inside_file = workspace / "notes.txt"
    inside_file.write_text("hello world", encoding="utf-8")

    resolved = resolve_scoped_path(inside_file, [workspace], must_exist=True)
    assert resolved == inside_file.resolve()

    with pytest.raises(UltimateMCPError) as excinfo:
        resolve_scoped_path(workspace / ".." / "secret.txt", [workspace])
    assert excinfo.value.code == ErrorCode.PATH_OUTSIDE_WORKSPACE

    with pytest.raises(UltimateMCPError) as excinfo:
        resolve_scoped_path(outside_secret, [workspace])
    assert excinfo.value.code == ErrorCode.PATH_OUTSIDE_WORKSPACE


@pytest.mark.parametrize(
    "bad_path",
    [
        r"\\evil-server\share\file.txt",
        "//evil-server/share/file.txt",
        "CON",
        "NUL.txt",
        "bad\x00path.txt",
    ],
)
def test_filesystem_should_reject_unc_device_and_nul_paths(
    tmp_path: Path, bad_path: str
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(UltimateMCPError) as excinfo:
        resolve_scoped_path(bad_path, [workspace])
    assert excinfo.value.code in (
        ErrorCode.PATH_OUTSIDE_WORKSPACE,
        ErrorCode.INVALID_INPUT,
    )


def test_filesystem_should_block_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = workspace / "escape_link.txt"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("Symlink creation requires elevated privilege on this Windows configuration")

    with pytest.raises(UltimateMCPError) as excinfo:
        resolve_scoped_path(link, [workspace])
    assert excinfo.value.code == ErrorCode.PATH_OUTSIDE_WORKSPACE


def test_read_text_bounded_should_paginate_and_atomic_write(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = atomic_write_text(workspace / "doc.txt", "0123456789abcdef", [workspace])
    page1 = read_text_bounded(target, [workspace], offset=0, max_chars=6)
    assert page1.content == "012345"
    assert page1.truncated is True
    assert page1.next_offset == 6

    page2 = read_text_bounded(target, [workspace], offset=6, max_chars=20)
    assert page2.content == "6789abcdef"
    assert page2.truncated is False
    assert page2.next_offset is None

    cfg = Settings(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "out",
        read_roots=[workspace],
    )
    svc_res = read_workspace_text_file_service(cfg, file_path="doc.txt", offset=0, max_chars=5)
    assert svc_res.status == ResultStatus.PARTIAL
    assert svc_res.data["next_offset"] == 5
    assert svc_res.provenance[0].provider == "local_filesystem"


def test_permission_policy_should_fail_closed() -> None:
    with pytest.raises(UltimateMCPError) as excinfo:
        evaluate_permission(PermissionLevel.L4_DENIED)
    assert excinfo.value.code == ErrorCode.PERMISSION_DENIED

    with pytest.raises(UltimateMCPError) as excinfo:
        evaluate_permission("UNKNOWN_LEVEL")
    assert excinfo.value.code == ErrorCode.PERMISSION_DENIED


def test_approval_lifecycle_and_security_invariants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = Settings(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "out",
        write_roots=[ws],
    )
    cfg.ensure_directories()

    now_box = [10_000_000_000.0]
    broker = ApprovalBroker(cfg.db_path, default_ttl_seconds=120, clock=lambda: now_box[0])

    target = ws / "output.txt"
    prep_res = prepare_write_file_service(
        cfg,
        broker,
        file_path=str(target),
        content="approved content",
        overwrite=False,
    )
    assert prep_res.status == ResultStatus.NEEDS_APPROVAL
    assert not target.exists()
    approval_id = prep_res.data["approval_id"]

    # 1. Cannot execute while still PENDING
    with pytest.raises(UltimateMCPError) as excinfo:
        execute_approved_action_service(cfg, broker, approval_id=approval_id)
    assert excinfo.value.code == ErrorCode.APPROVAL_REQUIRED
    assert not target.exists()

    # 2. Approve out-of-band via CLI using same state dir
    monkeypatch.setenv("ULTIMATE_MCP_STATE_DIR", str(cfg.state_dir))
    monkeypatch.setenv("ULTIMATE_MCP_OUTPUT_DIR", str(cfg.output_dir))
    exit_code = cli_main(["approvals", "approve", approval_id, "--reason", "verified by user"])
    assert exit_code == 0

    # 3. Tampered payload / wrong action type is rejected before consumption
    with pytest.raises(UltimateMCPError) as excinfo:
        broker.consume_approved(
            approval_id,
            expected_action_type="write_text_file",
            expected_payload={
                "file_path": str(target),
                "content": "TAMPERED content",
                "overwrite": False,
            },
        )
    assert excinfo.value.code == ErrorCode.APPROVAL_INVALID
    assert broker.get_record(approval_id).status == ApprovalStatus.APPROVED

    # 4. Execute approved action succeeds once
    exec_res = execute_approved_action_service(cfg, broker, approval_id=approval_id)
    assert exec_res.status == ResultStatus.SUCCESS
    assert target.read_text(encoding="utf-8") == "approved content"
    assert broker.get_record(approval_id).status == ApprovalStatus.CONSUMED

    # 5. Replay attack is rejected
    with pytest.raises(UltimateMCPError) as excinfo:
        execute_approved_action_service(cfg, broker, approval_id=approval_id)
    assert excinfo.value.code == ErrorCode.APPROVAL_INVALID

    # 6. Expired approval is rejected
    prep2 = prepare_write_file_service(
        cfg,
        broker,
        file_path=str(ws / "second.txt"),
        content="hello",
        overwrite=False,
    )
    apr2 = prep2.data["approval_id"]
    broker.decide_out_of_band(apr2, approve=True)
    now_box[0] += 500.0
    with pytest.raises(UltimateMCPError) as excinfo:
        execute_approved_action_service(cfg, broker, approval_id=apr2)
    assert excinfo.value.code == ErrorCode.APPROVAL_EXPIRED

    # 7. Target file modified after prepare invalidates approval
    changing = ws / "changing.txt"
    changing.write_text("v1", encoding="utf-8")
    prep3 = prepare_write_file_service(
        cfg,
        broker,
        file_path=str(changing),
        content="v2",
        overwrite=True,
    )
    apr3 = prep3.data["approval_id"]
    broker.decide_out_of_band(apr3, approve=True)
    changing.write_text("concurrent external modification", encoding="utf-8")
    with pytest.raises(UltimateMCPError) as excinfo:
        execute_approved_action_service(cfg, broker, approval_id=apr3)
    assert excinfo.value.code == ErrorCode.APPROVAL_INVALID
