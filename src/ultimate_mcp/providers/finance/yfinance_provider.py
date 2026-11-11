"""Yahoo Finance (`yfinance`) market quote and currency exchange rate adapter.

Follows ``phases/PHASE-02_RESEARCH_UTILITIES.md`` §E.2 and ``docs/MIGRATION_PLAN.md``:
- Removes the legacy fragile heuristic ticker rewriting (`if "-" in symbol and any(...)`).
- Accepts Yahoo Finance symbols explicitly (e.g. `AAPL`, `BTC-USD`, `EGP=X`, `EURUSD=X`)
  plus a tiny, explicitly documented alias table (`GOLD` -> `GC=F`, `SILVER` -> `SI=F`,
  `USD/EGP` -> `EGP=X`).
- Never claims "Live" or exchange real-time pricing without qualification; returns
  currency, provider timestamp, exchange, market state, and freshness caveat.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ultimate_mcp.core.models import ErrorCode, UltimateMCPError
from ultimate_mcp.core.registry import is_package_available
from ultimate_mcp.providers.base import ExchangeRateQuote, MarketQuote, ProviderHealth

SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.^=\-]{1,24}$")
CURRENCY_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")

EXPLICIT_SYMBOL_ALIASES: dict[str, str] = {
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "USD/EGP": "EGP=X",
    "USDEGP": "EGP=X",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
}

RawTickerFetcher = Callable[[str], dict[str, Any]]


def normalize_market_symbol(raw_symbol: str) -> str:
    """Validate and normalize a market symbol using only explicit, tested aliases."""
    cleaned = raw_symbol.strip().upper()
    if not cleaned:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Ticker symbol must not be empty.")
    resolved = EXPLICIT_SYMBOL_ALIASES.get(cleaned, cleaned)
    if not SYMBOL_PATTERN.fullmatch(resolved):
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Invalid ticker symbol format '{raw_symbol}'. Expected standard Yahoo Finance "
            "symbol such as 'AAPL', 'BTC-USD', 'EGP=X', or 'GC=F'.",
        )
    return resolved


def _default_yfinance_fetcher(symbol: str) -> dict[str, Any]:
    try:
        yf: Any = importlib.import_module("yfinance")
    except ImportError as exc:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Optional package 'yfinance' is not installed (install `[market]`).",
        ) from exc

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d")
        if hist is None or getattr(hist, "empty", True):
            return {"empty": True}

        close_series = hist["Close"].dropna()
        if len(close_series) == 0:
            return {"empty": True}

        last_price = float(close_series.iloc[-1])
        prev_close = float(close_series.iloc[-2]) if len(close_series) >= 2 else None
        last_idx = close_series.index[-1]
        if hasattr(last_idx, "isoformat"):
            quote_ts = str(last_idx.isoformat())
        else:
            quote_ts = str(last_idx)

        fast_info = getattr(ticker, "fast_info", None) or {}
        currency = str(fast_info.get("currency") or "UNKNOWN")
        exchange = str(fast_info.get("exchange") or "UNKNOWN")
        market_state = "LAST_AVAILABLE_CLOSE"
        return {
            "empty": False,
            "price": last_price,
            "previous_close": prev_close,
            "currency": currency,
            "exchange": exchange,
            "market_state": market_state,
            "quote_timestamp": quote_ts,
        }
    except UltimateMCPError:
        raise
    except Exception as exc:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            f"Yahoo Finance query failed for '{symbol}': {type(exc).__name__}",
            retryable=True,
        ) from exc


class YFinanceMarketProvider:
    """Market data and FX provider backed by ``yfinance``."""

    def __init__(self, fetcher: RawTickerFetcher | None = None) -> None:
        self._fetcher = fetcher or _default_yfinance_fetcher

    @property
    def name(self) -> str:
        return "yfinance"

    def health(self) -> ProviderHealth:
        has_yf = is_package_available("yfinance")
        return ProviderHealth(
            provider=self.name,
            available=has_yf,
            requires_api_key=False,
            api_key_configured=True,
            optional_packages=("yfinance",),
            missing_packages=() if has_yf else ("yfinance",),
            reason="ready" if has_yf else "missing optional package: yfinance",
        )

    def get_quote(self, symbol: str) -> MarketQuote:
        resolved = normalize_market_symbol(symbol)
        raw = self._fetcher(resolved)
        if raw.get("empty", True) or "price" not in raw:
            raise UltimateMCPError(
                ErrorCode.NOT_FOUND,
                f"No market quote available for symbol '{resolved}'.",
            )

        price = round(float(raw["price"]), 6)
        prev = raw.get("previous_close")
        prev_val = round(float(prev), 6) if prev is not None else None
        change_pct: float | None = None
        if prev_val and prev_val != 0:
            change_pct = round(((price - prev_val) / prev_val) * 100.0, 4)

        return MarketQuote(
            symbol=resolved,
            price=price,
            currency=str(raw.get("currency") or "UNKNOWN"),
            market_state=str(raw.get("market_state") or "LAST_AVAILABLE_CLOSE"),
            exchange=str(raw.get("exchange") or "UNKNOWN"),
            quote_timestamp=str(raw.get("quote_timestamp") or "unknown"),
            provider=self.name,
            retrieved_at=datetime.now(UTC).isoformat(),
            previous_close=prev_val,
            change_percent=change_pct,
        )

    def get_exchange_rate(
        self,
        base_currency: str,
        target_currency: str,
        *,
        amount: float = 1.0,
    ) -> ExchangeRateQuote:
        if amount < 0:
            raise UltimateMCPError(ErrorCode.INVALID_INPUT, "amount must be >= 0.")
        base = base_currency.strip().upper()
        target = target_currency.strip().upper()
        if not CURRENCY_CODE_PATTERN.fullmatch(base) or not CURRENCY_CODE_PATTERN.fullmatch(target):
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                "Currency codes must be 3-letter ISO 4217 codes (e.g. 'USD', 'EGP', 'EUR').",
            )

        if base == target:
            now_iso = datetime.now(UTC).isoformat()
            return ExchangeRateQuote(
                base_currency=base,
                target_currency=target,
                symbol=f"{base}{target}=X",
                rate=1.0,
                amount=amount,
                converted_amount=round(amount, 6),
                quote_timestamp=now_iso,
                provider=self.name,
                retrieved_at=now_iso,
            )

        fx_symbol = "EGP=X" if (base == "USD" and target == "EGP") else f"{base}{target}=X"
        quote = self.get_quote(fx_symbol)
        converted = round(amount * quote.price, 6)
        return ExchangeRateQuote(
            base_currency=base,
            target_currency=target,
            symbol=quote.symbol,
            rate=quote.price,
            amount=amount,
            converted_amount=converted,
            quote_timestamp=quote.quote_timestamp,
            provider=self.name,
            retrieved_at=quote.retrieved_at,
        )
