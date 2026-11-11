"""Characterization: import-time behavior and the advertised MCP tool surface."""

from __future__ import annotations

import asyncio
import json
from types import ModuleType

import pytest

EXPECTED_TOOLS = {
    "web_search",
    "limited_web_search",
    "get_market_price",
    "get_weather",
    "read_article",
    "youtube_expert",
    "calculate_or_time",
    "read_local_file",
}


def _list_tools(module: ModuleType) -> list:
    return asyncio.run(module.mcp.list_tools())


def test_should_fail_import_when_tavily_key_missing(isolated_env, import_legacy) -> None:
    """Legacy server eagerly builds TavilyClient, so a missing key crashes startup."""
    with pytest.raises(Exception) as excinfo:
        import_legacy()
    assert "api key" in str(excinfo.value).lower()


def test_should_import_when_only_tavily_key_present(isolated_env, import_legacy, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "dummy")
    module = import_legacy()
    assert module.YOUTUBE_API_KEY is None


def test_should_expose_exactly_eight_legacy_tools(legacy: ModuleType) -> None:
    names = {tool.name for tool in _list_tools(legacy)}
    assert names == EXPECTED_TOOLS


def test_should_keep_tool_decorator_returning_plain_function(legacy: ModuleType) -> None:
    assert callable(legacy.web_search)
    assert legacy.web_search.__name__ == "web_search"


def test_should_declare_enum_parameters(legacy: ModuleType) -> None:
    tools = {tool.name: tool for tool in _list_tools(legacy)}
    calc = tools["calculate_or_time"].inputSchema
    assert calc["required"] == ["action"]
    assert calc["properties"]["action"]["enum"] == ["time", "math"]
    assert calc["properties"]["value"]["default"] == "Africa/Cairo"
    yt = tools["youtube_expert"].inputSchema
    assert yt["properties"]["task"]["enum"] == ["auto", "search", "comments"]


def test_should_record_tool_definition_footprint(legacy: ModuleType) -> None:
    """Approximate context cost of the tool list (chars/4 heuristic, no tokenizer)."""
    payload = [
        {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
        for t in _list_tools(legacy)
    ]
    chars = len(json.dumps(payload))
    print(f"\nlegacy tool definitions: {chars} chars (~{chars // 4} tokens)")
    assert chars > 0
