"""Characterization: web_search (Tavily) and limited_web_search (DuckDuckGo)."""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest

LEGACY_OUTPUT_CAP = 4000


class FakeTavily:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def search(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeDDGS:
    def __init__(self, news: list[dict], text: list[dict], error: Exception | None = None):
        self._news, self._text, self._error = news, text, error
        self.news_calls: list[tuple] = []
        self.text_calls: list[tuple] = []

    def __call__(self) -> FakeDDGS:
        return self

    def __enter__(self) -> FakeDDGS:
        if self._error:
            raise self._error
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def news(self, query: str, **kwargs: Any) -> list[dict]:
        self.news_calls.append((query, kwargs))
        return self._news

    def text(self, query: str, **kwargs: Any) -> list[dict]:
        self.text_calls.append((query, kwargs))
        return self._text


def test_web_search_should_format_results(legacy: ModuleType, monkeypatch) -> None:
    fake = FakeTavily({"results": [{"title": "T1", "content": "C1", "url": "https://a"}]})
    monkeypatch.setattr(legacy, "tavily", fake)
    out = legacy.web_search("python release")
    assert out == "Professional Search Results:\n---\n- T1\nC1\nURL: https://a"
    assert fake.calls == [
        {"query": "python release", "search_depth": "advanced", "max_results": 4}
    ]


def test_web_search_should_return_error_text_on_failure(legacy, monkeypatch) -> None:
    monkeypatch.setattr(legacy, "tavily", FakeTavily(error=RuntimeError("401")))
    out = legacy.web_search("x query")
    assert out == "Error in advanced search (check API Key): 401"


def test_web_search_should_return_error_text_on_malformed_response(legacy, monkeypatch):
    monkeypatch.setattr(legacy, "tavily", FakeTavily({"unexpected": []}))
    assert legacy.web_search("x query").startswith("Error in advanced search")


@pytest.mark.parametrize("query", ["latest gpu", "today weather"])
def test_limited_search_should_trigger_news_on_keywords(legacy, monkeypatch, query) -> None:
    news = [{"title": "N", "date": "2026-01-01", "body": "B", "href": "https://n"}]
    fake = FakeDDGS(news=news, text=[])
    monkeypatch.setattr(legacy, "DDGS", fake)
    out = legacy.limited_web_search(query, "general")
    assert "[News Section]:" in out
    assert fake.news_calls[0][1] == {"max_results": 4}
    assert fake.text_calls[0][1] == {"max_results": 3, "timelimit": "w"}


def test_limited_search_general_mode_skips_news(legacy, monkeypatch) -> None:
    text = [{"title": "G", "body": "B", "href": "https://g"}]
    fake = FakeDDGS(news=[], text=text)
    monkeypatch.setattr(legacy, "DDGS", fake)
    out = legacy.limited_web_search("rust ownership", "general")
    assert fake.news_calls == []
    assert out == "Recent Search Results:\n\n[General Search - Last Week]:\n- G\nB\nURL: https://g\n\n"


def test_limited_search_should_report_empty(legacy, monkeypatch) -> None:
    monkeypatch.setattr(legacy, "DDGS", FakeDDGS(news=[], text=[]))
    out = legacy.limited_web_search("rust ownership")
    assert out == "Not enough recent information found, try altering the keywords."


def test_limited_search_should_silently_truncate(legacy, monkeypatch) -> None:
    text = [{"title": "G", "body": "x" * 5000, "href": "https://g"}]
    monkeypatch.setattr(legacy, "DDGS", FakeDDGS(news=[], text=text))
    out = legacy.limited_web_search("rust ownership")
    assert len(out) == LEGACY_OUTPUT_CAP
    assert "truncated" not in out


def test_limited_search_should_return_error_text(legacy, monkeypatch) -> None:
    monkeypatch.setattr(legacy, "DDGS", FakeDDGS([], [], error=RuntimeError("rate limit")))
    assert legacy.limited_web_search("rust ownership") == "Search engine error: rate limit"
