"""wttr.in weather provider adapter (`format=j1` JSON API).

Follows ``phases/PHASE-02_RESEARCH_UTILITIES.md`` §E.1:
- Uses SSRF-hardened HTTP fetching or injected JSON fetcher.
- Returns explicit observation time, units (°C, °F, km/h, %), resolved area name,
  and optional multi-day forecast.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ultimate_mcp.core.http import fetch_safe_url
from ultimate_mcp.core.models import ErrorCode, UltimateMCPError
from ultimate_mcp.core.registry import is_package_available
from ultimate_mcp.providers.base import ProviderHealth, WeatherReport

WttrFetcher = Callable[[str], dict[str, Any]]


def _default_wttr_fetcher(location: str) -> dict[str, Any]:
    safe_loc = urllib.parse.quote(location.strip(), safe="")
    url = f"https://wttr.in/{safe_loc}?format=j1"
    resp = fetch_safe_url(
        url,
        allowed_content_types=("application/json", "text/plain", "text/html"),
        max_response_bytes=512 * 1024,
        connect_timeout=5.0,
        read_timeout=10.0,
    )
    try:
        data = json.loads(resp.text)
    except json.JSONDecodeError as exc:
        raise UltimateMCPError(
            ErrorCode.UPSTREAM_INVALID_RESPONSE,
            f"Weather provider wttr.in returned malformed JSON for '{location}'.",
        ) from exc
    if not isinstance(data, dict):
        raise UltimateMCPError(
            ErrorCode.UPSTREAM_INVALID_RESPONSE,
            "Weather provider returned unexpected non-object JSON payload.",
        )
    return data


def _extract_first_text(items: Any, default: str = "") -> str:
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return str(items[0].get("value", default)).strip()
    return default


class WttrWeatherProvider:
    """Weather provider backed by ``wttr.in`` JSON endpoint."""

    def __init__(self, fetcher: WttrFetcher | None = None) -> None:
        self._fetcher = fetcher or _default_wttr_fetcher

    @property
    def name(self) -> str:
        return "wttr.in"

    def health(self) -> ProviderHealth:
        has_httpx = is_package_available("httpx")
        return ProviderHealth(
            provider=self.name,
            available=has_httpx,
            requires_api_key=False,
            api_key_configured=True,
            optional_packages=("httpx",),
            missing_packages=() if has_httpx else ("httpx",),
            reason="ready" if has_httpx else "missing optional package: httpx",
        )

    def get_weather(self, location: str, *, include_forecast: bool = False) -> WeatherReport:
        cleaned = location.strip()
        if not cleaned or len(cleaned) > 200:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                "Location must be a non-empty string up to 200 characters.",
            )

        payload = self._fetcher(cleaned)
        current_list = payload.get("current_condition")
        if not isinstance(current_list, list) or not current_list:
            raise UltimateMCPError(
                ErrorCode.NOT_FOUND,
                f"No current weather observations returned for '{cleaned}'.",
            )

        cur = current_list[0]
        if not isinstance(cur, dict):
            raise UltimateMCPError(
                ErrorCode.UPSTREAM_INVALID_RESPONSE,
                "Malformed current_condition entry from weather provider.",
            )

        area_list = payload.get("nearest_area")
        resolved_area = cleaned
        if isinstance(area_list, list) and area_list and isinstance(area_list[0], dict):
            area_obj = area_list[0]
            area_name = _extract_first_text(area_obj.get("areaName"), cleaned)
            country = _extract_first_text(area_obj.get("country"), "")
            resolved_area = f"{area_name}, {country}" if country else area_name

        try:
            temp_c = float(cur.get("temp_C", 0.0))
            temp_f = float(cur.get("temp_F", temp_c * 9.0 / 5.0 + 32.0))
            feels_c = float(cur.get("FeelsLikeC", temp_c))
            feels_f = float(cur.get("FeelsLikeF", temp_f))
            humidity = int(float(cur.get("humidity", 0)))
            wind_kmph = float(cur.get("windspeedKmph", 0.0))
        except (TypeError, ValueError) as exc:
            raise UltimateMCPError(
                ErrorCode.UPSTREAM_INVALID_RESPONSE,
                f"Invalid numeric weather fields returned by provider: {exc}",
            ) from exc

        condition = _extract_first_text(cur.get("weatherDesc"), "Unknown")
        wind_dir = str(cur.get("winddir16Point", "")).strip()
        obs_time = str(cur.get("localObsDateTime") or cur.get("observation_time") or "unknown")

        forecast_days: list[dict[str, Any]] = []
        if include_forecast:
            raw_weather = payload.get("weather")
            if isinstance(raw_weather, list):
                for day in raw_weather[:3]:
                    if isinstance(day, dict):
                        forecast_days.append(
                            {
                                "date": str(day.get("date", "")),
                                "min_temp_c": float(day.get("mintempC", 0.0)),
                                "max_temp_c": float(day.get("maxtempC", 0.0)),
                                "avg_temp_c": float(day.get("avgtempC", 0.0)),
                            }
                        )

        return WeatherReport(
            location=cleaned,
            resolved_area=resolved_area,
            condition=condition,
            temperature_c=temp_c,
            temperature_f=temp_f,
            feels_like_c=feels_c,
            feels_like_f=feels_f,
            humidity_percent=humidity,
            wind_kmph=wind_kmph,
            wind_direction=wind_dir,
            observation_time_utc=obs_time,
            provider=self.name,
            retrieved_at=datetime.now(UTC).isoformat(),
            forecast_days=forecast_days,
        )
