"""Characterization: get_market_price, get_weather, read_article."""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pandas as pd
import pytest

LEGACY_OUTPUT_CAP = 4000


class FakeTicker:
    created: list[str] = []

    def __init__(self, symbol: str, frame: pd.DataFrame) -> None:
        self.symbol = symbol
        self._frame = frame

    def history(self, period: str) -> pd.DataFrame:
        assert period == "1d"
        return self._frame


def _patch_ticker(monkeypatch, legacy: ModuleType, frame: pd.DataFrame) -> list[str]:
    seen: list[str] = []

    def factory(symbol: str) -> FakeTicker:
        seen.append(symbol)
        return FakeTicker(symbol, frame)

    monkeypatch.setattr(legacy.yf, "Ticker", factory)
    return seen


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def _patch_httpx(monkeypatch, legacy, response=None, error=None) -> list[tuple]:
    calls: list[tuple] = []

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        calls.append((url, kwargs))
        if error:
            raise error
        return response

    monkeypatch.setattr(legacy.httpx, "get", fake_get)
    return calls


@pytest.mark.parametrize(
    ("raw", "resolved"),
    [
        ("aapl", "AAPL"),
        ("BTC-USD", "BTC-USD"),
        ("usd-egp", "USDEGP=X"),
        ("EUR-USD", "EURUSD=X"),
        ("EGP=X", "EGP=X"),
    ],
)
def test_market_price_should_rewrite_symbols(legacy, monkeypatch, raw, resolved) -> None:
    seen = _patch_ticker(monkeypatch, legacy, pd.DataFrame({"Close": [1.0, 50.456]}))
    out = legacy.get_market_price(raw)
    assert seen == [resolved]
    assert out == f"Live price for {resolved} is: 50.46"


def test_market_price_should_report_empty_history(legacy, monkeypatch) -> None:
    _patch_ticker(monkeypatch, legacy, pd.DataFrame())
    assert legacy.get_market_price("NOPE") == "Cannot fetch price for symbol (NOPE)."


def test_market_price_should_return_error_text(legacy, monkeypatch) -> None:
    def boom(symbol: str) -> None:
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(legacy.yf, "Ticker", boom)
    assert legacy.get_market_price("AAPL") == "Financial error: yahoo down"


def test_weather_should_quote_location_and_format(legacy, monkeypatch) -> None:
    calls = _patch_httpx(monkeypatch, legacy, FakeResponse(200, "Cairo: Sunny +30C"))
    out = legacy.get_weather(" New York ")
    assert out == "Current Weather:\nCairo: Sunny +30C"
    url, kwargs = calls[0]
    assert url.startswith("https://wttr.in/New%20York?format=")
    assert kwargs == {"timeout": 10.0}


def test_weather_should_report_non_200(legacy, monkeypatch) -> None:
    _patch_httpx(monkeypatch, legacy, FakeResponse(503, "busy"))
    assert legacy.get_weather("Cairo") == "Weather server down."


def test_weather_should_return_error_text(legacy, monkeypatch) -> None:
    _patch_httpx(monkeypatch, legacy, error=TimeoutError("timed out"))
    assert legacy.get_weather("Cairo") == "Weather error: timed out"


def test_read_article_should_extract_paragraphs(legacy, monkeypatch) -> None:
    html = "<html><p> First </p><div>skip</div><p></p><p>Second</p></html>"
    calls = _patch_httpx(monkeypatch, legacy, FakeResponse(200, html))
    assert legacy.read_article("https://example.com/a") == "First\nSecond"
    _, kwargs = calls[0]
    assert kwargs["follow_redirects"] is True
    assert kwargs["timeout"] == 15.0


def test_read_article_should_not_validate_destination(legacy, monkeypatch) -> None:
    """SSRF risk: private/metadata addresses are passed straight to httpx."""
    calls = _patch_httpx(monkeypatch, legacy, FakeResponse(200, "<p>secret</p>"))
    assert legacy.read_article("http://169.254.169.254/latest") == "secret"
    assert calls[0][0] == "http://169.254.169.254/latest"


def test_read_article_should_ignore_http_status(legacy, monkeypatch) -> None:
    _patch_httpx(monkeypatch, legacy, FakeResponse(404, "<p>Not Found page</p>"))
    assert legacy.read_article("https://example.com/missing") == "Not Found page"


def test_read_article_should_truncate_with_marker(legacy, monkeypatch) -> None:
    _patch_httpx(monkeypatch, legacy, FakeResponse(200, f"<p>{'y' * 5000}</p>"))
    out = legacy.read_article("https://example.com/long")
    assert out == "y" * LEGACY_OUTPUT_CAP + "\n\n(Rest of content truncated)"


def test_read_article_should_return_error_text(legacy, monkeypatch) -> None:
    _patch_httpx(monkeypatch, legacy, error=ValueError("bad url"))
    assert legacy.read_article("notaurl!") == "Could not read the link: bad url"
