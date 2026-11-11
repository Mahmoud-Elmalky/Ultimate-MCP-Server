"""MCP server bootstrap, tool registration, and diagnostics (SDK v2)."""

from __future__ import annotations

import logging
import platform
import sys
from dataclasses import asdict, dataclass
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from ultimate_mcp import __version__
from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.core.cache import PerClassTTLCache
from ultimate_mcp.core.config import ProfileName, Settings
from ultimate_mcp.core.execution import ExecutionManager, TimeoutCategory
from ultimate_mcp.core.filesystem import DEFAULT_READ_CHAR_LIMIT
from ultimate_mcp.core.models import ToolResult
from ultimate_mcp.core.permissions import PermissionLevel
from ultimate_mcp.core.redaction import configure_logging
from ultimate_mcp.core.registry import ToolRegistry, ToolSpec, is_package_available
from ultimate_mcp.modules.browser.manager import (
    BrowserSessionManager,
    check_playwright_browser_binaries,
)
from ultimate_mcp.modules.browser.tools import register_browser_tools
from ultimate_mcp.modules.documents.reader import read_workspace_text_file_service
from ultimate_mcp.modules.research.tools import register_research_tools
from ultimate_mcp.modules.system.actions import (
    execute_approved_action_service,
    prepare_write_file_service,
)
from ultimate_mcp.modules.utilities.tools import register_utility_tools
from ultimate_mcp.providers.finance.yfinance_provider import YFinanceMarketProvider
from ultimate_mcp.providers.search.adapters import (
    DuckDuckGoSearchProvider,
    OpenAlexAcademicProvider,
    TavilySearchProvider,
)
from ultimate_mcp.providers.weather.wttr import WttrWeatherProvider
from ultimate_mcp.providers.youtube.adapter import DefaultYouTubeProvider
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
    cache: PerClassTTLCache[Any]
    mcp: MCPServer
    browser_manager: BrowserSessionManager | None = None


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
    cache: PerClassTTLCache[Any] | None = None,
    browser_manager: BrowserSessionManager | None = None,
) -> ToolRegistry:
    """Construct the ``ToolRegistry`` with all implemented v2 tools."""
    registry = ToolRegistry()
    active_cache = cache if cache is not None else PerClassTTLCache()
    active_browser = (
        browser_manager
        if browser_manager is not None
        else BrowserSessionManager(approval_broker=broker)
    )

    tavily_key = settings.tavily_api_key.get_secret_value() if settings.tavily_api_key else None
    youtube_key = settings.youtube_api_key.get_secret_value() if settings.youtube_api_key else None
    weather_provider = WttrWeatherProvider()
    market_provider = YFinanceMarketProvider()
    youtube_provider = DefaultYouTubeProvider(api_key=youtube_key)
    tavily_provider = TavilySearchProvider(api_key=tavily_key)
    ddg_provider = DuckDuckGoSearchProvider()
    academic_provider = OpenAlexAcademicProvider()

    register_utility_tools(
        registry=registry,
        settings=settings,
        executor=executor,
        adapt_result=_adapt_result,
        cache=active_cache,
        weather_provider=weather_provider,
        market_provider=market_provider,
        youtube_provider=youtube_provider,
    )
    register_research_tools(
        registry=registry,
        settings=settings,
        executor=executor,
        adapt_result=_adapt_result,
        cache=active_cache,
        tavily_provider=tavily_provider,
        ddg_provider=ddg_provider,
        academic_provider=academic_provider,
    )
    register_browser_tools(
        registry=registry,
        settings=settings,
        executor=executor,
        broker=broker,
        manager=active_browser,
        adapt_result=_adapt_result,
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
    cache: PerClassTTLCache[Any] = PerClassTTLCache()
    browser_manager = BrowserSessionManager(approval_broker=broker)
    registry = build_registry(
        cfg,
        executor,
        broker,
        cache=cache,
        browser_manager=browser_manager,
    )

    mcp = MCPServer("Ultimate-MCP-Server-v2")
    for spec in registry.enabled_specs(cfg, cfg.profile):
        mcp.tool(name=spec.name, description=spec.description)(spec.mcp_wrapper)

    return GatewayRuntime(
        settings=cfg,
        registry=registry,
        executor=executor,
        broker=broker,
        cache=cache,
        mcp=mcp,
        browser_manager=browser_manager,
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

    tavily_key = cfg.tavily_api_key.get_secret_value() if cfg.tavily_api_key else None
    youtube_key = cfg.youtube_api_key.get_secret_value() if cfg.youtube_api_key else None
    provider_health = {
        "tavily": asdict(TavilySearchProvider(api_key=tavily_key).health()),
        "duckduckgo": asdict(DuckDuckGoSearchProvider().health()),
        "openalex": asdict(OpenAlexAcademicProvider().health()),
        "wttr.in": asdict(WttrWeatherProvider().health()),
        "yfinance": asdict(YFinanceMarketProvider().health()),
        "youtube": asdict(DefaultYouTubeProvider(api_key=youtube_key).health()),
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
            "health": provider_health,
        },
        "optional_groups": optional_groups,
        "browser": check_playwright_browser_binaries(),
        "enabled_tool_count": len(enabled_names),
        "enabled_tools": enabled_names,
        "tools": tools_report,
        "warnings": warnings,
    }
