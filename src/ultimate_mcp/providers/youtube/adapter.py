"""YouTube search, transcript (`youtube-transcript-api` 1.2.x), and comments adapter.

Follows ``phases/PHASE-02_RESEARCH_UTILITIES.md`` §E.5–6:
- Fixes Phase 00 Risk R7: `youtube-transcript-api` 1.x removed the static
  `YouTubeTranscriptApi.get_transcript()` method in favor of instance
  `.fetch(video_id, languages=...)` (returning a `FetchedTranscript` whose
  snippets have `.text` or dict `'text'`). Supports both the 1.2.x instance
  `.fetch()` API and older `.get_transcript()` fallback if present.
- Splits `youtube_expert` into `youtube_search`, `youtube_transcript`, and `youtube_comments`
  while keeping `youtube_expert` as a documented compatibility wrapper.
- Handles disabled captions, unavailable videos, missing API key, and quota errors cleanly.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ultimate_mcp.core.models import ErrorCode, UltimateMCPError
from ultimate_mcp.core.registry import is_package_available
from ultimate_mcp.providers.base import (
    ProviderHealth,
    YouTubeCommentItem,
    YouTubeTranscriptReport,
    YouTubeVideoItem,
)

VIDEO_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^[0-9A-Za-z_-]{11}$"),
    re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([0-9A-Za-z_-]{11})"),
)


def extract_youtube_video_id(value: str) -> str | None:
    """Extract an 11-character YouTube video ID from a URL or bare ID string."""
    cleaned = value.strip()
    if not cleaned:
        return None
    for pattern in VIDEO_ID_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            return match.group(1) if match.lastindex else match.group(0)
    return None


class DefaultYouTubeProvider:
    """Lazy YouTube Data API v3 + `youtube-transcript-api` provider adapter."""

    def __init__(
        self,
        api_key: str | None = None,
        api_client_factory: Callable[[str], Any] | None = None,
        transcript_fetcher: Callable[[str, tuple[str, ...]], tuple[str, bool, list[str]]]
        | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip() or None
        self._api_client_factory = api_client_factory
        self._transcript_fetcher = transcript_fetcher

    @property
    def name(self) -> str:
        return "youtube"

    def health(self) -> ProviderHealth:
        has_gapi = is_package_available("googleapiclient")
        has_yt_trans = is_package_available("youtube_transcript_api")
        has_key = self._api_key is not None
        missing = [
            pkg
            for pkg, ok in (
                ("googleapiclient", has_gapi),
                ("youtube_transcript_api", has_yt_trans),
            )
            if not ok
        ]
        # Note: youtube_transcript works without YOUTUBE_API_KEY; search & comments require it.
        available = has_yt_trans or (has_gapi and has_key)
        reason = "ready"
        if not available:
            reason = f"missing packages ({', '.join(missing)}) or YOUTUBE_API_KEY"
        elif not has_key:
            reason = "transcript ready; search/comments require YOUTUBE_API_KEY"
        return ProviderHealth(
            provider=self.name,
            available=available,
            requires_api_key=False,
            api_key_configured=has_key,
            optional_packages=("googleapiclient", "youtube_transcript_api"),
            missing_packages=tuple(missing),
            reason=reason,
        )

    def _build_api_client(self) -> Any:
        if not self._api_key:
            raise UltimateMCPError(
                ErrorCode.AUTH_REQUIRED,
                "YOUTUBE_API_KEY is required for YouTube search and comment retrieval.",
            )
        if self._api_client_factory is not None:
            return self._api_client_factory(self._api_key)
        try:
            discovery: Any = importlib.import_module("googleapiclient.discovery")
        except ImportError as exc:
            raise UltimateMCPError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Optional package 'google-api-python-client' is not installed "
                "(install `[youtube]`).",
            ) from exc
        return discovery.build("youtube", "v3", developerKey=self._api_key)

    def search_videos(self, query: str, *, max_results: int = 5) -> list[YouTubeVideoItem]:
        cleaned = query.strip()
        if not cleaned or len(cleaned) > 500:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                "YouTube search query must be between 1 and 500 characters.",
            )
        capped = max(1, min(max_results, 10))
        client = self._build_api_client()
        try:
            req = client.search().list(
                q=cleaned,
                part="snippet",
                type="video",
                maxResults=capped,
            )
            response = req.execute()
        except UltimateMCPError:
            raise
        except Exception as exc:
            msg = str(exc)
            if "quota" in msg.lower() or "403" in msg or "429" in msg:
                raise UltimateMCPError(
                    ErrorCode.RATE_LIMITED,
                    "YouTube Data API quota exceeded or access restricted.",
                    retryable=False,
                ) from exc
            raise UltimateMCPError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"YouTube search failed: {type(exc).__name__}",
                retryable=True,
            ) from exc

        now_iso = datetime.now(UTC).isoformat()
        items: list[YouTubeVideoItem] = []
        for raw in (response or {}).get("items", []):
            if not isinstance(raw, dict):
                continue
            vid = str((raw.get("id") or {}).get("videoId") or "").strip()
            snippet = raw.get("snippet") or {}
            if not vid or not isinstance(snippet, dict):
                continue
            items.append(
                YouTubeVideoItem(
                    video_id=vid,
                    title=str(snippet.get("title") or vid).strip(),
                    url=f"https://youtu.be/{vid}",
                    channel_title=str(snippet.get("channelTitle") or "").strip(),
                    published_at=str(snippet.get("publishedAt") or "") or None,
                    description=" ".join(str(snippet.get("description") or "").split())[:400],
                    provider="youtube_data_api_v3",
                    retrieved_at=now_iso,
                )
            )
        return items

    def _fetch_raw_transcript(
        self,
        video_id: str,
        languages: tuple[str, ...],
    ) -> tuple[str, bool, list[str]]:
        if self._transcript_fetcher is not None:
            return self._transcript_fetcher(video_id, languages)

        try:
            yt_mod: Any = importlib.import_module("youtube_transcript_api")
        except ImportError as exc:
            raise UltimateMCPError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Optional package 'youtube-transcript-api' is not installed "
                "(install `[youtube]`).",
            ) from exc

        api_cls = yt_mod.YouTubeTranscriptApi
        try:
            # youtube-transcript-api >= 1.0 instance API: YouTubeTranscriptApi().fetch(...)
            if hasattr(api_cls, "fetch"):
                instance = api_cls()
                fetched = instance.fetch(video_id, languages=list(languages))
                lang = str(getattr(fetched, "language_code", None) or languages[0])
                is_gen = bool(getattr(fetched, "is_generated", False))
                segments: list[str] = []
                for snippet in fetched:
                    txt = (
                        snippet.get("text", "")
                        if isinstance(snippet, dict)
                        else getattr(snippet, "text", "")
                    )
                    cleaned_seg = " ".join(str(txt).split())
                    if cleaned_seg:
                        segments.append(cleaned_seg)
                return lang, is_gen, segments

            # Fallback for legacy 0.x API if ever present
            raw_list = api_cls.get_transcript(video_id, languages=list(languages))
            segments = [
                " ".join(str(item.get("text", "")).split())
                for item in raw_list
                if isinstance(item, dict) and str(item.get("text", "")).strip()
            ]
            return languages[0], False, segments
        except Exception as exc:
            exc_name = type(exc).__name__.lower()
            if any(k in exc_name for k in ("disabled", "notranscript", "unavailable", "notfound")):
                raise UltimateMCPError(
                    ErrorCode.NOT_FOUND,
                    f"No transcript available for video '{video_id}' ({type(exc).__name__}).",
                ) from exc
            raise UltimateMCPError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"Transcript retrieval failed for '{video_id}': {type(exc).__name__}",
            ) from exc

    def get_transcript(
        self,
        video_id_or_url: str,
        *,
        languages: tuple[str, ...] = ("en", "ar"),
        offset: int = 0,
        max_chars: int = 8_000,
    ) -> YouTubeTranscriptReport:
        if offset < 0:
            raise UltimateMCPError(ErrorCode.INVALID_INPUT, "offset must be >= 0.")
        if max_chars < 64 or max_chars > 20_000:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                "max_chars must be between 64 and 20000.",
            )
        vid = extract_youtube_video_id(video_id_or_url)
        if not vid:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                f"Could not extract a valid 11-character YouTube video ID from "
                f"'{video_id_or_url}'.",
            )

        lang, is_gen, segments = self._fetch_raw_transcript(vid, languages)
        full_text = " ".join(segments).strip()
        if not full_text:
            raise UltimateMCPError(
                ErrorCode.NOT_FOUND,
                f"No transcript text available for video '{vid}'.",
            )
        total_chars = len(full_text)
        sliced = full_text[offset : offset + max_chars]
        end_offset = offset + len(sliced)
        truncated = end_offset < total_chars
        next_offset = end_offset if truncated else None

        return YouTubeTranscriptReport(
            video_id=vid,
            url=f"https://youtu.be/{vid}",
            language=lang,
            is_generated=is_gen,
            text=sliced,
            total_chars=total_chars,
            start_offset=offset,
            end_offset=end_offset,
            truncated=truncated,
            next_offset=next_offset,
            segment_count=len(segments),
            provider="youtube_transcript_api",
            retrieved_at=datetime.now(UTC).isoformat(),
        )

    def get_comments(
        self,
        video_id_or_url: str,
        *,
        max_results: int = 10,
    ) -> list[YouTubeCommentItem]:
        vid = extract_youtube_video_id(video_id_or_url)
        if not vid:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                f"Could not extract a valid 11-character YouTube video ID from "
                f"'{video_id_or_url}'.",
            )
        capped = max(1, min(max_results, 20))
        client = self._build_api_client()
        try:
            req = client.commentThreads().list(
                part="snippet",
                videoId=vid,
                maxResults=capped,
                textFormat="plainText",
            )
            response = req.execute()
        except UltimateMCPError:
            raise
        except Exception as exc:
            msg = str(exc)
            if "commentsdisabled" in msg.lower():
                raise UltimateMCPError(
                    ErrorCode.PERMISSION_DENIED,
                    f"Comments are disabled on video '{vid}'.",
                ) from exc
            if "quota" in msg.lower() or "429" in msg:
                raise UltimateMCPError(
                    ErrorCode.RATE_LIMITED,
                    "YouTube Data API quota exceeded.",
                ) from exc
            raise UltimateMCPError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"YouTube comments retrieval failed: {type(exc).__name__}",
            ) from exc

        comments: list[YouTubeCommentItem] = []
        for item in (response or {}).get("items", []):
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or "")
            top = ((item.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
            if not isinstance(top, dict):
                continue
            text = " ".join(str(top.get("textOriginal") or top.get("textDisplay") or "").split())
            if not text:
                continue
            comments.append(
                YouTubeCommentItem(
                    comment_id=cid,
                    author=str(top.get("authorDisplayName") or "anonymous"),
                    text=text[:600],
                    like_count=int(top.get("likeCount") or 0),
                    published_at=str(top.get("publishedAt") or "") or None,
                )
            )
        return comments
