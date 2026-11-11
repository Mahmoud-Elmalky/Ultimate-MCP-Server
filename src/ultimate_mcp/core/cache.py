"""Bounded, thread-safe, per-data-class TTL cache with freshness and stale guards.

Follows ``phases/PHASE-02_RESEARCH_UTILITIES.md`` §F:
- Cache keys isolate data class, provider, query/identifier, locale, user_scope, and params.
- Rejects caching sensitive parameter keys or credential-bearing parameter values unless an
  explicit policy (`allow_sensitive_policy=True`) is supplied.
- Rejects un-isolated user-specific identifiers (`user_id`, `account_id`, `tenant_id`, `email`)
  in generic `params` unless explicitly scoped via `user_scope`.
- TTL and maximum entry counts are enforced both globally and per data class. An entry is
  fresh only while ``now < expires_at``; it is expired when ``now >= expires_at``.
- Expired entries are purged proactively before capacity LRU eviction.
- Thread-safe via ``threading.RLock`` for concurrent ``asyncio.to_thread`` worker execution.
- Financial/market data classes (``MARKET_QUOTE``, ``EXCHANGE_RATE``) are prohibited from
  serving expired/stale entries even if ``allow_stale=True`` is requested.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

T = TypeVar("T")

DEFAULT_MAX_CACHE_ENTRIES = 256
DEFAULT_MAX_ENTRIES_PER_CLASS = 64

FORBIDDEN_SENSITIVE_PARAM_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "authorization",
        "auth",
        "cookie",
        "set_cookie",
        "session",
        "session_id",
        "credential",
        "credentials",
        "private_key",
        "bearer",
    }
)

UNISOLATED_USER_PARAM_KEYS: frozenset[str] = frozenset(
    {
        "user",
        "user_id",
        "userid",
        "username",
        "account",
        "account_id",
        "tenant",
        "tenant_id",
        "email",
        "profile_id",
    }
)

SENSITIVE_VALUE_REGEX = re.compile(
    r"(?i)(?:\bbearer\s+[a-z0-9._\-]{8,}|\b(?:tvly|sk|rk|pk)-[a-z0-9_\-]{8,}"
    r"|\bAIza[0-9A-Za-z_\-]{16,}|\bgh[pousr]_[0-9A-Za-z]{16,}"
    r"|\bxox[baprs]-[0-9A-Za-z\-]{10,}|(?:api_key|access_token|password|secret)\s*[=:]\s*\S+)"
)


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


NO_STALE_DATA_CLASSES: frozenset[CacheDataClass] = frozenset(
    {
        CacheDataClass.MARKET_QUOTE,
        CacheDataClass.EXCHANGE_RATE,
    }
)

DEFAULT_CLASS_TTL_SECONDS: dict[CacheDataClass, float] = {
    CacheDataClass.WEATHER: 300.0,  # 5 minutes
    CacheDataClass.MARKET_QUOTE: 60.0,  # 1 minute (never served stale)
    CacheDataClass.EXCHANGE_RATE: 300.0,  # 5 minutes (never served stale)
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


def _validate_cache_params_and_values(
    params: dict[str, Any] | None,
    *,
    has_explicit_user_scope: bool,
) -> None:
    """Reject sensitive keys/values and un-isolated user-specific identifiers."""
    if not params:
        return
    for raw_key, raw_val in params.items():
        norm_key = str(raw_key).strip().lower().replace("-", "_")
        if norm_key in FORBIDDEN_SENSITIVE_PARAM_KEYS or any(
            tok in norm_key for tok in ("secret", "password", "token", "api_key", "cookie")
        ):
            raise ValueError(
                f"Refusing to build cache key with sensitive parameter '{raw_key}'. "
                "Sensitive credentials must not be cached without an explicit security policy."
            )
        if not has_explicit_user_scope and norm_key in UNISOLATED_USER_PARAM_KEYS:
            raise ValueError(
                f"Refusing to cache user-specific parameter '{raw_key}' without explicit "
                "'user_scope' isolation."
            )
        if isinstance(raw_val, dict):
            _validate_cache_params_and_values(
                raw_val,
                has_explicit_user_scope=has_explicit_user_scope,
            )
        elif isinstance(raw_val, str) and SENSITIVE_VALUE_REGEX.search(raw_val):
            raise ValueError(
                f"Refusing to build cache key: parameter '{raw_key}' contains a credential-like "
                "secret or token value."
            )


def build_cache_key(
    data_class: CacheDataClass,
    provider: str,
    query: str,
    *,
    locale: str = "en",
    user_scope: str = "public",
    params: dict[str, Any] | None = None,
    allow_sensitive_policy: bool = False,
) -> str:
    """Create a deterministic SHA-256 cache key isolated by class, provider, scope, and query."""
    if not provider or not provider.strip():
        raise ValueError("provider must not be empty when building a cache key.")
    norm_scope = user_scope.strip().lower() or "public"
    if not allow_sensitive_policy:
        if SENSITIVE_VALUE_REGEX.search(query):
            raise ValueError(
                "Refusing to build cache key: query string contains a credential-like secret."
            )
        _validate_cache_params_and_values(
            params,
            has_explicit_user_scope=(norm_scope != "public"),
        )

    canonical = json.dumps(
        {
            "class": data_class.value,
            "provider": provider.strip().lower(),
            "scope": norm_scope,
            "query": query.strip(),
            "locale": locale.strip().lower(),
            "params": params or {},
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"{data_class.value}:{provider.strip().lower()}:{norm_scope}:{digest}"


class PerClassTTLCache(Generic[T]):
    """Thread-safe bounded LRU + per-data-class TTL cache with stale-serving guards."""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
        max_entries_per_class: int = DEFAULT_MAX_ENTRIES_PER_CLASS,
        ttl_overrides: dict[CacheDataClass, float] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        if max_entries_per_class <= 0:
            raise ValueError("max_entries_per_class must be > 0")
        self._max_entries = max_entries
        self._max_entries_per_class = min(max_entries_per_class, max_entries)
        self._ttls: dict[CacheDataClass, float] = {
            **DEFAULT_CLASS_TTL_SECONDS,
            **(ttl_overrides or {}),
        }
        self._clock = clock
        self._lock = threading.RLock()
        self._store: OrderedDict[str, CacheEntry[T]] = OrderedDict()

    def ttl_for(self, data_class: CacheDataClass) -> float:
        """Return configured TTL in seconds for ``data_class``."""
        return self._ttls.get(data_class, 300.0)

    def _purge_expired_locked(self, now: float) -> int:
        """Remove all entries with ``now >= expires_at`` while holding ``self._lock``.

        Note: this also drops entries that would otherwise be eligible for an explicitly
        requested stale fallback; stale fallback is best-effort, never guaranteed.
        """
        expired_keys = [k for k, entry in self._store.items() if now >= entry.expires_at]
        for k in expired_keys:
            del self._store[k]
        return len(expired_keys)

    def _evict_for_class_limit_locked(self, data_class: CacheDataClass) -> None:
        """Ensure ``data_class`` does not exceed ``self._max_entries_per_class``."""
        class_keys = [k for k, entry in self._store.items() if entry.data_class == data_class]
        overflow = len(class_keys) - self._max_entries_per_class
        for idx in range(max(0, overflow)):
            del self._store[class_keys[idx]]

    def put(
        self,
        key: str,
        data_class: CacheDataClass,
        provider: str,
        value: T,
        *,
        ttl_seconds: float | None = None,
    ) -> CacheEntry[T]:
        """Store a value under ``key`` with its data-class TTL and capacity limits."""
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_for(data_class)
        if ttl <= 0:
            raise ValueError("ttl_seconds must be > 0")

        with self._lock:
            now = self._clock()
            entry = CacheEntry(
                key=key,
                data_class=data_class,
                provider=provider.strip().lower(),
                value=value,
                created_at=now,
                expires_at=now + ttl,
            )
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = entry

            self._purge_expired_locked(now)
            if key not in self._store:
                self._store[key] = entry

            self._evict_for_class_limit_locked(data_class)
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
        """Retrieve a cached item if fresh (or within ``max_stale_seconds`` when allowed)."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None

            now = self._clock()
            age = max(0.0, now - entry.created_at)
            ttl = entry.expires_at - entry.created_at

            if now < entry.expires_at:
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

            stale_allowed = allow_stale and (entry.data_class not in NO_STALE_DATA_CLASSES)
            stale_overage = now - entry.expires_at
            if stale_allowed and max_stale_seconds > 0 and stale_overage <= max_stale_seconds:
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

    def count_by_class(self, data_class: CacheDataClass) -> int:
        """Return the number of currently stored entries for ``data_class``."""
        with self._lock:
            return sum(1 for entry in self._store.values() if entry.data_class == data_class)

    def clear(self) -> None:
        """Remove all entries from the cache."""
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
