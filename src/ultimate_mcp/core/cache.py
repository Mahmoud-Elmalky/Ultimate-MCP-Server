"""Bounded, per-data-class TTL cache with explicit freshness and stale metadata.

Follows ``phases/PHASE-02_RESEARCH_UTILITIES.md`` §F:
- Cache keys include data class, provider, query/identifier, locale, and normalized parameters.
- TTL is configured per data class (not a single global TTL).
- Entries return explicit cache hit/age/freshness metadata and can optionally serve stale
  entries within a bounded stale window when marked explicitly as stale.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

T = TypeVar("T")

DEFAULT_MAX_CACHE_ENTRIES = 256


class CacheDataClass(StrEnum):
    """Data classes with distinct freshness/TTL policies."""

    WEATHER = "weather"
    MARKET_QUOTE = "market_quote"
    EXCHANGE_RATE = "exchange_rate"
    WEB_SEARCH = "web_search"
    NEWS_SEARCH = "news_search"
    ACADEMIC_SEARCH = "academic_search"
    DOCS_SEARCH = "docs_search"
    WEBPAGE = "webpage"
    RSS_FEED = "rss_feed"
    YOUTUBE_SEARCH = "youtube_search"
    YOUTUBE_TRANSCRIPT = "youtube_transcript"
    YOUTUBE_COMMENTS = "youtube_comments"


DEFAULT_CLASS_TTL_SECONDS: dict[CacheDataClass, float] = {
    CacheDataClass.WEATHER: 300.0,  # 5 minutes
    CacheDataClass.MARKET_QUOTE: 60.0,  # 1 minute
    CacheDataClass.EXCHANGE_RATE: 300.0,  # 5 minutes
    CacheDataClass.WEB_SEARCH: 600.0,  # 10 minutes
    CacheDataClass.NEWS_SEARCH: 180.0,  # 3 minutes
    CacheDataClass.ACADEMIC_SEARCH: 3600.0,  # 1 hour
    CacheDataClass.DOCS_SEARCH: 1800.0,  # 30 minutes
    CacheDataClass.WEBPAGE: 900.0,  # 15 minutes
    CacheDataClass.RSS_FEED: 300.0,  # 5 minutes
    CacheDataClass.YOUTUBE_SEARCH: 900.0,  # 15 minutes
    CacheDataClass.YOUTUBE_TRANSCRIPT: 86400.0,  # 24 hours (transcripts are static)
    CacheDataClass.YOUTUBE_COMMENTS: 600.0,  # 10 minutes
}


@dataclass(frozen=True)
class CacheEntry(Generic[T]):
    """Stored value with creation, expiry, and freshness metadata."""

    key: str
    data_class: CacheDataClass
    provider: str
    value: T
    created_at: float
    expires_at: float

    @property
    def created_at_iso(self) -> str:
        return datetime.fromtimestamp(self.created_at, tz=UTC).isoformat()


@dataclass(frozen=True)
class CacheLookup(Generic[T]):
    """Result of looking up a key in the TTL cache."""

    value: T
    data_class: CacheDataClass
    provider: str
    cached_at_iso: str
    age_seconds: float
    ttl_seconds: float
    is_stale: bool

    def freshness_note(self, base_note: str = "") -> str:
        """Build an explicit freshness disclosure string."""
        state = "STALE cached result" if self.is_stale else "Cached result"
        prefix = f"{base_note} " if base_note else ""
        return (
            f"{prefix}[{state} from {self.provider}; cached_at={self.cached_at_iso}, "
            f"age={self.age_seconds:.1f}s, ttl={self.ttl_seconds:.0f}s]"
        )


def build_cache_key(
    data_class: CacheDataClass,
    provider: str,
    query: str,
    *,
    locale: str = "en",
    params: dict[str, Any] | None = None,
) -> str:
    """Create a deterministic SHA-256 cache key from normalized parameters."""
    canonical = json.dumps(
        {
            "class": data_class.value,
            "provider": provider.strip().lower(),
            "query": query.strip(),
            "locale": locale.strip().lower(),
            "params": params or {},
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"{data_class.value}:{provider.strip().lower()}:{digest}"


class PerClassTTLCache(Generic[T]):
    """Bounded LRU + per-data-class TTL cache with explicit stale marking."""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
        ttl_overrides: dict[CacheDataClass, float] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        self._max_entries = max_entries
        self._ttls: dict[CacheDataClass, float] = {
            **DEFAULT_CLASS_TTL_SECONDS,
            **(ttl_overrides or {}),
        }
        self._clock = clock
        self._store: OrderedDict[str, CacheEntry[T]] = OrderedDict()

    def ttl_for(self, data_class: CacheDataClass) -> float:
        """Return configured TTL in seconds for ``data_class``."""
        return self._ttls.get(data_class, 300.0)

    def put(
        self,
        key: str,
        data_class: CacheDataClass,
        provider: str,
        value: T,
        *,
        ttl_seconds: float | None = None,
    ) -> CacheEntry[T]:
        """Store a value under ``key`` with its data-class TTL."""
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_for(data_class)
        if ttl <= 0:
            raise ValueError("ttl_seconds must be > 0")
        now = self._clock()
        entry = CacheEntry(
            key=key,
            data_class=data_class,
            provider=provider,
            value=value,
            created_at=now,
            expires_at=now + ttl,
        )
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = entry
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)
        return entry

    def get(
        self,
        key: str,
        *,
        allow_stale: bool = False,
        max_stale_seconds: float = 300.0,
    ) -> CacheLookup[T] | None:
        """Retrieve a cached item if fresh (or within ``max_stale_seconds``)."""
        entry = self._store.get(key)
        if entry is None:
            return None

        now = self._clock()
        age = max(0.0, now - entry.created_at)
        ttl = entry.expires_at - entry.created_at

        if now <= entry.expires_at:
            self._store.move_to_end(key)
            return CacheLookup(
                value=entry.value,
                data_class=entry.data_class,
                provider=entry.provider,
                cached_at_iso=entry.created_at_iso,
                age_seconds=age,
                ttl_seconds=ttl,
                is_stale=False,
            )

        stale_overage = now - entry.expires_at
        if allow_stale and stale_overage <= max_stale_seconds:
            return CacheLookup(
                value=entry.value,
                data_class=entry.data_class,
                provider=entry.provider,
                cached_at_iso=entry.created_at_iso,
                age_seconds=age,
                ttl_seconds=ttl,
                is_stale=True,
            )

        del self._store[key]
        return None

    def clear(self) -> None:
        """Remove all entries from the cache."""
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)
