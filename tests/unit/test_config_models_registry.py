"""Unit tests for Settings, secret redaction, result envelopes, and registry profiles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ultimate_mcp.core.config import ProfileName, Settings
from ultimate_mcp.core.execution import TimeoutCategory
from ultimate_mcp.core.models import (
    ErrorCode,
    Provenance,
    ResultStatus,
    ToolResult,
    UltimateMCPError,
)
from ultimate_mcp.core.permissions import PermissionLevel
from ultimate_mcp.core.redaction import (
    REDACTED_PLACEHOLDER,
    configure_logging,
    redact_text,
    redact_value,
)
from ultimate_mcp.core.registry import ToolRegistry, ToolSpec


def test_settings_should_start_without_api_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    cfg = Settings(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "out",
    )
    assert cfg.tavily_api_key is None
    assert cfg.youtube_api_key is None
    assert cfg.profile == ProfileName.DEFAULT
    cfg.ensure_directories()
    assert cfg.state_dir.is_dir()
    assert cfg.output_dir.is_dir()


def test_settings_should_ignore_placeholder_api_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAVILY_API_KEY", "your_tavily_api_key_here")
    cfg = Settings(state_dir=tmp_path / "s", output_dir=tmp_path / "o")
    assert cfg.tavily_api_key is None


def test_settings_should_reject_drive_root_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    anchor = Path(tmp_path.anchor)
    with pytest.raises(ValueError, match="overly broad root"):
        Settings(
            state_dir=tmp_path / "s",
            output_dir=tmp_path / "o",
            read_roots=[anchor],
        )


def test_settings_should_parse_semicolon_and_comma_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    r1 = tmp_path / "ws1"
    r2 = tmp_path / "ws2"
    r1.mkdir()
    r2.mkdir()
    monkeypatch.setenv("ULTIMATE_MCP_READ_ROOTS", f"{r1};{r2}")
    cfg = Settings(state_dir=tmp_path / "s", output_dir=tmp_path / "o")
    assert cfg.read_roots == [r1.resolve(), r2.resolve()]


def test_settings_redacted_dict_should_mask_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-super-secret-123456")
    cfg = Settings(state_dir=tmp_path / "s", output_dir=tmp_path / "o")
    dumped = json.dumps(cfg.redacted_dict())
    assert "tvly-super-secret-123456" not in dumped
    assert cfg.redacted_dict()["tavily_api_key"] == REDACTED_PLACEHOLDER


def test_redact_text_and_value_should_scrub_keys_and_tokens() -> None:
    secret = "my-custom-secret-998877"
    raw = {
        "api_key": "plain-value",
        "nested": {"Authorization": "Bearer abcdefghijklmnop", "note": f"leaked {secret}"},
        "items": ["tvly-1234567890abcdef", "safe"],
    }
    cleaned = redact_value(raw, known_secrets=[secret])
    assert cleaned["api_key"] == REDACTED_PLACEHOLDER
    assert cleaned["nested"]["Authorization"] == REDACTED_PLACEHOLDER
    assert cleaned["nested"]["note"] == f"leaked {REDACTED_PLACEHOLDER}"
    assert cleaned["items"][0] == REDACTED_PLACEHOLDER
    assert cleaned["items"][1] == "safe"
    assert redact_text("token AIzaSyD-12345678901234567890", ()) == f"token {REDACTED_PLACEHOLDER}"


def test_structured_logger_should_redact_secrets_in_file(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "server.log"
    secret = "secret-value-xyz987"
    log = configure_logging(level="INFO", log_file=log_path, known_secrets=[secret])
    log.info(f"Connecting with {secret}", extra={"request_id": "req1", "tool": "test"})
    for h in log.handlers:
        h.flush()
    content = log_path.read_text(encoding="utf-8")
    assert secret not in content
    assert REDACTED_PLACEHOLDER in content


def test_tool_result_output_budget_should_bound_and_save_artifact(tmp_path: Path) -> None:
    huge = "x" * 10_000
    res = ToolResult.ok(
        tool="big_tool",
        summary=huge,
        data={"body": huge},
        provenance=[Provenance(provider="unit_test", source_id="doc1")],
    )
    out_text = res.to_mcp_text(max_bytes=1_500, output_dir=tmp_path)
    parsed = json.loads(out_text)
    assert parsed["status"] == ResultStatus.PARTIAL.value
    assert parsed["metadata"]["truncated"] is True
    assert parsed["metadata"]["artifact_path"] is not None
    assert Path(parsed["metadata"]["artifact_path"]).exists()
    assert any("Output exceeded budget" in w for w in parsed["warnings"])


def test_registry_should_filter_by_profile_and_missing_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = Settings(state_dir=tmp_path / "s", output_dir=tmp_path / "o")
    reg = ToolRegistry()

    async def dummy() -> str:
        return "{}"

    def handler() -> ToolResult:
        return ToolResult.ok("t", "ok")

    reg.register(
        ToolSpec(
            name="core_tool",
            module="utilities",
            description="Always available",
            handler=handler,
            mcp_wrapper=dummy,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.DEFAULT}),
            timeout_category=TimeoutCategory.LOCAL,
        )
    )
    reg.register(
        ToolSpec(
            name="missing_pkg_tool",
            module="browser",
            description="Requires missing pkg",
            handler=handler,
            mcp_wrapper=dummy,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.DEFAULT, ProfileName.BROWSER}),
            optional_packages=("nonexistent_package_xyz_123",),
        )
    )
    reg.register(
        ToolSpec(
            name="missing_key_tool",
            module="research",
            description="Requires tavily key",
            handler=handler,
            mcp_wrapper=dummy,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.DEFAULT, ProfileName.RESEARCH}),
            required_settings=("tavily_api_key",),
        )
    )
    reg.register(
        ToolSpec(
            name="coding_only_tool",
            module="coding",
            description="Only in coding profile",
            handler=handler,
            mcp_wrapper=dummy,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.CODING}),
        )
    )

    enabled_default = [s.name for s in reg.enabled_specs(cfg, ProfileName.DEFAULT)]
    assert enabled_default == ["core_tool"]

    enabled_coding = [s.name for s in reg.enabled_specs(cfg, ProfileName.CODING)]
    assert enabled_coding == ["coding_only_tool"]

    enabled_full = [s.name for s in reg.enabled_specs(cfg, ProfileName.FULL)]
    assert set(enabled_full) == {"core_tool", "coding_only_tool"}

    with pytest.raises(ValueError, match="Duplicate tool"):
        reg.register(reg.all_specs()[0])
    assert UltimateMCPError(ErrorCode.INVALID_INPUT, "bad").code == ErrorCode.INVALID_INPUT
