"""Provider interfaces and normalized domain data models (Phase 02 §A)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ProviderHealth:
    """Health and availability status for an external or optional provider."""

    provider: str
    available: bool
    requires_api_key: bool = False
    api_key_configured: bool = False
    optional_packages: tuple[str, ...] = ()
    missing_packages: tuple[str, ...] = ()
    reason: str = "ready"


@dataclass(frozen=True)
class SearchResultItem:
    """Normalized search result item across web, news, docs, and academic providers."""

    title: str
    url: str
    snippet: str
    provider: str
    rank: int
    retrieved_at: str
    published_at: str | None = None
    authors: tuple[str, ...] = ()
    source_id: str | None = None


@dataclass(frozen=True)
class WeatherReport:
    """Normalized weather observation and optional short forecast."""

    location: str
    resolved_area: str
    condition: str
    temperature_c: float
    temperature_f: float
    feels_like_c: float
    feels_like_f: float
    humidity_percent: int
    wind_kmph: float
    wind_direction: str
    observation_time_utc: str
    provider: str
    retrieved_at: str
    forecast_days: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class MarketQuote:
    """Normalized financial market quote with explicit freshness caveats."""

    symbol: str
    price: float
    currency: str
    market_state: str
    exchange: str
    quote_timestamp: str
    provider: str
    retrieved_at: str
    previous_close: float | None = None
    change_percent: float | None = None
    freshness_caveat: str = (
        "Delayed or last-available market quote via Yahoo Finance; not guaranteed "
        "exchange real-time."
    )


@dataclass(frozen=True)
class ExchangeRateQuote:
    """Normalized currency exchange rate and optional converted amount."""

    base_currency: str
    target_currency: str
    symbol: str
    rate: float
    amount: float
    converted_amount: float
    quote_timestamp: str
    provider: str
    retrieved_at: str
    freshness_caveat: str = (
        "Indicative FX quote from Yahoo Finance; not an executable bank or exchange rate."
    )


@dataclass(frozen=True)
class YouTubeVideoItem:
    """Normalized YouTube video search item."""

    video_id: str
    title: str
    url: str
    channel_title: str
    published_at: str | None
    description: str
    provider: str
    retrieved_at: str


@dataclass(frozen=True)
class YouTubeTranscriptReport:
    """Normalized YouTube transcript result with offset pagination support."""

    video_id: str
    url: str
    language: str
    is_generated: bool
    text: str
    total_chars: int
    start_offset: int
    end_offset: int
    truncated: bool
    next_offset: int | None
    segment_count: int
    provider: str
    retrieved_at: str


@dataclass(frozen=True)
class YouTubeCommentItem:
    """Normalized public YouTube top-level comment."""

    comment_id: str
    author: str
    text: str
    like_count: int
    published_at: str | None


@dataclass(frozen=True)
class RSSFeedItem:
    """Normalized RSS or Atom feed entry."""

    title: str
    link: str
    summary: str
    published_at: str | None
    guid: str | None


@runtime_checkable
class SearchProvider(Protocol):
    """Interface for web, news, documentation, or academic search adapters."""

    @property
    def name(self) -> str: ...

    def health(self) -> ProviderHealth: ...

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        search_type: str = "general",
    ) -> list[SearchResultItem]: ...


@runtime_checkable
class WeatherProvider(Protocol):
    """Interface for weather adapters."""

    @property
    def name(self) -> str: ...

    def health(self) -> ProviderHealth: ...

    def get_weather(self, location: str, *, include_forecast: bool = False) -> WeatherReport: ...


@runtime_checkable
class MarketDataProvider(Protocol):
    """Interface for market quote and FX adapters."""

    @property
    def name(self) -> str: ...

    def health(self) -> ProviderHealth: ...

    def get_quote(self, symbol: str) -> MarketQuote: ...

    def get_exchange_rate(
        self,
        base_currency: str,
        target_currency: str,
        *,
        amount: float = 1.0,
    ) -> ExchangeRateQuote: ...


@runtime_checkable
class YouTubeProvider(Protocol):
    """Interface for YouTube search, transcript, and comment adapters."""

    @property
    def name(self) -> str: ...

    def health(self) -> ProviderHealth: ...

    def search_videos(self, query: str, *, max_results: int = 5) -> list[YouTubeVideoItem]: ...

    def get_transcript(
        self,
        video_id_or_url: str,
        *,
        languages: tuple[str, ...] = ("en", "ar"),
        offset: int = 0,
        max_chars: int = 8000,
    ) -> YouTubeTranscriptReport: ...

    def get_comments(
        self,
        video_id_or_url: str,
        *,
        max_results: int = 10,
    ) -> list[YouTubeCommentItem]: ...


@runtime_checkable
class RSSProvider(Protocol):
    """Interface for RSS and Atom feed adapters."""

    @property
    def name(self) -> str: ...

    def health(self) -> ProviderHealth: ...

    def fetch_feed(
        self,
        feed_url: str,
        *,
        query: str | None = None,
        max_items: int = 10,
    ) -> tuple[str, list[RSSFeedItem]]: ...
