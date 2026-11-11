"""Weather, market price, exchange rate, YouTube, and legacy utility services."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from ultimate_mcp.core.cache import CacheDataClass, PerClassTTLCache, build_cache_key
from ultimate_mcp.core.models import (
    ErrorCode,
    Provenance,
    ResultStatus,
    ToolResult,
    UltimateMCPError,
)
from ultimate_mcp.modules.utilities.calculator import calculate_service
from ultimate_mcp.modules.utilities.clock import get_current_time_service
from ultimate_mcp.providers.base import (
    MarketDataProvider,
    WeatherProvider,
    YouTubeProvider,
)
from ultimate_mcp.providers.youtube.adapter import extract_youtube_video_id


def get_weather_service(
    location: str,
    *,
    provider: WeatherProvider,
    include_forecast: bool = False,
    cache: PerClassTTLCache[Any] | None = None,
) -> ToolResult:
    """Retrieve current weather conditions and optional short forecast with provenance."""
    cache_key = build_cache_key(
        CacheDataClass.WEATHER,
        provider.name,
        location,
        params={"include_forecast": include_forecast},
    )
    if cache is not None:
        hit = cache.get(cache_key)
        if hit is not None:
            rep = hit.value
            return ToolResult.ok(
                tool="get_weather",
                summary=(
                    f"{rep.resolved_area}: {rep.condition}, {rep.temperature_c}°C "
                    f"(feels like {rep.feels_like_c}°C), humidity {rep.humidity_percent}%, "
                    f"wind {rep.wind_kmph} km/h {rep.wind_direction}."
                ),
                data={**asdict(rep), "cached": True},
                provenance=[
                    Provenance(
                        provider=rep.provider,
                        retrieved_at=rep.retrieved_at,
                        source_id=rep.resolved_area,
                        published_at=rep.observation_time_utc,
                        freshness_note=hit.freshness_note(
                            f"Observation time: {rep.observation_time_utc}."
                        ),
                        untrusted_content=False,
                    )
                ],
            )

    rep = provider.get_weather(location, include_forecast=include_forecast)
    if cache is not None:
        cache.put(cache_key, CacheDataClass.WEATHER, provider.name, rep)

    return ToolResult.ok(
        tool="get_weather",
        summary=(
            f"{rep.resolved_area}: {rep.condition}, {rep.temperature_c}°C "
            f"(feels like {rep.feels_like_c}°C), humidity {rep.humidity_percent}%, "
            f"wind {rep.wind_kmph} km/h {rep.wind_direction}."
        ),
        data={**asdict(rep), "cached": False},
        provenance=[
            Provenance(
                provider=rep.provider,
                retrieved_at=rep.retrieved_at,
                source_id=rep.resolved_area,
                published_at=rep.observation_time_utc,
                freshness_note=f"Observation time: {rep.observation_time_utc}; units: °C/°F, km/h.",
                untrusted_content=False,
            )
        ],
    )


def get_market_price_service(
    symbol: str,
    *,
    provider: MarketDataProvider,
    cache: PerClassTTLCache[Any] | None = None,
) -> ToolResult:
    """Retrieve latest available market quote with currency, timestamp, and freshness caveat."""
    cache_key = build_cache_key(CacheDataClass.MARKET_QUOTE, provider.name, symbol.upper())
    if cache is not None:
        hit = cache.get(cache_key)
        if hit is not None:
            q = hit.value
            return ToolResult.ok(
                tool="get_market_price",
                summary=(
                    f"Latest available quote for {q.symbol}: {q.price} {q.currency} "
                    f"(as of {q.quote_timestamp}, state={q.market_state})."
                ),
                data={**asdict(q), "cached": True},
                provenance=[
                    Provenance(
                        provider=q.provider,
                        retrieved_at=q.retrieved_at,
                        source_id=q.symbol,
                        published_at=q.quote_timestamp,
                        freshness_note=hit.freshness_note(q.freshness_caveat),
                        untrusted_content=False,
                    )
                ],
            )

    q = provider.get_quote(symbol)
    if cache is not None:
        cache.put(cache_key, CacheDataClass.MARKET_QUOTE, provider.name, q)

    return ToolResult.ok(
        tool="get_market_price",
        summary=(
            f"Latest available quote for {q.symbol}: {q.price} {q.currency} "
            f"(as of {q.quote_timestamp}, state={q.market_state})."
        ),
        data={**asdict(q), "cached": False},
        provenance=[
            Provenance(
                provider=q.provider,
                retrieved_at=q.retrieved_at,
                source_id=q.symbol,
                published_at=q.quote_timestamp,
                freshness_note=q.freshness_caveat,
                untrusted_content=False,
            )
        ],
    )


def get_exchange_rate_service(
    base_currency: str,
    target_currency: str,
    *,
    amount: float = 1.0,
    provider: MarketDataProvider,
    cache: PerClassTTLCache[Any] | None = None,
) -> ToolResult:
    """Retrieve currency exchange rate and converted amount with timestamp and caveat."""
    pair_key = f"{base_currency.strip().upper()}->{target_currency.strip().upper()}"
    cache_key = build_cache_key(
        CacheDataClass.EXCHANGE_RATE,
        provider.name,
        pair_key,
        params={"amount": amount},
    )
    if cache is not None:
        hit = cache.get(cache_key)
        if hit is not None:
            fx = hit.value
            return ToolResult.ok(
                tool="get_exchange_rate",
                summary=(
                    f"{fx.amount} {fx.base_currency} = {fx.converted_amount} "
                    f"{fx.target_currency} (rate={fx.rate} as of {fx.quote_timestamp})."
                ),
                data={**asdict(fx), "cached": True},
                provenance=[
                    Provenance(
                        provider=fx.provider,
                        retrieved_at=fx.retrieved_at,
                        source_id=fx.symbol,
                        published_at=fx.quote_timestamp,
                        freshness_note=hit.freshness_note(fx.freshness_caveat),
                        untrusted_content=False,
                    )
                ],
            )

    fx = provider.get_exchange_rate(base_currency, target_currency, amount=amount)
    if cache is not None:
        cache.put(cache_key, CacheDataClass.EXCHANGE_RATE, provider.name, fx)

    return ToolResult.ok(
        tool="get_exchange_rate",
        summary=(
            f"{fx.amount} {fx.base_currency} = {fx.converted_amount} "
            f"{fx.target_currency} (rate={fx.rate} as of {fx.quote_timestamp})."
        ),
        data={**asdict(fx), "cached": False},
        provenance=[
            Provenance(
                provider=fx.provider,
                retrieved_at=fx.retrieved_at,
                source_id=fx.symbol,
                published_at=fx.quote_timestamp,
                freshness_note=fx.freshness_caveat,
                untrusted_content=False,
            )
        ],
    )


def youtube_search_service(
    query: str,
    *,
    provider: YouTubeProvider,
    max_results: int = 5,
) -> ToolResult:
    """Search YouTube videos via the configured YouTube provider."""
    videos = provider.search_videos(query, max_results=max_results)
    return ToolResult.ok(
        tool="youtube_search",
        summary=f"Found {len(videos)} YouTube video(s) for '{query.strip()}'.",
        data={
            "query": query.strip(),
            "results": [asdict(v) for v in videos],
        },
        provenance=[
            Provenance(
                provider=v.provider,
                retrieved_at=v.retrieved_at,
                source_url=v.url,
                source_id=v.video_id,
                published_at=v.published_at,
                untrusted_content=True,
            )
            for v in videos
        ],
    )


def youtube_transcript_service(
    video_id_or_url: str,
    *,
    provider: YouTubeProvider,
    languages: tuple[str, ...] = ("en", "ar"),
    offset: int = 0,
    max_chars: int = 8_000,
) -> ToolResult:
    """Retrieve a YouTube video's transcript with language metadata and offset pagination."""
    report = provider.get_transcript(
        video_id_or_url,
        languages=languages,
        offset=offset,
        max_chars=max_chars,
    )
    warnings: list[str] = []
    status = ResultStatus.SUCCESS
    if report.truncated:
        status = ResultStatus.PARTIAL
        warnings.append(
            f"Transcript truncated at {report.end_offset}/{report.total_chars} chars. "
            f"Pass offset={report.next_offset} to read the next segment."
        )
    res = ToolResult.ok(
        tool="youtube_transcript",
        summary=(
            f"Retrieved transcript ({report.language}, {len(report.text)} chars) "
            f"for video '{report.video_id}'."
        ),
        data=asdict(report),
        warnings=warnings,
        provenance=[
            Provenance(
                provider=report.provider,
                retrieved_at=report.retrieved_at,
                source_url=report.url,
                source_id=report.video_id,
                freshness_note=f"language={report.language}; generated={report.is_generated}",
                untrusted_content=True,
            )
        ],
        status=status,
    )
    res.metadata.truncated = report.truncated
    res.metadata.next_offset = report.next_offset
    return res


def youtube_comments_service(
    video_id_or_url: str,
    *,
    provider: YouTubeProvider,
    max_results: int = 10,
) -> ToolResult:
    """Retrieve top-level public comments for a YouTube video."""
    vid = extract_youtube_video_id(video_id_or_url) or video_id_or_url.strip()
    comments = provider.get_comments(video_id_or_url, max_results=max_results)
    now_iso = datetime.now(UTC).isoformat()
    return ToolResult.ok(
        tool="youtube_comments",
        summary=f"Retrieved {len(comments)} top-level comment(s) for video '{vid}'.",
        data={
            "video_id": vid,
            "comments": [asdict(c) for c in comments],
        },
        provenance=[
            Provenance(
                provider="youtube_data_api_v3",
                retrieved_at=now_iso,
                source_url=f"https://youtu.be/{vid}",
                source_id=vid,
                untrusted_content=True,
            )
        ],
    )


def legacy_calculate_or_time_service(
    action: str,
    value: str = "Africa/Cairo",
) -> ToolResult:
    """Safe compatibility wrapper for legacy `calculate_or_time` (uses AST calculator)."""
    if action == "math":
        res = calculate_service(value)
    elif action == "time":
        res = get_current_time_service(value)
    else:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Unsupported action '{action}'. Expected 'time' or 'math'.",
        )
    res.warnings.append(
        "Deprecated tool 'calculate_or_time': prefer 'calculate' or 'get_current_time'."
    )
    return res


def legacy_youtube_expert_service(
    query_or_url: str,
    task: str = "auto",
    *,
    provider: YouTubeProvider,
) -> ToolResult:
    """Compatibility wrapper for legacy `youtube_expert` routing to split v2 services."""
    vid = extract_youtube_video_id(query_or_url)
    if task == "search" or not vid:
        res = youtube_search_service(query_or_url, provider=provider, max_results=3)
    elif task == "comments":
        res = youtube_comments_service(query_or_url, provider=provider, max_results=5)
    elif task == "auto":
        res = youtube_transcript_service(query_or_url, provider=provider)
    else:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Unsupported task '{task}'. Expected 'auto', 'search', or 'comments'.",
        )
    res.warnings.append(
        "Deprecated tool 'youtube_expert': prefer 'youtube_search', 'youtube_transcript', "
        "or 'youtube_comments'."
    )
    return res
