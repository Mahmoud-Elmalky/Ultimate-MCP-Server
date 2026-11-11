"""Deterministic offline tests for Phase 02 utilities, weather, market, and YouTube providers."""

from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest

from ultimate_mcp.core.cache import PerClassTTLCache
from ultimate_mcp.core.http import SafeHttpResponse
from ultimate_mcp.core.models import ErrorCode, ResultStatus, UltimateMCPError
from ultimate_mcp.modules.research.live_data_service import (
    get_exchange_rate_service,
    get_market_price_service,
    get_weather_service,
    legacy_calculate_or_time_service,
    legacy_youtube_expert_service,
    youtube_comments_service,
    youtube_search_service,
    youtube_transcript_service,
)
from ultimate_mcp.modules.utilities.units import convert_units_service
from ultimate_mcp.providers.finance.yfinance_provider import (
    YFinanceMarketProvider,
    _default_yfinance_fetcher,
    normalize_market_symbol,
)
from ultimate_mcp.providers.weather.wttr import WttrWeatherProvider, _default_wttr_fetcher
from ultimate_mcp.providers.youtube.adapter import (
    DefaultYouTubeProvider,
    extract_youtube_video_id,
)


def _fake_http_response(url: str, payload: Any) -> SafeHttpResponse:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    raw = text.encode("utf-8")
    return SafeHttpResponse(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="application/json",
        headers={"content-type": "application/json"},
        body=raw,
        text=text,
        redirect_count=0,
        resolved_ips=("93.184.216.34",),
        retrieved_at="2026-11-11T19:00:00+00:00",
    )


def test_convert_units_across_all_categories() -> None:
    km_to_mi = convert_units_service(10.0, "km", "mi")
    assert km_to_mi.status == ResultStatus.SUCCESS
    assert km_to_mi.data["category"] == "length"
    assert pytest.approx(km_to_mi.data["converted_value"], rel=1e-4) == 6.21371

    lb_to_kg = convert_units_service(2.20462262, "lb", "kg")
    assert pytest.approx(lb_to_kg.data["converted_value"], rel=1e-5) == 1.0

    c_to_f = convert_units_service(100.0, "c", "f")
    assert c_to_f.data["converted_value"] == 212.0

    f_to_k = convert_units_service(32.0, "fahrenheit", "kelvin")
    assert pytest.approx(f_to_k.data["converted_value"], rel=1e-5) == 273.15

    k_to_c = convert_units_service(273.15, "k", "c")
    assert pytest.approx(k_to_c.data["converted_value"], abs=1e-5) == 0.0

    c_to_c = convert_units_service(25.0, "c", "celsius")
    assert c_to_c.data["converted_value"] == 25.0

    gb_to_mb = convert_units_service(1.5, "gb", "mb")
    assert gb_to_mb.data["converted_value"] == 1500.0

    hr_to_s = convert_units_service(2.0, "hr", "s")
    assert hr_to_s.data["converted_value"] == 7200.0


def test_convert_units_validation_errors() -> None:
    with pytest.raises(UltimateMCPError) as exc_abs:
        convert_units_service(-300.0, "c", "k")
    assert exc_abs.value.code == ErrorCode.INVALID_INPUT

    with pytest.raises(UltimateMCPError) as exc_cat:
        convert_units_service(10.0, "km", "kg")
    assert exc_cat.value.code == ErrorCode.INVALID_INPUT

    with pytest.raises(UltimateMCPError) as exc_unk:
        convert_units_service(10.0, "parsec", "km")
    assert exc_unk.value.code == ErrorCode.INVALID_INPUT

    with pytest.raises(UltimateMCPError) as exc_nan:
        convert_units_service(float("nan"), "km", "m")
    assert exc_nan.value.code == ErrorCode.INVALID_INPUT


def test_wttr_weather_provider_and_service_with_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    sample_payload = {
        "current_condition": [
            {
                "temp_C": "26",
                "temp_F": "79",
                "FeelsLikeC": "27",
                "FeelsLikeF": "81",
                "humidity": "48",
                "windspeedKmph": "15",
                "winddir16Point": "NNE",
                "weatherDesc": [{"value": "Partly cloudy"}],
                "localObsDateTime": "2026-11-11 08:00 PM",
            }
        ],
        "nearest_area": [
            {
                "areaName": [{"value": "Cairo"}],
                "country": [{"value": "Egypt"}],
            }
        ],
        "weather": [
            {
                "date": "2026-11-11",
                "mintempC": "18",
                "maxtempC": "27",
                "hourly": [{"weatherDesc": [{"value": "Sunny"}]}],
            }
        ],
    }
    calls: list[str] = []

    def fake_fetcher(loc: str) -> dict[str, Any]:
        calls.append(loc)
        return sample_payload

    prov = WttrWeatherProvider(fetcher=fake_fetcher)
    assert prov.health().api_key_configured is True

    cache: PerClassTTLCache[Any] = PerClassTTLCache()
    res1 = get_weather_service("Cairo", provider=prov, include_forecast=True, cache=cache)
    assert res1.status == ResultStatus.SUCCESS
    assert res1.data["resolved_area"] == "Cairo, Egypt"
    assert res1.data["temperature_c"] == 26.0
    assert res1.data["cached"] is False
    assert len(res1.data["forecast_days"]) == 1
    assert len(calls) == 1

    res2 = get_weather_service("Cairo", provider=prov, include_forecast=True, cache=cache)
    assert res2.data["cached"] is True
    assert len(calls) == 1
    assert "Cached result from" in (res2.provenance[0].freshness_note or "")

    # Test _default_wttr_fetcher with mocked fetch_safe_url
    monkeypatch.setattr(
        "ultimate_mcp.providers.weather.wttr.fetch_safe_url",
        lambda url, **kw: _fake_http_response(url, sample_payload),
    )
    parsed_default = _default_wttr_fetcher("Cairo")
    assert parsed_default["current_condition"][0]["temp_C"] == "26"


def test_wttr_weather_provider_error_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ultimate_mcp.providers.weather.wttr.fetch_safe_url",
        lambda url, **kw: _fake_http_response(url, "{not-json"),
    )
    bad_json_prov = WttrWeatherProvider()
    with pytest.raises(UltimateMCPError) as exc_json:
        bad_json_prov.get_weather("Cairo")
    assert exc_json.value.code == ErrorCode.UPSTREAM_INVALID_RESPONSE

    empty_cond_prov = WttrWeatherProvider(fetcher=lambda loc: {"current_condition": []})
    with pytest.raises(UltimateMCPError) as exc_nf:
        empty_cond_prov.get_weather("Atlantis")
    assert exc_nf.value.code == ErrorCode.NOT_FOUND

    with pytest.raises(UltimateMCPError) as exc_in:
        empty_cond_prov.get_weather("   ")
    assert exc_in.value.code == ErrorCode.INVALID_INPUT


def test_market_symbol_normalization_and_yfinance_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert normalize_market_symbol("gold") == "GC=F"
    assert normalize_market_symbol("usd/egp") == "EGP=X"
    assert normalize_market_symbol("btc-usd") == "BTC-USD"
    assert normalize_market_symbol("aapl") == "AAPL"

    with pytest.raises(UltimateMCPError) as exc_bad:
        normalize_market_symbol("AAPL; DROP TABLE")
    assert exc_bad.value.code == ErrorCode.INVALID_INPUT

    def fake_raw_fetcher(symbol: str) -> dict[str, Any]:
        return {
            "empty": False,
            "price": 225.5 if symbol != "EGP=X" else 49.25,
            "previous_close": 220.0 if symbol != "EGP=X" else 49.0,
            "currency": "USD" if symbol != "EGP=X" else "EGP",
            "exchange": "NMS",
            "market_state": "REGULAR",
            "quote_timestamp": "2026-11-11T16:00:00Z",
        }

    prov = YFinanceMarketProvider(fetcher=fake_raw_fetcher)
    assert prov.health().api_key_configured is True
    cache: PerClassTTLCache[Any] = PerClassTTLCache()

    q_res = get_market_price_service("AAPL", provider=prov, cache=cache)
    assert q_res.status == ResultStatus.SUCCESS
    assert q_res.data["symbol"] == "AAPL"
    assert q_res.data["price"] == 225.5
    assert q_res.data["currency"] == "USD"
    assert q_res.data["market_state"] == "REGULAR"
    assert "Delayed or last-available" in q_res.data["freshness_caveat"]

    # Cached call
    q_cached = get_market_price_service("AAPL", provider=prov, cache=cache)
    assert q_cached.data["cached"] is True

    # Exchange rate
    fx_res = get_exchange_rate_service("USD", "EGP", amount=100.0, provider=prov, cache=cache)
    assert fx_res.data["symbol"] == "EGP=X"
    assert fx_res.data["converted_amount"] == 4925.0

    fx_cached = get_exchange_rate_service("USD", "EGP", amount=100.0, provider=prov, cache=cache)
    assert fx_cached.data["cached"] is True

    # Same currency shortcut
    same_fx = get_exchange_rate_service("USD", "USD", amount=50.0, provider=prov)
    assert same_fx.data["rate"] == 1.0
    assert same_fx.data["converted_amount"] == 50.0

    # Test _default_yfinance_fetcher with fake yfinance module
    fake_yf = types.ModuleType("yfinance")

    class FakeSeries:
        index = ["2026-11-10", "2026-11-11"]
        iloc = [100.0, 105.0]

        def dropna(self) -> FakeSeries:
            return self

        def __len__(self) -> int:
            return 2

    class FakeHist:
        empty = False

        def __getitem__(self, key: str) -> FakeSeries:
            return FakeSeries()

    class FakeTickerObj:
        def __init__(self, sym: str) -> None:
            self.fast_info = {"currency": "USD", "exchange": "NYSE"}

        def history(self, period: str = "5d") -> FakeHist:
            return FakeHist()

    fake_yf.Ticker = FakeTickerObj  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    raw_yf = _default_yfinance_fetcher("IBM")
    assert raw_yf["price"] == 105.0
    assert raw_yf["previous_close"] == 100.0


def test_yfinance_provider_empty_quote_and_invalid_fx() -> None:
    prov = YFinanceMarketProvider(fetcher=lambda sym: {"empty": True})
    with pytest.raises(UltimateMCPError) as exc_nf:
        prov.get_quote("UNKNOWN")
    assert exc_nf.value.code == ErrorCode.NOT_FOUND

    with pytest.raises(UltimateMCPError) as exc_cur:
        prov.get_exchange_rate("US", "EGP", amount=10.0)
    assert exc_cur.value.code == ErrorCode.INVALID_INPUT

    with pytest.raises(UltimateMCPError) as exc_amt:
        prov.get_exchange_rate("USD", "EGP", amount=-5.0)
    assert exc_amt.value.code == ErrorCode.INVALID_INPUT


def test_youtube_video_id_extraction_and_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    assert extract_youtube_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_youtube_video_id("https://youtu.be/dQw4w9WgXcQ?t=10") == "dQw4w9WgXcQ"
    assert (
        extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&ab=1")
        == "dQw4w9WgXcQ"
    )
    assert (
        extract_youtube_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    )
    assert extract_youtube_video_id("python tutorial for beginners") is None

    # Missing API key raises AUTH_REQUIRED for search/comments
    unauth_prov = DefaultYouTubeProvider(api_key=None)
    assert unauth_prov.health().api_key_configured is False
    with pytest.raises(UltimateMCPError) as exc_auth:
        unauth_prov.search_videos("mcp server")
    assert exc_auth.value.code == ErrorCode.AUTH_REQUIRED

    # Test 1.2.x youtube-transcript-api instance `.fetch()` via fake module
    @dataclass
    class FakeSnippet:
        text: str

    class FakeFetchedTranscript:
        language_code = "en"
        is_generated = False

        def __iter__(self):
            yield FakeSnippet("Hello world from YouTube. " * 4)
            yield FakeSnippet("Second caption line with extra words for pagination.")

    class FakeTranscriptApi:
        def fetch(self, video_id: str, languages: list[str]) -> FakeFetchedTranscript:
            return FakeFetchedTranscript()

    fake_yt_mod = types.ModuleType("youtube_transcript_api")
    fake_yt_mod.YouTubeTranscriptApi = FakeTranscriptApi  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_yt_mod)

    class FakeYouTubeClient:
        def search(self) -> FakeYouTubeClient:
            return self

        def commentThreads(self) -> FakeYouTubeClient:
            return self

        def list(self, **kwargs: Any) -> FakeYouTubeClient:
            self._kwargs = kwargs
            return self

        def execute(self) -> dict[str, Any]:
            if "q" in self._kwargs:
                return {
                    "items": [
                        {
                            "id": {"videoId": "dQw4w9WgXcQ"},
                            "snippet": {
                                "title": "MCP Architecture",
                                "channelTitle": "AI Channel",
                                "publishedAt": "2026-01-01T00:00:00Z",
                                "description": "Deep dive",
                            },
                        }
                    ]
                }
            return {
                "items": [
                    {
                        "id": "cmt_1",
                        "snippet": {
                            "topLevelComment": {
                                "snippet": {
                                    "authorDisplayName": "Alice",
                                    "textDisplay": "Great overview!",
                                    "likeCount": 12,
                                    "publishedAt": "2026-01-02T00:00:00Z",
                                }
                            }
                        },
                    }
                ]
            }

    prov = DefaultYouTubeProvider(
        api_key="AIzaFakeKey",
        api_client_factory=lambda k: FakeYouTubeClient(),
    )

    s_res = youtube_search_service("MCP", provider=prov, max_results=3)
    assert s_res.status == ResultStatus.SUCCESS
    assert s_res.data["results"][0]["video_id"] == "dQw4w9WgXcQ"

    t_res = youtube_transcript_service(
        "https://youtu.be/dQw4w9WgXcQ",
        provider=prov,
        offset=0,
        max_chars=64,
    )
    assert t_res.status == ResultStatus.PARTIAL
    assert t_res.metadata.truncated is True
    assert t_res.metadata.next_offset == 64

    c_res = youtube_comments_service("dQw4w9WgXcQ", provider=prov, max_results=5)
    assert c_res.status == ResultStatus.SUCCESS
    assert c_res.data["comments"][0]["author"] == "Alice"

    # Error branches: commentsDisabled, quotaExceeded, TranscriptsDisabled
    class DisabledCommentsClient(FakeYouTubeClient):
        def execute(self) -> dict[str, Any]:
            raise RuntimeError("commentsDisabled for this video")

    disabled_prov = DefaultYouTubeProvider(
        api_key="AIzaFakeKey",
        api_client_factory=lambda k: DisabledCommentsClient(),
        transcript_fetcher=lambda vid, langs: ("en", False, []),
    )
    with pytest.raises(UltimateMCPError) as exc_cd:
        disabled_prov.get_comments("dQw4w9WgXcQ")
    assert exc_cd.value.code == ErrorCode.PERMISSION_DENIED

    with pytest.raises(UltimateMCPError) as exc_empty_tr:
        disabled_prov.get_transcript("dQw4w9WgXcQ")
    assert exc_empty_tr.value.code == ErrorCode.NOT_FOUND

    class QuotaClient(FakeYouTubeClient):
        def execute(self) -> dict[str, Any]:
            raise RuntimeError("quotaExceeded 403")

    quota_prov = DefaultYouTubeProvider(
        api_key="AIzaFakeKey",
        api_client_factory=lambda k: QuotaClient(),
    )
    with pytest.raises(UltimateMCPError) as exc_q:
        quota_prov.search_videos("mcp")
    assert exc_q.value.code == ErrorCode.RATE_LIMITED

    # Legacy wrappers
    calc_wrap = legacy_calculate_or_time_service("math", "6 * 7")
    assert calc_wrap.data["result"] == 42
    assert any("Deprecated" in w for w in calc_wrap.warnings)

    time_wrap = legacy_calculate_or_time_service("time", "UTC")
    assert time_wrap.data["timezone"] == "UTC"

    yt_wrap = legacy_youtube_expert_service("dQw4w9WgXcQ", "auto", provider=prov)
    assert "Deprecated" in yt_wrap.warnings[-1]
    yt_search_wrap = legacy_youtube_expert_service("mcp tutorial", "search", provider=prov)
    assert len(yt_search_wrap.data["results"]) == 1
    yt_cmt_wrap = legacy_youtube_expert_service("dQw4w9WgXcQ", "comments", provider=prov)
    assert len(yt_cmt_wrap.data["comments"]) == 1
