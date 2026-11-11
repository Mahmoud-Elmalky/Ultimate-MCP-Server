"""Integration tests for the v2 MCP stdio server, profiles, error semantics, and CLI."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ultimate_mcp.app import build_doctor_report, create_gateway
from ultimate_mcp.cli import main as cli_main
from ultimate_mcp.core.config import ProfileName, Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_doctor_and_profiles_without_api_keys(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.setenv("ULTIMATE_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ULTIMATE_MCP_OUTPUT_DIR", str(tmp_path / "out"))

    report = build_doctor_report(profile_override=ProfileName.DEFAULT)
    assert report["profile"] == "default"
    assert report["sqlite_fts5"] is True
    assert set(report["enabled_tools"]) == {
        "calculate",
        "execute_approved_action",
        "get_current_time",
        "prepare_write_file",
        "read_text_file",
    }
    # No model-callable decision tool (`approve_action`, `decide_approval`, etc.) is ever exposed!
    forbidden = {"approve_action", "reject_action", "decide_approval", "approvals_approve"}
    assert forbidden.isdisjoint(set(report["enabled_tools"]))

    browser_report = build_doctor_report(profile_override=ProfileName.BROWSER)
    assert browser_report["enabled_tools"] == ["get_current_time"]

    assert cli_main(["doctor"]) == 0
    assert cli_main(["doctor", "--json"]) == 0
    assert cli_main(["catalog", "--profile", "default"]) == 0
    assert cli_main(["approvals", "list"]) == 0
    assert cli_main(["approvals", "approve", "apr_nonexistent"]) == 1


async def test_v2_stdio_protocol_handshake_and_tool_calls(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    sample = ws / "hello.txt"
    sample.write_text("line1\nline2", encoding="utf-8")

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("TAVILY_API_KEY", "YOUTUBE_API_KEY")
    }
    env.update(
        {
            "ULTIMATE_MCP_STATE_DIR": str(tmp_path / "state"),
            "ULTIMATE_MCP_OUTPUT_DIR": str(tmp_path / "out"),
            "ULTIMATE_MCP_READ_ROOTS": str(ws),
            "ULTIMATE_MCP_WRITE_ROOTS": str(ws),
        }
    )

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ultimate_mcp", "serve", "--profile", "default"],
        env=env,
        cwd=str(REPO_ROOT),
    )

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
        tool_names = {t.name for t in listed.tools}
        assert tool_names == {
            "calculate",
            "execute_approved_action",
            "get_current_time",
            "prepare_write_file",
            "read_text_file",
        }

        # 1. Valid calculation
        calc_res = await session.call_tool("calculate", {"expression": "sqrt(81) + 5"})
        assert calc_res.is_error is False
        calc_json = json.loads(calc_res.content[0].text)
        assert calc_json["status"] == "success"
        assert calc_json["data"]["result"] == 14.0

        # 2. Unsafe expression returns MCP error (is_error=True)
        bad_calc = await session.call_tool("calculate", {"expression": "(1).__class__"})
        assert bad_calc.is_error is True
        bad_json = json.loads(bad_calc.content[0].text)
        assert bad_json["status"] == "error"
        assert bad_json["error"]["code"] == "INVALID_INPUT"

        # 3. Scoped file read
        read_res = await session.call_tool("read_text_file", {"file_path": "hello.txt"})
        assert read_res.is_error is False
        read_json = json.loads(read_res.content[0].text)
        assert read_json["data"]["content"] == "line1\nline2"
        assert read_json["provenance"][0]["provider"] == "local_filesystem"

        # 4. Path traversal blocked with MCP error
        trav_res = await session.call_tool("read_text_file", {"file_path": "../outside.txt"})
        assert trav_res.is_error is True
        trav_json = json.loads(trav_res.content[0].text)
        assert trav_json["error"]["code"] == "PATH_OUTSIDE_WORKSPACE"

        # 5. Prepare write returns needs_approval without creating file
        prep_res = await session.call_tool(
            "prepare_write_file",
            {"file_path": "created.txt", "content": "via mcp", "overwrite": False},
        )
        assert prep_res.is_error is False
        prep_json = json.loads(prep_res.content[0].text)
        assert prep_json["status"] == "needs_approval"
        approval_id = prep_json["data"]["approval_id"]
        assert not (ws / "created.txt").exists()

        # 6. Calling execute_approved_action before CLI approval fails with APPROVAL_REQUIRED
        unapp_res = await session.call_tool(
            "execute_approved_action",
            {"approval_id": approval_id},
        )
        assert unapp_res.is_error is True
        unapp_json = json.loads(unapp_res.content[0].text)
        assert unapp_json["error"]["code"] == "APPROVAL_REQUIRED"


def test_create_gateway_full_profile_emits_no_stdout(tmp_path: Path, capsys) -> None:
    cfg = Settings(
        profile=ProfileName.FULL,
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "out",
    )
    create_gateway(settings=cfg)
    captured = capsys.readouterr()
    assert captured.out == ""
