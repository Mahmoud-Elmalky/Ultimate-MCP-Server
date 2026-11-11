"""Centralized validated configuration for Ultimate MCP Server v2.

Missing optional provider API keys never prevent core server startup; they only disable
the corresponding optional provider adapter.
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

from platformdirs import user_data_dir
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from ultimate_mcp.core.redaction import REDACTED_PLACEHOLDER

APP_NAME = "ultimate-mcp-server"
APP_AUTHOR = "UltimateMCP"

DEFAULT_MAX_OUTPUT_BYTES = 24_000
MIN_MAX_OUTPUT_BYTES = 1_024
MAX_MAX_OUTPUT_BYTES = 500_000

DEFAULT_MAX_CONCURRENCY = 4
MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 32

DEFAULT_APPROVAL_TTL_SECONDS = 600
MIN_APPROVAL_TTL_SECONDS = 30
MAX_APPROVAL_TTL_SECONDS = 86_400

DEFAULT_LOCAL_TIMEOUT_SECONDS = 5.0
DEFAULT_NETWORK_TIMEOUT_SECONDS = 15.0
DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 30.0

DEFAULT_BROWSER_MAX_SESSIONS = 2
DEFAULT_BROWSER_MAX_TABS = 5
DEFAULT_BROWSER_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
DEFAULT_BROWSER_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_BROWSER_NAV_TIMEOUT_MS = 15_000


class ProfileName(StrEnum):
    """Startup-selected tool exposure profiles (including small-model focused profiles)."""

    DEFAULT = "default"
    SEARCH = "search"
    WEB_RESEARCH = "web_research"
    FACT_CHECK = "fact_check"
    LIVE_DATA = "live_data"
    YOUTUBE = "youtube"
    RESEARCH = "research"
    CODING = "coding"
    DOCUMENTS = "documents"
    BROWSER = "browser"
    FULL = "full"


class TransportMode(StrEnum):
    """Supported MCP server transports."""

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable-http"


def _default_state_dir() -> Path:
    return Path(user_data_dir(APP_NAME, APP_AUTHOR)) / "state"


def _default_output_dir() -> Path:
    return Path(user_data_dir(APP_NAME, APP_AUTHOR)) / "artifacts"


def _parse_roots(raw: Any) -> list[Path]:
    """Parse semicolon- or comma-separated path roots into resolved Path objects."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list | tuple):
        items = [str(item).strip() for item in raw if str(item).strip()]
    elif isinstance(raw, Path):
        items = [str(raw)]
    elif isinstance(raw, str):
        sep = ";" if ";" in raw else ","
        items = [part.strip().strip("'\"") for part in raw.split(sep) if part.strip()]
    else:
        raise ValueError(f"Unsupported roots configuration type: {type(raw).__name__}")
    return [Path(item).expanduser().resolve(strict=False) for item in items]


def _is_broad_root(path: Path) -> bool:
    """Return True if path is a filesystem root (e.g. C:\\ or /) or the user's home root."""
    resolved = path.resolve(strict=False)
    if resolved == Path(resolved.anchor):
        return True
    try:
        home = Path.home().resolve(strict=False)
        if resolved == home:
            return True
    except RuntimeError:
        pass
    return False


class Settings(BaseSettings):
    """Validated runtime settings loaded from environment variables or an explicit ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    profile: ProfileName = Field(
        default=ProfileName.DEFAULT,
        validation_alias="ULTIMATE_MCP_PROFILE",
    )
    state_dir: Path = Field(
        default_factory=_default_state_dir,
        validation_alias="ULTIMATE_MCP_STATE_DIR",
    )
    output_dir: Path = Field(
        default_factory=_default_output_dir,
        validation_alias="ULTIMATE_MCP_OUTPUT_DIR",
    )
    read_roots: Annotated[list[Path], NoDecode] = Field(
        default_factory=list,
        validation_alias="ULTIMATE_MCP_READ_ROOTS",
    )
    write_roots: Annotated[list[Path], NoDecode] = Field(
        default_factory=list,
        validation_alias="ULTIMATE_MCP_WRITE_ROOTS",
    )
    allow_broad_roots: bool = Field(
        default=False,
        validation_alias="ULTIMATE_MCP_ALLOW_BROAD_ROOTS",
    )
    log_level: str = Field(
        default="INFO",
        validation_alias="ULTIMATE_MCP_LOG_LEVEL",
    )
    log_file: Path | None = Field(
        default=None,
        validation_alias="ULTIMATE_MCP_LOG_FILE",
    )
    max_output_bytes: int = Field(
        default=DEFAULT_MAX_OUTPUT_BYTES,
        ge=MIN_MAX_OUTPUT_BYTES,
        le=MAX_MAX_OUTPUT_BYTES,
        validation_alias="ULTIMATE_MCP_MAX_OUTPUT_BYTES",
    )
    max_concurrency: int = Field(
        default=DEFAULT_MAX_CONCURRENCY,
        ge=MIN_CONCURRENCY,
        le=MAX_CONCURRENCY,
        validation_alias="ULTIMATE_MCP_MAX_CONCURRENCY",
    )
    approval_ttl_seconds: int = Field(
        default=DEFAULT_APPROVAL_TTL_SECONDS,
        ge=MIN_APPROVAL_TTL_SECONDS,
        le=MAX_APPROVAL_TTL_SECONDS,
        validation_alias="ULTIMATE_MCP_APPROVAL_TTL_SECONDS",
    )
    local_timeout_seconds: float = Field(
        default=DEFAULT_LOCAL_TIMEOUT_SECONDS,
        gt=0.1,
        le=120.0,
        validation_alias="ULTIMATE_MCP_LOCAL_TIMEOUT_SECONDS",
    )
    network_timeout_seconds: float = Field(
        default=DEFAULT_NETWORK_TIMEOUT_SECONDS,
        gt=0.5,
        le=300.0,
        validation_alias="ULTIMATE_MCP_NETWORK_TIMEOUT_SECONDS",
    )
    subprocess_timeout_seconds: float = Field(
        default=DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
        gt=0.5,
        le=600.0,
        validation_alias="ULTIMATE_MCP_SUBPROCESS_TIMEOUT_SECONDS",
    )
    transport: TransportMode = Field(
        default=TransportMode.STDIO,
        validation_alias="ULTIMATE_MCP_TRANSPORT",
    )
    http_host: str = Field(
        default="127.0.0.1",
        validation_alias="ULTIMATE_MCP_HTTP_HOST",
    )
    http_port: int = Field(
        default=8765,
        ge=1024,
        le=65535,
        validation_alias="ULTIMATE_MCP_HTTP_PORT",
    )

    tavily_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="TAVILY_API_KEY",
    )
    youtube_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="YOUTUBE_API_KEY",
    )
    browser_max_sessions: int = Field(
        default=DEFAULT_BROWSER_MAX_SESSIONS,
        ge=1,
        le=8,
        validation_alias="ULTIMATE_MCP_BROWSER_MAX_SESSIONS",
    )
    browser_max_tabs: int = Field(
        default=DEFAULT_BROWSER_MAX_TABS,
        ge=1,
        le=15,
        validation_alias="ULTIMATE_MCP_BROWSER_MAX_TABS",
    )
    browser_max_download_bytes: int = Field(
        default=DEFAULT_BROWSER_MAX_DOWNLOAD_BYTES,
        ge=1024,
        le=20_000_000,
        validation_alias="ULTIMATE_MCP_BROWSER_MAX_DOWNLOAD_BYTES",
    )
    browser_max_response_bytes: int = Field(
        default=DEFAULT_BROWSER_MAX_RESPONSE_BYTES,
        ge=65_536,
        le=10_485_760,
        validation_alias="ULTIMATE_MCP_BROWSER_MAX_RESPONSE_BYTES",
    )
    browser_navigation_timeout_ms: int = Field(
        default=DEFAULT_BROWSER_NAV_TIMEOUT_MS,
        ge=1_000,
        le=60_000,
        validation_alias="ULTIMATE_MCP_BROWSER_NAV_TIMEOUT_MS",
    )
    browser_headless: bool = Field(
        default=True,
        validation_alias="ULTIMATE_MCP_BROWSER_HEADLESS",
    )
    browser_disable_webrtc: bool = Field(
        default=True,
        validation_alias="ULTIMATE_MCP_BROWSER_DISABLE_WEBRTC",
    )

    @field_validator("state_dir", "output_dir", mode="before")
    @classmethod
    def _coerce_dir(cls, value: Any, info: Any) -> Path:
        if value is None or (isinstance(value, str) and not value.strip()):
            if info.field_name == "state_dir":
                return _default_state_dir()
            return _default_output_dir()
        return Path(str(value).strip()).expanduser().resolve(strict=False)

    @field_validator("log_file", mode="before")
    @classmethod
    def _coerce_optional_path(cls, value: Any) -> Path | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return Path(str(value).strip()).expanduser().resolve(strict=False)

    @field_validator("read_roots", "write_roots", mode="before")
    @classmethod
    def _coerce_roots(cls, value: Any) -> list[Path]:
        return _parse_roots(value)

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(f"Invalid log_level '{value}'. Expected one of {sorted(allowed)}.")
        return normalized

    @field_validator("tavily_api_key", "youtube_api_key", mode="before")
    @classmethod
    def _normalize_optional_secret(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            placeholders = {"", "your_tavily_api_key_here", "your_youtube_api_key_here"}
            if stripped in placeholders:
                return None
            return stripped
        return value

    @model_validator(mode="after")
    def _check_root_safety(self) -> Settings:
        if not self.allow_broad_roots:
            for root in [*self.read_roots, *self.write_roots]:
                if _is_broad_root(root):
                    raise ValueError(
                        f"Refusing overly broad root '{root}' (drive root or user home). "
                        "Configure a specific workspace directory or set "
                        "ULTIMATE_MCP_ALLOW_BROAD_ROOTS=true if intentional."
                    )
        return self

    @property
    def db_path(self) -> Path:
        """Path to the SQLite state and approvals database."""
        return self.state_dir / "ultimate_mcp.sqlite3"

    @property
    def effective_read_roots(self) -> list[Path]:
        """Allowed read roots (includes read_roots, write_roots, and output_dir)."""
        combined: list[Path] = []
        for root in [*self.read_roots, *self.write_roots, self.output_dir.resolve(strict=False)]:
            if root not in combined:
                combined.append(root)
        return combined

    @property
    def effective_write_roots(self) -> list[Path]:
        """Allowed write roots (includes write_roots and output_dir)."""
        combined: list[Path] = []
        for root in [*self.write_roots, self.output_dir.resolve(strict=False)]:
            if root not in combined:
                combined.append(root)
        return combined

    def ensure_directories(self) -> None:
        """Create state_dir and output_dir and verify write access."""
        for directory in (self.state_dir, self.output_dir):
            directory.mkdir(parents=True, exist_ok=True)
            if not os.access(directory, os.R_OK | os.W_OK):
                raise PermissionError(f"Directory '{directory}' is not readable and writable.")

    def known_secret_values(self) -> list[str]:
        """Return non-empty secret strings so loggers and error handlers can redact them."""
        secrets: list[str] = []
        for secret in (self.tavily_api_key, self.youtube_api_key):
            if secret is not None:
                raw = secret.get_secret_value()
                if raw:
                    secrets.append(raw)
        return secrets

    def redacted_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable summary with secrets masked."""
        return {
            "profile": self.profile.value,
            "transport": self.transport.value,
            "state_dir": str(self.state_dir),
            "output_dir": str(self.output_dir),
            "db_path": str(self.db_path),
            "read_roots": [str(p) for p in self.read_roots],
            "write_roots": [str(p) for p in self.write_roots],
            "log_level": self.log_level,
            "log_file": str(self.log_file) if self.log_file else None,
            "max_output_bytes": self.max_output_bytes,
            "max_concurrency": self.max_concurrency,
            "approval_ttl_seconds": self.approval_ttl_seconds,
            "local_timeout_seconds": self.local_timeout_seconds,
            "network_timeout_seconds": self.network_timeout_seconds,
            "subprocess_timeout_seconds": self.subprocess_timeout_seconds,
            "tavily_api_key": REDACTED_PLACEHOLDER if self.tavily_api_key else None,
            "youtube_api_key": REDACTED_PLACEHOLDER if self.youtube_api_key else None,
            "browser_max_sessions": self.browser_max_sessions,
            "browser_max_tabs": self.browser_max_tabs,
            "browser_max_download_bytes": self.browser_max_download_bytes,
            "browser_max_response_bytes": self.browser_max_response_bytes,
            "browser_navigation_timeout_ms": self.browser_navigation_timeout_ms,
            "browser_headless": self.browser_headless,
            "browser_disable_webrtc": self.browser_disable_webrtc,
        }
