from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    exchange: str = ""
    quote_type: str = ""
    currency: str = ""
    source: str = "Yahoo Finance"
    figi: str = ""
    market_cap: float | None = None
    aum: float | None = None
    isin: str = ""

    @property
    def display_name(self) -> str:
        name = self.name or self.symbol
        venue = f" | {self.exchange}" if self.exchange else ""
        asset_type = f" | {self.quote_type}" if self.quote_type else ""
        return f"{self.symbol} | {name}{venue}{asset_type}"


@dataclass(frozen=True)
class RangeSpec:
    label: str
    period: str
    interval: str
    start: str = ""
    end: str = ""


@dataclass(frozen=True)
class MarketSession:
    status: str = "Session unavailable"
    exchange_timezone: str = ""
    regular_exchange_hours: str = ""
    regular_local_hours: str = ""
    extended_session: str = "Extended-hours availability unavailable"
    overnight_session: str = "Overnight availability not indicated"


@dataclass(frozen=True)
class QuoteSnapshot:
    last: float | None = None
    bid: float | None = None
    ask: float | None = None
    change: float | None = None
    change_percent: float | None = None
    volume: float | None = None
    market_state: str = ""


INTRADAY_MATRIX = (
    (
        "1D",
        (
            RangeSpec("1D / 1m", "1d", "1m"),
            RangeSpec("1D / 5m", "1d", "5m"),
            RangeSpec("1D / 15m", "1d", "15m"),
            RangeSpec("1D / 30m", "1d", "30m"),
            RangeSpec("1D / 60m", "1d", "60m"),
        ),
    ),
    (
        "5D",
        (
            RangeSpec("5D / 1m", "5d", "1m"),
            RangeSpec("5D / 5m", "5d", "5m"),
            RangeSpec("5D / 15m", "5d", "15m"),
            RangeSpec("5D / 30m", "5d", "30m"),
            RangeSpec("5D / 60m", "5d", "60m"),
        ),
    ),
    (
        "1M",
        (
            RangeSpec("1M / 5m", "1mo", "5m"),
            RangeSpec("1M / 15m", "1mo", "15m"),
            RangeSpec("1M / 30m", "1mo", "30m"),
            RangeSpec("1M / 60m", "1mo", "60m"),
        ),
    ),
)

INTRADAY_RANGES = tuple(spec for _duration, specs in INTRADAY_MATRIX for spec in specs)

HISTORICAL_RANGES = (
    RangeSpec("3M", "3mo", "1d"),
    RangeSpec("6M", "6mo", "1d"),
    RangeSpec("YTD", "ytd", "1d"),
    RangeSpec("1Y", "1y", "1d"),
    RangeSpec("5Y", "5y", "1wk"),
    RangeSpec("MAX", "max", "1mo"),
)
