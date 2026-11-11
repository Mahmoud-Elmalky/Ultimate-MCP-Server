"""MCP tool registration for utilities, live data, and YouTube tools (Phase 02)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from mcp.types import CallToolResult
from pydantic import Field

from ultimate_mcp.core.cache import PerClassTTLCache
from ultimate_mcp.core.config import ProfileName, Settings
from ultimate_mcp.core.execution import ExecutionManager, TimeoutCategory
from ultimate_mcp.core.models import ToolResult
from ultimate_mcp.core.permissions import PermissionLevel
from ultimate_mcp.core.registry import ToolRegistry, ToolSpec
from ultimate_mcp.modules.research.live_data_service import (
    get_exchange_rate_service,
    get_market_price_service,
    get_weather_service,
    legacy_calculate_or_time_service,
    legacy_youtube_expert_service,
    youtube_comments_service,
    youtube_search_service,
    youtube_transcript_service,
)
from ultimate_mcp.modules.utilities.calculator import calculate_service
from ultimate_mcp.modules.utilities.clock import DEFAULT_TIMEZONE, get_current_time_service
from ultimate_mcp.modules.utilities.units import convert_units_service
from ultimate_mcp.providers.finance.yfinance_provider import YFinanceMarketProvider
from ultimate_mcp.providers.weather.wttr import WttrWeatherProvider
from ultimate_mcp.providers.youtube.adapter import DefaultYouTubeProvider

ResultAdapter = Callable[[ToolResult, Settings], CallToolResult]


def register_utility_tools(
    registry: ToolRegistry,
    settings: Settings,
    executor: ExecutionManager,
    adapt_result: ResultAdapter,
    cache: PerClassTTLCache[Any],
    weather_provider: WttrWeatherProvider,
    market_provider: YFinanceMarketProvider,
    youtube_provider: DefaultYouTubeProvider,
) -> None:
    """Register calculator, clock, unit conversion, weather, market, and YouTube tools."""

    async def calculate(
        expression: Annotated[
            str,
            Field(
                min_length=1,
                max_length=500,
                description=(
                    "Arithmetic/math expression using +, -, *, /, //, %, **, constants "
                    "(pi, e, tau), or allowlisted math functions (sqrt, sin, cos, log, etc.)."
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
        return adapt_result(res, settings)

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
        return adapt_result(res, settings)

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

    async def convert_units(
        value: Annotated[float, Field(description="Numeric quantity to convert.")],
        from_unit: Annotated[
            str,
            Field(min_length=1, max_length=32, description="Source unit (e.g. 'km', 'lb', 'c')."),
        ],
        to_unit: Annotated[
            str,
            Field(min_length=1, max_length=32, description="Target unit (e.g. 'mi', 'kg', 'f')."),
        ],
    ) -> CallToolResult:
        """Convert quantities across length, mass, temperature, volume, speed, area, or data."""
        res = await executor.execute(
            "convert_units",
            convert_units_service,
            {"value": value, "from_unit": from_unit, "to_unit": to_unit},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="convert_units",
            module="utilities",
            description=(
                "Convert numeric values between physical or digital units (length, mass, time, "
                "temperature, volume, speed, area, data, pressure)."
            ),
            handler=convert_units_service,
            mcp_wrapper=convert_units,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.RESEARCH, ProfileName.FULL}),
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
            produces_provenance=True,
        )
    )

    async def get_weather(
        location: Annotated[
            str,
            Field(min_length=1, max_length=120, description="City or region name (e.g. 'Cairo')."),
        ],
        include_forecast: Annotated[
            bool,
            Field(description="Include a 3-day daily min/max forecast summary."),
        ] = False,
    ) -> CallToolResult:
        """Retrieve current weather observations and optional 3-day forecast for a location."""
        res = await executor.execute(
            "get_weather",
            get_weather_service,
            {
                "location": location,
                "provider": weather_provider,
                "include_forecast": include_forecast,
                "cache": cache,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider=weather_provider.name,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="get_weather",
            module="utilities",
            description=(
                "Get current weather conditions (°C/°F, humidity, wind, observation time) and "
                "optional 3-day forecast for a city or location."
            ),
            handler=get_weather_service,
            mcp_wrapper=get_weather,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.RESEARCH, ProfileName.FULL}),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
            provider=weather_provider.name,
        )
    )

    async def get_market_price(
        symbol: Annotated[
            str,
            Field(
                min_length=1,
                max_length=24,
                description="Ticker symbol or alias (e.g. 'AAPL', 'MSFT', 'BTC-USD', 'GOLD').",
            ),
        ],
    ) -> CallToolResult:
        """Retrieve latest available quote, currency, market state, and timestamp for a symbol."""
        res = await executor.execute(
            "get_market_price",
            get_market_price_service,
            {"symbol": symbol, "provider": market_provider, "cache": cache},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider=market_provider.name,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="get_market_price",
            module="utilities",
            description=(
                "Get the latest available market quote, currency, timestamp, and market state "
                "for a stock, index, commodity, or crypto ticker (delayed/indicative feed)."
            ),
            handler=get_market_price_service,
            mcp_wrapper=get_market_price,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.RESEARCH, ProfileName.FULL}),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
            provider=market_provider.name,
        )
    )

    async def get_exchange_rate(
        base_currency: Annotated[
            str,
            Field(
                min_length=3,
                max_length=3,
                description="3-letter ISO currency code (e.g. 'USD').",
            ),
        ],
        target_currency: Annotated[
            str,
            Field(
                min_length=3,
                max_length=3,
                description="3-letter ISO currency code (e.g. 'EGP').",
            ),
        ],
        amount: Annotated[
            float,
            Field(gt=0, description="Positive amount in base_currency to convert."),
        ] = 1.0,
    ) -> CallToolResult:
        """Look up latest indicative FX rate and convert an amount between two ISO currencies."""
        res = await executor.execute(
            "get_exchange_rate",
            get_exchange_rate_service,
            {
                "base_currency": base_currency,
                "target_currency": target_currency,
                "amount": amount,
                "provider": market_provider,
                "cache": cache,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider=market_provider.name,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="get_exchange_rate",
            module="utilities",
            description=(
                "Convert an amount between two 3-letter ISO currencies and return the latest "
                "available exchange rate, timestamp, and provider caveat."
            ),
            handler=get_exchange_rate_service,
            mcp_wrapper=get_exchange_rate,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.RESEARCH, ProfileName.FULL}),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
            provider=market_provider.name,
        )
    )

    async def youtube_search(
        query: Annotated[
            str,
            Field(min_length=1, max_length=300, description="YouTube video search query."),
        ],
        max_results: Annotated[
            int,
            Field(ge=1, le=10, description="Maximum number of videos to return (1-10)."),
        ] = 5,
    ) -> CallToolResult:
        """Search public YouTube videos (requires YOUTUBE_API_KEY)."""
        res = await executor.execute(
            "youtube_search",
            youtube_search_service,
            {"query": query, "provider": youtube_provider, "max_results": max_results},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider=youtube_provider.name,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="youtube_search",
            module="utilities",
            description=(
                "Search public YouTube videos by keyword and return titles, video IDs, channels, "
                "and publication dates."
            ),
            handler=youtube_search_service,
            mcp_wrapper=youtube_search,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.RESEARCH, ProfileName.FULL}),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
            provider=youtube_provider.name,
        )
    )

    async def youtube_transcript(
        video_id_or_url: Annotated[
            str,
            Field(
                min_length=1,
                max_length=500,
                description="11-character YouTube video ID or watch/short URL.",
            ),
        ],
        offset: Annotated[
            int,
            Field(ge=0, description="Character offset for paginated transcript reading."),
        ] = 0,
        max_chars: Annotated[
            int,
            Field(ge=200, le=20000, description="Maximum characters to return in this segment."),
        ] = 8000,
    ) -> CallToolResult:
        """Retrieve a YouTube video's captions/transcript with language and offset pagination."""
        res = await executor.execute(
            "youtube_transcript",
            youtube_transcript_service,
            {
                "video_id_or_url": video_id_or_url,
                "provider": youtube_provider,
                "offset": offset,
                "max_chars": max_chars,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider=youtube_provider.name,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="youtube_transcript",
            module="utilities",
            description=(
                "Fetch captions/transcript text for a YouTube video ID or URL with language "
                "metadata and character-offset pagination."
            ),
            handler=youtube_transcript_service,
            mcp_wrapper=youtube_transcript,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.RESEARCH, ProfileName.FULL}),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
            provider=youtube_provider.name,
        )
    )

    async def youtube_comments(
        video_id_or_url: Annotated[
            str,
            Field(min_length=1, max_length=500, description="YouTube video ID or URL."),
        ],
        max_results: Annotated[
            int,
            Field(ge=1, le=20, description="Maximum top-level comments to fetch (1-20)."),
        ] = 10,
    ) -> CallToolResult:
        """Retrieve top-level public comments for a YouTube video (requires YOUTUBE_API_KEY)."""
        res = await executor.execute(
            "youtube_comments",
            youtube_comments_service,
            {
                "video_id_or_url": video_id_or_url,
                "provider": youtube_provider,
                "max_results": max_results,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider=youtube_provider.name,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="youtube_comments",
            module="utilities",
            description=(
                "Retrieve public top-level comments for a YouTube video by relevance when "
                "permitted by video settings and API quota."
            ),
            handler=youtube_comments_service,
            mcp_wrapper=youtube_comments,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.RESEARCH, ProfileName.FULL}),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
            provider=youtube_provider.name,
        )
    )

    # Legacy compatibility aliases (exposed only in FULL profile)
    async def calculate_or_time(
        action: Annotated[str, Field(description="Action: 'time' or 'math'.")],
        value: Annotated[str, Field(description="Timezone or math expression.")] = "Africa/Cairo",
    ) -> CallToolResult:
        """Deprecated compatibility wrapper: prefer `calculate` or `get_current_time`."""
        res = await executor.execute(
            "calculate_or_time",
            legacy_calculate_or_time_service,
            {"action": action, "value": value},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="calculate_or_time",
            module="utilities",
            description=(
                "[Deprecated: use calculate or get_current_time] Legacy wrapper for math or time."
            ),
            handler=legacy_calculate_or_time_service,
            mcp_wrapper=calculate_or_time,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.FULL}),
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
    )

    async def youtube_expert(
        query_or_url: Annotated[str, Field(min_length=1, max_length=500)],
        task: Annotated[str, Field(description="'auto', 'search', or 'comments'.")] = "auto",
    ) -> CallToolResult:
        """Deprecated compatibility wrapper: prefer youtube_search/transcript/comments."""
        res = await executor.execute(
            "youtube_expert",
            legacy_youtube_expert_service,
            {"query_or_url": query_or_url, "task": task, "provider": youtube_provider},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            provider=youtube_provider.name,
        )
        return adapt_result(res, settings)

    registry.register(
        ToolSpec(
            name="youtube_expert",
            module="utilities",
            description=(
                "[Deprecated: use youtube_search, youtube_transcript, or youtube_comments] "
                "Legacy YouTube helper wrapper."
            ),
            handler=legacy_youtube_expert_service,
            mcp_wrapper=youtube_expert,
            permission=PermissionLevel.L0_READ,
            profiles=frozenset({ProfileName.FULL}),
            timeout_category=TimeoutCategory.NETWORK,
            idempotent=True,
            produces_provenance=True,
            provider=youtube_provider.name,
        )
    )
