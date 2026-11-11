"""Timezone-aware clock utility using stdlib ``zoneinfo`` (with ``tzdata`` on Windows)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ultimate_mcp.core.models import ErrorCode, Provenance, ToolResult, UltimateMCPError

DEFAULT_TIMEZONE = "Africa/Cairo"


def get_current_time_service(timezone: str = DEFAULT_TIMEZONE) -> ToolResult:
    """Return authoritative current date and time in the requested IANA timezone."""
    tz_name = timezone.strip() or DEFAULT_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Unknown or invalid IANA timezone '{tz_name}' (e.g. 'UTC', 'Africa/Cairo', "
            "'America/New_York').",
        ) from exc

    now = datetime.now(tz)
    formatted_12h = now.strftime("%Y-%m-%d %I:%M:%S %p")
    iso_str = now.isoformat()
    utc_offset = now.strftime("%z")

    return ToolResult.ok(
        tool="get_current_time",
        summary=f"Time in {tz_name}: {formatted_12h}",
        data={
            "timezone": tz_name,
            "iso": iso_str,
            "formatted": formatted_12h,
            "date": now.strftime("%Y-%m-%d"),
            "weekday": now.strftime("%A"),
            "utc_offset": utc_offset,
            "unix_timestamp": int(now.timestamp()),
        },
        provenance=[
            Provenance(
                provider="system_clock",
                source_id=tz_name,
                freshness_note="System clock converted via IANA tzdb (zoneinfo).",
                untrusted_content=False,
            )
        ],
    )
