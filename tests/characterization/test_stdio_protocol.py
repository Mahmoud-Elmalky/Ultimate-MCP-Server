"""Characterization: real stdio MCP handshake against the legacy server process.

Spawns ``ultimate_mcp_server.py`` with the official SDK stdio client, verifying that the
protocol stream is not corrupted by stray stdout output and that an offline tool call
works end to end. No network access is required.
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .conftest import REPO_ROOT

HANDSHAKE_TIMEOUT_SECONDS = 120


async def _exercise_server() -> tuple[list[str], str, bool]:
    env = {**os.environ, "TAVILY_API_KEY": "dummy-offline-key"}
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(REPO_ROOT / "ultimate_mcp_server.py")],
        env=env,
        cwd=str(REPO_ROOT),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
        result = await session.call_tool("calculate_or_time", {"action": "math", "value": "6*7"})
        text = result.content[0].text
        bad = await session.call_tool("calculate_or_time", {"action": "bogus"})
        return [t.name for t in listed.tools], text, bool(bad.isError)


def test_stdio_handshake_and_offline_call() -> None:
    names, text, invalid_enum_is_error = asyncio.run(
        asyncio.wait_for(_exercise_server(), HANDSHAKE_TIMEOUT_SECONDS)
    )
    assert len(names) == 8
    assert text == "Result: 42"
    assert invalid_enum_is_error is True
