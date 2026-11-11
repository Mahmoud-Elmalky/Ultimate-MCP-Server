"""MCP server bootstrap, tool registration, and diagnostics (SDK v2)."""

from __future__ import annotations

import logging
import platform
import sys
from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from ultimate_mcp import __version__
from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.core.config import ProfileName, Settings
from ultimate_mcp.core.execution import ExecutionManager, TimeoutCategory
from ultimate_mcp.core.filesystem import DEFAULT_READ_CHAR_LIMIT
from ultimate_mcp.core.models import ToolResult
from ultimate_mcp.core.permissions import PermissionLevel
from ultimate_mcp.core.redaction import configure_logging
from ultimate_mcp.core.registry import ToolRegistry, ToolSpec, is_package_available
from ultimate_mcp.modules.documents.reader import read_workspace_text_file_service
from ultimate_mcp.modules.system.actions import (
    execute_approved_action_service,
    prepare_write_file_service,
)
from ultimate_mcp.modules.utilities.calculator import calculate_service
from ultimate_mcp.modules.utilities.clock import DEFAULT_TIMEZONE, get_current_time_service
from ultimate_mcp.storage.db import check_fts5_available, init_database

logger = logging.getLogger("ultimate_mcp.bootstrap")

OPTIONAL_PACKAGE_GROUPS: dict[str, tuple[str, ...]] = {
    "research": ("httpx", "bs4", "lxml", "tavily", "ddgs"),
    "market": ("yfinance",),
    "youtube": ("googleapiclient", "youtube_transcript_api"),
    "browser": ("playwright",),
    "documents": ("pypdf", "docx", "openpyxl", "pptx"),
    "data": ("pandas", "numpy", "openpyxl"),
    "system": ("psutil",),
    "media": ("PIL",),
}


@dataclass
class GatewayRuntime:
    """Container holding the initialized MCPServer, settings, registry, and services."""

    settings: Settings
    registry: ToolRegistry
    executor: ExecutionManager
    broker: ApprovalBroker
    mcp: MCPServer


def _adapt_result(result: ToolResult, settings: Settings) -> CallToolResult:
    """Serialize ``ToolResult`` into a native ``CallToolResult`` with ``is_error``."""
    text = result.to_mcp_text(
        max_bytes=settings.max_output_bytes,
        output_dir=settings.output_dir,
        known_secrets=settings.known_secret_values(),
    )
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        is_error=result.is_error,
    )


def build_registry(
    settings: Settings,
    executor: ExecutionManager,
    broker: ApprovalBroker,
) -> ToolRegistry:
    """Construct the ``ToolRegistry`` with all implemented v2 tools."""
    registry = ToolRegistry()

    async def calculate(
        expression: Annotated[
            str,
            Field(
                min_length=1,
                max_length=500,
                description=(
                    "Mathematical expression using numbers, +, -, *, /, //, %, **, "
                    "constants (pi, e, tau), or allowlisted math functions (sqrt, sin, cos, "
                    "log, round, min, max, factorial, gcd, etc.)."
                ),
            ),
        ],
    ) -> CallToolResult:
        """Evaluate an arithmetic or math expression deterministically without eval()."""
        res = await executor.execute(
            "calculate",
            calculate_service,
            {"expression": expression},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
        return _adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="calculate",
            module="utilities",
            description=(
                "Evaluate an exact arithmetic or mathematical expression using a strict AST "
                "allowlist. Use instead of mental math."
            ),
            handler=calculate_service,
            mcp_wrapper=calculate,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset(
                {
                    ProfileName.DEFAULT,
                    ProfileName.RESEARCH,
                    ProfileName.CODING,
                    ProfileName.DOCUMENTS,
                    ProfileName.FULL,
                }
            ),
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
    )

    async def get_current_time(
        timezone: Annotated[
            str,
            Field(
                min_length=1,
                max_length=100,
                description="IANA timezone name such as 'UTC', 'Africa/Cairo', 'Europe/London'.",
            ),
        ] = DEFAULT_TIMEZONE,
    ) -> CallToolResult:
        """Return current date, 12-hour time, ISO timestamp, and weekday in a named timezone."""
        res = await executor.execute(
            "get_current_time",
            get_current_time_service,
            {"timezone": timezone},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
        return _adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="get_current_time",
            module="utilities",
            description=(
                "Get the authoritative current date, time, ISO timestamp, and weekday for an "
                "IANA timezone (default Africa/Cairo)."
            ),
            handler=get_current_time_service,
            mcp_wrapper=get_current_time,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset(
                {
                    ProfileName.DEFAULT,
                    ProfileName.RESEARCH,
                    ProfileName.CODING,
                    ProfileName.DOCUMENTS,
                    ProfileName.BROWSER,
                    ProfileName.FULL,
                }
            ),
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
            produces_provenance=True,
        )
    )

    async def read_text_file(
        file_path: Annotated[
            str,
            Field(
                min_length=1,
                max_length=2000,
                description="File path inside configured workspace read roots.",
            ),
        ],
        offset: Annotated[
            int,
            Field(ge=0, description="Character offset to start reading from."),
        ] = 0,
        max_chars: Annotated[
            int,
            Field(
                ge=64,
                le=20000,
                description="Maximum characters to return in this page.",
            ),
        ] = DEFAULT_READ_CHAR_LIMIT,
    ) -> CallToolResult:
        """Read UTF-8 text from a file within configured workspace roots with pagination."""
        res = await executor.execute(
            "read_text_file",
            read_workspace_text_file_service,
            {
                "settings": settings,
                "file_path": file_path,
                "offset": offset,
                "max_chars": max_chars,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
        return _adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="read_text_file",
            module="documents",
            description=(
                "Read bounded UTF-8 text from a file inside configured workspace roots. "
                "Supports offset pagination and never follows symlinks outside roots."
            ),
            handler=read_workspace_text_file_service,
            mcp_wrapper=read_text_file,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset(
                {
                    ProfileName.DEFAULT,
                    ProfileName.CODING,
                    ProfileName.DOCUMENTS,
                    ProfileName.FULL,
                }
            ),
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
            produces_provenance=True,
        )
    )

    async def prepare_write_file(
        file_path: Annotated[
            str,
            Field(
                min_length=1,
                max_length=2000,
                description="Target file path inside configured write roots.",
            ),
        ],
        content: Annotated[
            str,
            Field(
                max_length=200000,
                description="UTF-8 text content to write once approved.",
            ),
        ],
        overwrite: Annotated[
            bool,
            Field(description="Whether overwriting an existing file is requested."),
        ] = False,
    ) -> CallToolResult:
        """Prepare a pending file-write request requiring out-of-band human approval."""
        res = await executor.execute(
            "prepare_write_file",
            prepare_write_file_service,
            {
                "settings": settings,
                "broker": broker,
                "file_path": file_path,
                "content": content,
                "overwrite": overwrite,
            },
            permission=PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=False,
        )
        return _adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="prepare_write_file",
            module="system",
            description=(
                "Stage a UTF-8 file write inside approved write roots and return an approval_id "
                "and SHA-256 digest. Performs no write until approved out-of-band."
            ),
            handler=prepare_write_file_service,
            mcp_wrapper=prepare_write_file,
            permission=PermissionLevel.L1_PREPARE,
            profiles=frozenset({ProfileName.DEFAULT, ProfileName.CODING, ProfileName.FULL}),
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=False,
        )
    )

    async def execute_approved_action(
        approval_id: Annotated[
            str,
            Field(
                min_length=4,
                max_length=64,
                description=(
                    "Approval ID previously authorized via `ultimate-mcp approvals approve`."
                ),
            ),
        ],
    ) -> CallToolResult:
        """Execute a single-use action that was approved out-of-band by the local user."""
        res = await executor.execute(
            "execute_approved_action",
            execute_approved_action_service,
            {
                "settings": settings,
                "broker": broker,
                "approval_id": approval_id,
            },
            permission=PermissionLevel.L2_WRITE,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=False,
            has_valid_approval=True,
        )
        return _adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="execute_approved_action",
            module="system",
            description=(
                "Execute a prepared operation after the user has approved its approval_id via "
                "the local CLI. Verifies expiry, digest, and one-time consumption."
            ),
            handler=execute_approved_action_service,
            mcp_wrapper=execute_approved_action,
            permission=PermissionLevel.L2_WRITE,
            profiles=frozenset({ProfileName.DEFAULT, ProfileName.CODING, ProfileName.FULL}),
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=False,
        )
    )

    return registry


def create_gateway(
    settings: Settings | None = None,
    profile_override: ProfileName | None = None,
) -> GatewayRuntime:
    """Create and configure the Ultimate MCP v2 gateway for the selected startup profile."""
    cfg = settings or Settings()
    if profile_override is not None:
        cfg.profile = profile_override

    configure_logging(
        level=cfg.log_level,
        log_file=cfg.log_file,
        known_secrets=cfg.known_secret_values(),
    )
    cfg.ensure_directories()
    init_database(cfg.db_path)

    if cfg.profile == ProfileName.FULL:
        logger.warning(
            "Profile 'full' is active: exposing all enabled tools increases model context cost."
        )

    executor = ExecutionManager(cfg)
    broker = ApprovalBroker(
        cfg.db_path,
        default_ttl_seconds=cfg.approval_ttl_seconds,
        known_secrets=cfg.known_secret_values(),
    )
    registry = build_registry(cfg, executor, broker)

    mcp = MCPServer("Ultimate-MCP-Server-v2")
    for spec in registry.enabled_specs(cfg, cfg.profile):
        mcp.tool(name=spec.name, description=spec.description)(spec.mcp_wrapper)

    return GatewayRuntime(
        settings=cfg,
        registry=registry,
        executor=executor,
        broker=broker,
        mcp=mcp,
    )


def build_doctor_report(
    settings: Settings | None = None,
    profile_override: ProfileName | None = None,
) -> dict[str, Any]:
    """Generate a structured diagnostic report (no secrets exposed)."""
    cfg = settings or Settings()
    if profile_override is not None:
        cfg.profile = profile_override

    cfg.ensure_directories()
    init_database(cfg.db_path)
    executor = ExecutionManager(cfg)
    broker = ApprovalBroker(cfg.db_path, default_ttl_seconds=cfg.approval_ttl_seconds)
    registry = build_registry(cfg, executor, broker)

    optional_groups: dict[str, dict[str, Any]] = {}
    for group, pkgs in OPTIONAL_PACKAGE_GROUPS.items():
        pkg_state = {pkg: is_package_available(pkg) for pkg in pkgs}
        optional_groups[group] = {
            "all_installed": all(pkg_state.values()),
            "packages": pkg_state,
        }

    tools_report = registry.catalog_report(cfg, cfg.profile)
    enabled_names = [t["name"] for t in tools_report if t["enabled"]]

    warnings: list[str] = []
    if cfg.profile == ProfileName.FULL:
        warnings.append(
            "Profile 'full' exposes all enabled tools and may consume extra local model context."
        )
    if not cfg.read_roots and not cfg.write_roots:
        warnings.append(
            "No custom read_roots or write_roots configured; filesystem tools are restricted to "
            "the default artifacts output_dir until roots are set."
        )

    return {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "profile": cfg.profile.value,
        "sqlite_fts5": check_fts5_available(),
        "settings": cfg.redacted_dict(),
        "providers": {
            "tavily_configured": cfg.tavily_api_key is not None,
            "youtube_configured": cfg.youtube_api_key is not None,
        },
        "optional_groups": optional_groups,
        "enabled_tool_count": len(enabled_names),
        "enabled_tools": enabled_names,
        "tools": tools_report,
        "warnings": warnings,
    }
