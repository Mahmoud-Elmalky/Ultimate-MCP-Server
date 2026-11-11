"""Characterization: youtube_expert and calculate_or_time."""

from __future__ import annotations

import re
from types import ModuleType
from typing import Any

import pytest

LEGACY_OUTPUT_CAP = 4000
VIDEO_ID = "dQw4w9WgXcQ"


class FakeRequest:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def execute(self) -> dict:
        return self._payload


class FakeYouTube:
    def __init__(self, search: dict | None = None, comments: dict | None = None) -> None:
        self._search, self._comments = search or {}, comments or {}
        self.calls: list[tuple[str, dict]] = []

    def search(self) -> FakeYouTube:
        return self

    def commentThreads(self) -> FakeYouTube:  # noqa: N802 - mirrors Google client API
        self._mode = "comments"
        return self

    def list(self, **kwargs: Any) -> FakeRequest:
        mode = getattr(self, "_mode", "search")
        self.calls.append((mode, kwargs))
        return FakeRequest(self._comments if mode == "comments" else self._search)


def _patch_build(monkeypatch, legacy: ModuleType, fake: FakeYouTube) -> list[tuple]:
    built: list[tuple] = []

    def fake_build(service: str, version: str, developerKey: str | None) -> FakeYouTube:  # noqa: N803
        built.append((service, version, developerKey))
        return fake

    monkeypatch.setattr(legacy, "build", fake_build)
    return built


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (f"https://www.youtube.com/watch?v={VIDEO_ID}", VIDEO_ID),
        (f"https://youtu.be/{VIDEO_ID}", VIDEO_ID),
        (f"https://youtube.com/shorts/{VIDEO_ID}", VIDEO_ID),
        (f"https://youtube.com/embed/{VIDEO_ID}?t=3", VIDEO_ID),
        ("python asyncio tutorial", None),
    ],
)
def test_extract_video_id(legacy, value, expected) -> None:
    assert legacy.extract_video_id(value) == expected


def test_youtube_search_should_list_videos(legacy, monkeypatch) -> None:
    payload = {"items": [{"snippet": {"title": "Vid"}, "id": {"videoId": VIDEO_ID}}]}
    fake = FakeYouTube(search=payload)
    built = _patch_build(monkeypatch, legacy, fake)
    out = legacy.youtube_expert("asyncio tutorial")
    assert out == f"YouTube Results:\n- Vid\nURL: https://youtu.be/{VIDEO_ID}\n"
    assert built == [("youtube", "v3", "test-youtube-key")]
    assert fake.calls[0][1]["maxResults"] == 3


def test_youtube_comments_should_join_top_comments(legacy, monkeypatch) -> None:
    items = [
        {"snippet": {"topLevelComment": {"snippet": {"textOriginal": t}}}} for t in ("a", "b")
    ]
    _patch_build(monkeypatch, legacy, FakeYouTube(comments={"items": items}))
    out = legacy.youtube_expert(f"https://youtu.be/{VIDEO_ID}", "comments")
    assert out == "Top Comments:\na\n---\nb"


def test_youtube_transcript_is_broken_on_current_library(legacy) -> None:
    """youtube-transcript-api 1.x removed the static get_transcript(); legacy path fails."""
    out = legacy.youtube_expert(f"https://youtu.be/{VIDEO_ID}")
    assert out.startswith("YouTube error:")
    assert "get_transcript" in out


def test_youtube_transcript_legacy_contract_when_api_present(legacy, monkeypatch) -> None:
    def fake_get_transcript(video_id: str, languages: list[str]) -> list[dict]:
        assert languages == ["ar", "en"]
        return [{"text": "z" * 3000}, {"text": "w" * 3000}]

    monkeypatch.setattr(
        legacy.YouTubeTranscriptApi, "get_transcript", fake_get_transcript, raising=False
    )
    out = legacy.youtube_expert(f"https://youtu.be/{VIDEO_ID}")
    assert out.startswith("Video Summary:\n")
    assert len(out) == len("Video Summary:\n") + LEGACY_OUTPUT_CAP


@pytest.mark.parametrize(
    ("expr", "expected"),
    [("2+3*4", "Result: 14"), ("sqrt(16)", "Result: 4.0"), ("pi", f"Result: {3.141592653589793}")],
)
def test_calculator_should_evaluate(legacy, expr, expected) -> None:
    assert legacy.calculate_or_time("math", expr) == expected


def test_calculator_should_return_error_text(legacy) -> None:
    assert legacy.calculate_or_time("math", "1/0") == "Error: division by zero"


def test_calculator_eval_allows_attribute_access(legacy) -> None:
    """Security risk: eval() permits dunder attribute traversal on literals."""
    out = legacy.calculate_or_time("math", "(1).__class__.__name__")
    assert out == "Result: int"


def test_calculator_math_with_default_value_errors(legacy) -> None:
    out = legacy.calculate_or_time("math")
    assert out.startswith("Error:")


def test_time_should_use_default_timezone(legacy) -> None:
    out = legacy.calculate_or_time("time")
    assert re.fullmatch(
        r"Time in Africa/Cairo: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (AM|PM)", out
    )


def test_time_should_report_unknown_timezone(legacy) -> None:
    assert legacy.calculate_or_time("time", "Mars/Phobos") == "Error: 'Mars/Phobos'"
