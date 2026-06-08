from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from dataclasses import dataclass, replace
from io import StringIO
from pathlib import Path
from threading import Lock
from time import monotonic, sleep

import pandas as pd
import requests
import yfinance as yf
import yfinance.cache as yf_cache

from .live_quotes import LiveQuoteService
from .models import Instrument, MarketEvent, MarketSession, QuoteSnapshot, RangeSpec
from .portfolio_index import (
    PORTFOLIO_INDEX_SYMBOL,
    load_portfolio_index_history,
    portfolio_market_session,
    search_portfolio_index,
)


_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_CUSIP = re.compile(r"^[A-Z0-9*@#]{8}[0-9*@#]$")
_FIGI = re.compile(r"^BBG[A-Z0-9]{9}$")
_TERMINAL_TICKER = re.compile(
    r"^(\S+)\s+([A-Z]{2})(?:\s+(?:EQUITY|INDEX|COMDTY|CURNCY|CORP|GOVT))?$",
    re.IGNORECASE,
)
_BLOOMBERG_TO_YAHOO_EXCHANGE = {
    "AU": ".AX",
    "BB": ".BR",
    "CN": ".TO",
    "DC": ".CO",
    "FP": ".PA",
    "GY": ".DE",
    "HK": ".HK",
    "ID": ".IR",
    "IM": ".MI",
    "JT": ".T",
    "LN": ".L",
    "NA": ".AS",
    "NO": ".OL",
    "SM": ".MC",
    "SS": ".ST",
    "SW": ".SW",
    "US": "",
}
_EURONEXT_SEARCH_ENDPOINT = "https://live.euronext.com/en/instrumentSearch/searchJSON"
_NFIN_ENDPOINT = "https://api.nfin.dev"
_YAHOO_TO_EURONEXT_MIC = {
    ".AS": "XAMS",
    ".BR": "XBRU",
    ".IR": "XDUB",
    ".MI": "XMIL",
    ".OL": "XOSL",
    ".PA": "XPAR",
}
_BINANCE_QUOTES = (
    "USDT",
    "FDUSD",
    "USDC",
    "BTC",
    "ETH",
    "BNB",
    "EUR",
    "GBP",
    "TRY",
    "BRL",
    "AUD",
)
_BINANCE_DEFAULT_BASES = {
    "BTC",
    "ETH",
    "BNB",
    "SOL",
    "XRP",
    "ADA",
    "DOGE",
    "AVAX",
    "LINK",
    "DOT",
    "LTC",
    "BCH",
    "TRX",
    "TON",
    "SHIB",
    "UNI",
    "AAVE",
}
_BINANCE_INTERVALS = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "60m": "1h",
    "1d": "1d",
    "1wk": "1w",
    "1mo": "1M",
}
_BINANCE_INTERVAL_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "1d": 24 * 60 * 60_000,
    "1w": 7 * 24 * 60 * 60_000,
    "1M": 31 * 24 * 60 * 60_000,
}


@dataclass(frozen=True)
class DataQuality:
    source: str
    score: float
    usable: bool
    bar_count: int
    freshness_score: float
    completeness_score: float
    validity_score: float
    regularity_score: float
    notes: tuple[str, ...] = ()


def detect_identifier(query: str) -> str | None:
    value = query.strip().upper()
    if _FIGI.fullmatch(value):
        return "ID_BB_GLOBAL"
    if _ISIN.fullmatch(value):
        return "ID_ISIN"
    if _CUSIP.fullmatch(value):
        return "ID_CUSIP"
    return None


def yahoo_symbol_from_terminal_query(query: str) -> str | None:
    """Convert terminal ticker/venue syntax into a Yahoo listing symbol."""
    match = _TERMINAL_TICKER.fullmatch(query.strip())
    if not match:
        return None
    ticker, venue = match.groups()
    suffix = _BLOOMBERG_TO_YAHOO_EXCHANGE.get(venue.upper())
    if suffix is None:
        return None
    return f"{ticker}{suffix}"


def configure_yfinance_cache() -> None:
    cache_dir = Path(__file__).resolve().parent / "out" / "yfinance_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    yf_cache.set_cache_location(str(cache_dir))


class OpenFigiClient:
    """Resolve standardized identifiers into candidate listed instruments."""

    endpoint = "https://api.openfigi.com/v3/mapping"

    def __init__(
        self,
        api_key: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENFIGI_API_KEY", "")
        self.session = session or requests.Session()

    def map_identifier(self, query: str, identifier_type: str) -> list[Instrument]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key
        response = self.session.post(
            self.endpoint,
            json=[{"idType": identifier_type, "idValue": query.strip().upper()}],
            headers=headers,
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        records = payload[0].get("data", []) if payload else []
        return [
            Instrument(
                symbol=str(record.get("ticker", "")).strip(),
                name=str(record.get("name", "")).strip(),
                exchange=str(record.get("exchCode", "")).strip(),
                quote_type=str(record.get("marketSector", "")).strip(),
                source="OpenFIGI",
                figi=str(record.get("figi", "")).strip(),
                isin=query.strip().upper() if identifier_type == "ID_ISIN" else "",
            )
            for record in records
            if record.get("ticker")
        ]


class TwelveDataClient:
    endpoint = "https://api.twelvedata.com"

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None) -> None:
        self.api_key = api_key or os.getenv("TWELVE_DATA_API_KEY", "")
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str) -> list[Instrument]:
        response = self.session.get(
            f"{self.endpoint}/symbol_search",
            params={"symbol": query, "apikey": self.api_key, "outputsize": 12},
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        records = payload.get("data", [])
        return [
            Instrument(
                symbol=str(record.get("symbol", "")).strip(),
                name=str(record.get("instrument_name") or record.get("symbol") or "").strip(),
                exchange=str(record.get("exchange", "")).strip(),
                quote_type=str(record.get("instrument_type", "")).strip(),
                currency=str(record.get("currency", "")).strip(),
                source="Twelve Data",
            )
            for record in records
            if record.get("symbol")
        ]

    def history(
        self,
        instrument: Instrument,
        range_spec: RangeSpec,
        include_extended_hours: bool = False,
    ) -> pd.DataFrame:
        interval = _twelve_interval(range_spec.interval)
        params = {
            "symbol": instrument.symbol,
            "interval": interval,
            "outputsize": _twelve_output_size(range_spec),
            "order": "asc",
            "apikey": self.api_key,
        }
        if range_spec.start and range_spec.end:
            params.update({"start_date": range_spec.start, "end_date": range_spec.end})
        response = self.session.get(
            f"{self.endpoint}/time_series",
            params=params,
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == "error":
            raise RuntimeError(payload.get("message", "Twelve Data request failed"))
        values = payload.get("values", [])
        if not values:
            return pd.DataFrame()
        frame = pd.DataFrame(values).rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
        )
        frame.index = pd.to_datetime(frame.pop("datetime"))
        for column in ("Open", "High", "Low", "Close", "Volume"):
            if column in frame:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["Close"])
        frame.attrs["data_source"] = "Twelve Data"
        return frame


class StooqClient:
    endpoint = "https://stooq.com/q/d/l/"

    def __init__(
        self,
        api_key: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("STOOQ_API_KEY", "")
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def history(self, instrument: Instrument, range_spec: RangeSpec) -> pd.DataFrame:
        if not self.enabled:
            return pd.DataFrame()
        if range_spec.interval not in {"1d", "1wk", "1mo"}:
            return pd.DataFrame()
        symbol = _stooq_symbol(instrument)
        if not symbol:
            return pd.DataFrame()
        response = self.session.get(
            self.endpoint,
            params={
                "s": symbol,
                "d1": _stooq_start_date(range_spec),
                "d2": _stooq_end_date(range_spec),
                "i": "d",
                "apikey": self.api_key,
            },
            timeout=12,
        )
        response.raise_for_status()
        if "No data" in response.text:
            return pd.DataFrame()
        frame = pd.read_csv(StringIO(response.text), parse_dates=["Date"])
        if frame.empty or "Close" not in frame:
            return pd.DataFrame()
        frame = frame.set_index("Date").sort_index()
        if range_spec.interval == "1wk":
            frame = _resample_ohlcv(frame, "W-FRI")
        elif range_spec.interval == "1mo":
            frame = _resample_ohlcv(frame, "ME")
        frame.attrs["data_source"] = "Stooq (EOD)"
        return frame.dropna(subset=["Close"])


class NfinCalendarClient:
    endpoint = _NFIN_ENDPOINT

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None) -> None:
        self.api_key = api_key or os.getenv("NFIN_API_KEY", "")
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return os.getenv("NFIN_DISABLE", "0") != "1"

    def market_events(self, instrument: Instrument, limit: int = 16) -> list[MarketEvent]:
        symbol = nfin_symbol_from_instrument(instrument)
        if not self.enabled or not symbol:
            return []
        events: list[MarketEvent] = []
        events.extend(_nfin_quote_dividend_events(self._get(f"quote/{symbol}/dividends"), symbol))
        events.extend(_nfin_upcoming_recent_events(self._get("calendar/upcoming-recent"), symbol))
        for route, parser in (
            ("calendar/earnings", _nfin_earnings_events),
            ("calendar/dividends", _nfin_dividend_events),
            ("calendar/splits", _nfin_split_events),
            ("ipo/calendar", _nfin_ipo_events),
        ):
            payload = self._get(route)
            rows = _nfin_rows(payload)
            events.extend(parser(rows, symbol))
            if len(events) >= limit:
                break
        return select_market_events(events, limit)

    def _get(self, route: str) -> object:
        headers = {"User-Agent": "market-terminal/0.1"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-Nfin-Key"] = self.api_key
        response = self.session.get(
            f"{self.endpoint}/v1/{route}",
            headers=headers,
            timeout=12,
        )
        response.raise_for_status()
        return response.json()


class BinanceSpotClient:
    endpoint = "https://api.binance.com"

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return os.getenv("BINANCE_DISABLE", "0") != "1"

    def search(self, query: str) -> list[Instrument]:
        symbol = binance_symbol_from_query(query)
        if not symbol:
            return []
        base, quote = split_binance_symbol(symbol)
        return [
            Instrument(
                symbol,
                f"{base}/{quote} spot",
                exchange="Binance",
                quote_type="Crypto",
                currency=quote,
                source="Binance Spot",
            )
        ]

    def history(
        self,
        instrument: Instrument,
        range_spec: RangeSpec,
        include_extended_hours: bool = False,
    ) -> pd.DataFrame:
        if not self.enabled or range_spec.interval not in _BINANCE_INTERVALS:
            return pd.DataFrame()
        symbol = binance_symbol_from_instrument(instrument)
        if not symbol:
            return pd.DataFrame()
        interval = _BINANCE_INTERVALS[range_spec.interval]
        rows = self._klines(symbol, interval, range_spec)
        if not rows:
            return pd.DataFrame()
        frame = pd.DataFrame(
            rows,
            columns=(
                "open_time",
                "Open",
                "High",
                "Low",
                "Close",
                "Volume",
                "close_time",
                "quote_volume",
                "trades",
                "taker_buy_base",
                "taker_buy_quote",
                "unused",
            ),
        )
        frame.index = pd.to_datetime(frame.pop("open_time"), unit="ms", utc=True)
        for column in ("Open", "High", "Low", "Close", "Volume"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        result = frame[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        result.attrs["data_source"] = "Binance Spot"
        result.attrs["binance_symbol"] = symbol
        return result

    def quote_snapshot(self, instrument: Instrument) -> QuoteSnapshot:
        symbol = binance_symbol_from_instrument(instrument)
        if not self.enabled or not symbol:
            raise RuntimeError("Instrument is not a Binance spot crypto pair")
        response = self.session.get(
            f"{self.endpoint}/api/v3/ticker/24hr",
            params={"symbol": symbol},
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        return QuoteSnapshot(
            last=_as_optional_float(payload.get("lastPrice")),
            bid=_as_optional_float(payload.get("bidPrice")),
            ask=_as_optional_float(payload.get("askPrice")),
            change=_as_optional_float(payload.get("priceChange")),
            change_percent=_as_optional_float(payload.get("priceChangePercent")),
            volume=_as_optional_float(payload.get("volume")),
            market_state="OPEN - 24/7 CRYPTO",
            source="Binance Spot",
            source_detail="Public 24h ticker snapshot",
        )

    def _klines(self, symbol: str, interval: str, range_spec: RangeSpec) -> list[list]:
        start, end = _binance_time_bounds(range_spec)
        rows: list[list] = []
        next_start = start
        while True:
            params = {"symbol": symbol, "interval": interval, "limit": 1000}
            if next_start is not None:
                params["startTime"] = next_start
            if end is not None:
                params["endTime"] = end
            response = self.session.get(
                f"{self.endpoint}/api/v3/klines",
                params=params,
                timeout=12,
            )
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            rows.extend(batch)
            if next_start is None or len(batch) < 1000:
                break
            next_start = int(batch[-1][0]) + _BINANCE_INTERVAL_MS[interval]
            if end is not None and next_start > end:
                break
        return rows


class MarketDataProvider:
    history_ttl_seconds = 120.0
    instrument_details_ttl_seconds = 86400.0
    market_events_ttl_seconds = 3600.0
    market_session_ttl_seconds = 300.0
    quote_info_ttl_seconds = 60.0
    quote_snapshot_ttl_seconds = 20.0
    live_quote_snapshot_ttl_seconds = 2.0
    yahoo_min_request_interval_seconds = 0.75

    def __init__(
        self,
        figi: OpenFigiClient | None = None,
        twelve: TwelveDataClient | None = None,
        stooq: StooqClient | None = None,
        binance: BinanceSpotClient | None = None,
        nfin: NfinCalendarClient | None = None,
        live_quotes: LiveQuoteService | None = None,
    ) -> None:
        configure_yfinance_cache()
        self.figi = figi or OpenFigiClient()
        self.twelve = twelve or TwelveDataClient()
        self.stooq = stooq or StooqClient()
        self.binance = binance or BinanceSpotClient()
        self.nfin = nfin or NfinCalendarClient()
        self.live_quotes = live_quotes or LiveQuoteService()
        self._yahoo_lock = Lock()
        self._last_yahoo_request_at = 0.0
        self._history_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}
        self._instrument_details_cache: dict[str, tuple[float, Instrument]] = {}
        self._market_events_cache: dict[tuple[str, int], tuple[float, list[MarketEvent]]] = {}
        self._market_session_cache: dict[str, tuple[float, MarketSession]] = {}
        self._quote_info_cache: dict[str, tuple[float, dict]] = {}
        self._quote_snapshot_cache: dict[tuple[str, bool], tuple[float, QuoteSnapshot]] = {}

    def search(self, query: str) -> list[Instrument]:
        query = query.strip()
        if not query:
            return []
        portfolio_results = search_portfolio_index(query)
        if portfolio_results:
            return portfolio_results
        identifier_type = detect_identifier(query)
        if identifier_type:
            mapped = self.figi.map_identifier(query, identifier_type)
            enriched: list[Instrument] = []
            for candidate in mapped[:8]:
                yahoo_symbol = (
                    yahoo_symbol_from_terminal_query(
                        f"{candidate.symbol} {candidate.exchange}"
                    )
                    or candidate.symbol
                )
                results = self._search_yahoo(yahoo_symbol)
                exact_results = [
                    result
                    for result in results
                    if result.symbol.upper() == yahoo_symbol.upper()
                ]
                enriched.extend(
                    [
                        Instrument(
                            symbol=result.symbol,
                            name=result.name or candidate.name,
                            exchange=result.exchange or candidate.exchange,
                            quote_type=result.quote_type or candidate.quote_type,
                            currency=result.currency,
                            source="Yahoo Finance + OpenFIGI",
                            figi=candidate.figi,
                            market_cap=result.market_cap,
                            isin=candidate.isin
                            or (query.upper() if identifier_type == "ID_ISIN" else ""),
                        )
                        for result in exact_results
                    ]
                    or [candidate]
                )
            return _unique_instruments(enriched or mapped)
        instruments: list[Instrument] = []
        binance = getattr(self, "binance", None)
        if binance is not None and binance.enabled:
            instruments.extend(binance.search(query))
        yahoo_symbol = yahoo_symbol_from_terminal_query(query)
        if yahoo_symbol:
            try:
                translated = self._search_yahoo(yahoo_symbol)
                instruments.extend(_prioritize_symbol(translated, yahoo_symbol))
            except Exception:
                pass
        try:
            instruments.extend(self._search_yahoo(query))
        except Exception:
            pass
        if self.twelve.enabled:
            try:
                instruments.extend(self.twelve.search(query))
            except Exception:
                pass
        return _unique_instruments(instruments)

    def instrument_details(self, instrument: Instrument) -> Instrument:
        cached = self._cached_instrument_details(instrument)
        if cached is not None:
            return cached
        market_cap = instrument.market_cap
        aum = instrument.aum
        try:
            ticker = yf.Ticker(instrument.symbol)
            self._pace_yahoo()
            info = ticker.info or {}
        except Exception:
            info = {}
        if market_cap is None:
            market_cap = _as_optional_float(info.get("marketCap"))
        if aum is None:
            aum = _as_optional_float(
                info.get("totalAssets")
                or info.get("totalNetAssets")
                or info.get("netAssets")
                or info.get("fundTotalAssets")
            )
        if instrument.isin:
            result = replace(instrument, market_cap=market_cap, aum=aum)
            self._store_instrument_details(instrument, result)
            return result
        try:
            ticker = yf.Ticker(instrument.symbol)
            self._pace_yahoo()
            isin = str(ticker.get_isin() or "").strip()
        except Exception:
            isin = ""
        if not isin or isin == "-":
            isin = _lookup_isin_by_euronext_listing(instrument.symbol)
        result = replace(instrument, isin=isin, market_cap=market_cap, aum=aum)
        self._store_instrument_details(instrument, result)
        return result

    def market_events(self, instrument: Instrument, limit: int = 16) -> list[MarketEvent]:
        cached = self._cached_market_events(instrument, limit)
        if cached is not None:
            return cached
        if instrument.symbol.upper() == PORTFOLIO_INDEX_SYMBOL:
            return []
        binance = getattr(self, "binance", None)
        if binance is not None and binance_symbol_from_instrument(instrument):
            return []
        ticker = yf.Ticker(instrument.symbol)
        events: list[MarketEvent] = []
        try:
            self._pace_yahoo()
            events.extend(_events_from_yahoo_calendar(ticker.get_calendar()))
        except Exception:
            pass
        try:
            self._pace_yahoo()
            events.extend(_events_from_yahoo_earnings_dates(ticker.get_earnings_dates(limit=limit)))
        except Exception:
            pass
        nfin = getattr(self, "nfin", None)
        if nfin is not None:
            try:
                events.extend(nfin.market_events(instrument, limit=limit))
            except Exception:
                pass
        result = select_market_events(events, limit)
        self._store_market_events(instrument, limit, result)
        return result

    def _search_yahoo(self, query: str) -> list[Instrument]:
        self._pace_yahoo()
        search = yf.Search(
            query,
            max_results=12,
            news_count=0,
            lists_count=0,
            include_cb=False,
            include_nav_links=False,
            include_research=False,
            include_cultural_assets=False,
            timeout=12,
        )
        instruments = []
        for quote in search.quotes:
            symbol = str(quote.get("symbol", "")).strip()
            if not symbol:
                continue
            instruments.append(
                Instrument(
                    symbol=symbol,
                    name=str(
                        quote.get("longname")
                        or quote.get("shortname")
                        or quote.get("name")
                        or symbol
                    ).strip(),
                    exchange=str(
                        quote.get("exchDisp") or quote.get("exchange") or ""
                    ).strip(),
                    quote_type=str(quote.get("quoteType", "")).strip(),
                    currency=str(quote.get("currency", "")).strip(),
                    market_cap=_as_optional_float(quote.get("marketCap")),
                    aum=_as_optional_float(
                        quote.get("totalAssets")
                        or quote.get("totalNetAssets")
                        or quote.get("netAssets")
                    ),
                )
            )
        return _unique_instruments(instruments)

    def history(
        self,
        instrument: Instrument,
        range_spec: RangeSpec,
        include_extended_hours: bool = False,
    ) -> pd.DataFrame:
        if instrument.symbol.upper() == PORTFOLIO_INDEX_SYMBOL:
            return load_portfolio_index_history(range_spec)
        cached = self._cached_history(instrument, range_spec, include_extended_hours)
        if cached is not None:
            return cached
        attempts = [self._history_yahoo]
        binance = getattr(self, "binance", None)
        if binance is not None and binance.enabled and binance_symbol_from_instrument(instrument):
            attempts.insert(0, binance.history)
        if self.twelve.enabled:
            attempts.append(self.twelve.history)
        if self.stooq.enabled:
            attempts.append(lambda value, spec, _prepost=False: self.stooq.history(value, spec))
        failures = []
        candidates: list[tuple[pd.DataFrame, DataQuality]] = []
        for fetch in attempts:
            try:
                frame = fetch(instrument, range_spec, include_extended_hours)
            except Exception as exc:
                failures.append(str(exc))
                continue
            if not frame.empty:
                frame = _clip_custom_range(frame, range_spec)
                quality = score_history_frame(frame, range_spec)
                frame.attrs["quality"] = quality
                candidates.append((frame, quality))
        usable = [candidate for candidate in candidates if candidate[1].usable]
        if usable:
            selected, selected_quality = max(usable, key=lambda candidate: candidate[1].score)
            selected.attrs["quality"] = selected_quality
            selected.attrs["quality_candidates"] = tuple(
                quality for _frame, quality in candidates
            )
            self._store_history(instrument, range_spec, include_extended_hours, selected)
            return selected
        if candidates:
            diagnostics = " | ".join(_quality_summary(quality) for _frame, quality in candidates)
            raise RuntimeError(f"Sources returned bars but failed quality checks: {diagnostics}")
        if failures:
            raise RuntimeError("No data source returned bars: " + " | ".join(failures))
        return pd.DataFrame()

    def _history_yahoo(
        self,
        instrument: Instrument,
        range_spec: RangeSpec,
        include_extended_hours: bool = False,
    ) -> pd.DataFrame:
        request = {
            "interval": range_spec.interval,
            "auto_adjust": False,
            "actions": False,
            "prepost": include_extended_hours,
            "raise_errors": True,
        }
        if range_spec.start and range_spec.end:
            request.update(
                {
                    "start": range_spec.start,
                    "end": (pd.Timestamp(range_spec.end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                }
            )
        else:
            request["period"] = range_spec.period
        ticker = yf.Ticker(instrument.symbol)
        self._pace_yahoo()
        frame = ticker.history(**request)
        if frame.empty:
            return frame
        required_columns = ["Open", "High", "Low", "Close", "Volume"]
        available = [column for column in required_columns if column in frame.columns]
        result = frame[available].dropna(subset=["Close"])
        result.attrs["data_source"] = "Yahoo Finance"
        return result

    def market_session(self, instrument: Instrument) -> MarketSession:
        cached = self._cached_market_session(instrument)
        if cached is not None:
            return cached
        if instrument.symbol.upper() == PORTFOLIO_INDEX_SYMBOL:
            return portfolio_market_session()
        binance = getattr(self, "binance", None)
        if binance is not None and binance.enabled and binance_symbol_from_instrument(instrument):
            return MarketSession(
                status="OPEN - 24/7 CRYPTO",
                exchange_timezone="UTC",
                regular_exchange_hours="Always open",
                regular_local_hours="Always open",
                extended_session="Not applicable",
                overnight_session="Trades continuously",
            )
        ticker = yf.Ticker(instrument.symbol)
        self._pace_yahoo()
        metadata = ticker.get_history_metadata()
        session = build_market_session(metadata)
        self._store_market_session(instrument, session)
        return session

    def quote_snapshot(self, instrument: Instrument, include_slow_info: bool = True) -> QuoteSnapshot:
        cached = self._cached_quote_snapshot(instrument, include_slow_info)
        if cached is not None:
            return cached
        if instrument.symbol.upper() == PORTFOLIO_INDEX_SYMBOL:
            frame = load_portfolio_index_history(RangeSpec("MAX", "max", "1d"))
            last = float(frame["Close"].iloc[-1]) if not frame.empty else None
            previous = float(frame["Close"].iloc[-2]) if len(frame) >= 2 else None
            change = last - previous if last is not None and previous is not None else None
            change_percent = (
                change / previous * 100
                if change is not None and previous not in (None, 0)
                else None
            )
            volume = (
                float(frame["Volume"].iloc[-1])
                if not frame.empty and "Volume" in frame.columns
                else None
            )
            quote = QuoteSnapshot(
                last=last,
                change=change,
                change_percent=change_percent,
                volume=volume,
                market_state="LOCAL INDEX",
                source="Local portfolio index",
                source_detail="Generated local portfolio monitor",
            )
            self._store_quote_snapshot(instrument, include_slow_info, quote)
            return quote
        live_quotes = getattr(self, "live_quotes", None)
        if live_quotes is not None:
            try:
                quote = live_quotes.quote_snapshot(instrument)
            except Exception:
                quote = None
            if quote is not None:
                self._store_quote_snapshot(instrument, include_slow_info, quote)
                return quote
        binance = getattr(self, "binance", None)
        if binance is not None and binance.enabled and binance_symbol_from_instrument(instrument):
            try:
                quote = binance.quote_snapshot(instrument)
                self._store_quote_snapshot(instrument, include_slow_info, quote)
                return quote
            except Exception:
                pass
        ticker = yf.Ticker(instrument.symbol)
        try:
            self._pace_yahoo()
            fast = dict(ticker.fast_info or {})
        except Exception:
            fast = {}
        info = self._quote_info(instrument.symbol, ticker, include_slow_info)
        last = _first_optional_float(
            fast,
            info,
            ("last_price", "lastPrice", "regularMarketPrice", "currentPrice", "previousClose"),
        )
        previous_close = _first_optional_float(
            fast,
            info,
            (
                "previous_close",
                "previousClose",
                "regularMarketPreviousClose",
                "regular_market_previous_close",
            ),
        )
        change = _first_optional_float(
            fast,
            info,
            ("regular_market_change", "regularMarketChange", "change"),
        )
        change_percent = _first_optional_float(
            fast,
            info,
            (
                "regular_market_change_percent",
                "regularMarketChangePercent",
                "changePercent",
            ),
        )
        if change is None and last is not None and previous_close not in (None, 0):
            change = last - previous_close
        if change_percent is None and change is not None and previous_close not in (None, 0):
            change_percent = change / previous_close * 100
        quote = QuoteSnapshot(
            last=last,
            bid=_first_optional_float(fast, info, ("bid", "bidPrice")),
            ask=_first_optional_float(fast, info, ("ask", "askPrice")),
            change=change,
            change_percent=change_percent,
            volume=_first_optional_float(
                fast,
                info,
                ("last_volume", "lastVolume", "regularMarketVolume", "volume"),
            ),
            market_state=_first_optional_string(
                fast,
                info,
                ("market_state", "marketState"),
            ),
            source="Yahoo Finance",
            source_detail="yfinance fast_info/info fallback",
        )
        self._store_quote_snapshot(instrument, include_slow_info, quote)
        return quote

    def _pace_yahoo(self) -> None:
        lock = getattr(self, "_yahoo_lock", None)
        if lock is None:
            self._yahoo_lock = Lock()
            lock = self._yahoo_lock
        with lock:
            now = monotonic()
            wait_seconds = self.yahoo_min_request_interval_seconds - (
                now - getattr(self, "_last_yahoo_request_at", 0.0)
            )
            if wait_seconds > 0:
                sleep(wait_seconds)
            self._last_yahoo_request_at = monotonic()

    def _history_cache_key(
        self,
        instrument: Instrument,
        range_spec: RangeSpec,
        include_extended_hours: bool,
    ) -> tuple:
        return (
            instrument.symbol.upper(),
            range_spec.label,
            range_spec.period,
            range_spec.interval,
            range_spec.start,
            range_spec.end,
            include_extended_hours,
        )

    def _copy_history_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        copied = frame.copy()
        copied.attrs.update(frame.attrs)
        return copied

    def _cached_history(
        self,
        instrument: Instrument,
        range_spec: RangeSpec,
        include_extended_hours: bool,
    ) -> pd.DataFrame | None:
        cache = getattr(self, "_history_cache", None)
        if cache is None:
            cache = {}
            self._history_cache = cache
        cached = cache.get(self._history_cache_key(instrument, range_spec, include_extended_hours))
        if cached and monotonic() - cached[0] < self.history_ttl_seconds:
            return self._copy_history_frame(cached[1])
        return None

    def _store_history(
        self,
        instrument: Instrument,
        range_spec: RangeSpec,
        include_extended_hours: bool,
        frame: pd.DataFrame,
    ) -> None:
        cache = getattr(self, "_history_cache", None)
        if cache is None:
            cache = {}
            self._history_cache = cache
        cache[self._history_cache_key(instrument, range_spec, include_extended_hours)] = (
            monotonic(),
            self._copy_history_frame(frame),
        )

    def _cached_instrument_details(self, instrument: Instrument) -> Instrument | None:
        cache = getattr(self, "_instrument_details_cache", None)
        if cache is None:
            cache = {}
            self._instrument_details_cache = cache
        cached = cache.get(instrument.symbol.upper())
        if cached and monotonic() - cached[0] < self.instrument_details_ttl_seconds:
            return cached[1]
        return None

    def _store_instrument_details(self, instrument: Instrument, details: Instrument) -> None:
        cache = getattr(self, "_instrument_details_cache", None)
        if cache is None:
            cache = {}
            self._instrument_details_cache = cache
        cache[instrument.symbol.upper()] = (monotonic(), details)

    def _cached_market_events(
        self, instrument: Instrument, limit: int
    ) -> list[MarketEvent] | None:
        cache = getattr(self, "_market_events_cache", None)
        if cache is None:
            cache = {}
            self._market_events_cache = cache
        cached = cache.get((instrument.symbol.upper(), limit))
        if cached and monotonic() - cached[0] < self.market_events_ttl_seconds:
            return list(cached[1])
        return None

    def _store_market_events(
        self, instrument: Instrument, limit: int, events: list[MarketEvent]
    ) -> None:
        cache = getattr(self, "_market_events_cache", None)
        if cache is None:
            cache = {}
            self._market_events_cache = cache
        cache[(instrument.symbol.upper(), limit)] = (monotonic(), list(events))

    def _cached_market_session(self, instrument: Instrument) -> MarketSession | None:
        cache = getattr(self, "_market_session_cache", None)
        if cache is None:
            cache = {}
            self._market_session_cache = cache
        cached = cache.get(instrument.symbol.upper())
        if cached and monotonic() - cached[0] < self.market_session_ttl_seconds:
            return cached[1]
        return None

    def _store_market_session(self, instrument: Instrument, session: MarketSession) -> None:
        cache = getattr(self, "_market_session_cache", None)
        if cache is None:
            cache = {}
            self._market_session_cache = cache
        cache[instrument.symbol.upper()] = (monotonic(), session)

    def _cached_quote_snapshot(
        self, instrument: Instrument, include_slow_info: bool
    ) -> QuoteSnapshot | None:
        cache = getattr(self, "_quote_snapshot_cache", None)
        if cache is None:
            cache = {}
            self._quote_snapshot_cache = cache
        cached = cache.get((instrument.symbol.upper(), include_slow_info))
        if cached and monotonic() - cached[0] < self._quote_snapshot_ttl(cached[1]):
            return cached[1]
        return None

    def _store_quote_snapshot(
        self, instrument: Instrument, include_slow_info: bool, quote: QuoteSnapshot
    ) -> None:
        cache = getattr(self, "_quote_snapshot_cache", None)
        if cache is None:
            cache = {}
            self._quote_snapshot_cache = cache
        cache[(instrument.symbol.upper(), include_slow_info)] = (monotonic(), quote)

    def _quote_snapshot_ttl(self, quote: QuoteSnapshot) -> float:
        source = str(getattr(quote, "source", "") or "")
        if source and source not in {"Yahoo Finance", "Local portfolio index"}:
            return self.live_quote_snapshot_ttl_seconds
        return self.quote_snapshot_ttl_seconds

    def _quote_info(self, symbol: str, ticker, include_slow_info: bool) -> dict:
        cache = getattr(self, "_quote_info_cache", None)
        if cache is None:
            cache = {}
            self._quote_info_cache = cache
        key = symbol.upper()
        cached = cache.get(key)
        now = monotonic()
        if cached and now - cached[0] < self.quote_info_ttl_seconds:
            return cached[1]
        if not include_slow_info:
            return cached[1] if cached else {}
        try:
            self._pace_yahoo()
            info = ticker.info or {}
        except Exception:
            info = cached[1] if cached else {}
        cache[key] = (now, info)
        return info


def _unique_instruments(instruments: list[Instrument]) -> list[Instrument]:
    unique: dict[str, Instrument] = {}
    for instrument in instruments:
        unique.setdefault(instrument.symbol, instrument)
    return list(unique.values())


def _unique_market_events(events: list[MarketEvent]) -> list[MarketEvent]:
    unique: dict[tuple[str, str, str], MarketEvent] = {}
    for event in sorted(events, key=_market_event_sort_key):
        if not event.event:
            continue
        timestamp_key = event.timestamp.isoformat() if event.timestamp else ""
        key = (timestamp_key, event.event_type.upper(), event.event.upper())
        unique.setdefault(key, event)
    return list(unique.values())


def select_market_events(
    events: list[MarketEvent],
    limit: int = 16,
    recent_limit: int = 4,
    now: datetime | None = None,
) -> list[MarketEvent]:
    if limit <= 0:
        return []
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    unique = _unique_market_events(events)
    upcoming = [event for event in unique if _event_datetime_utc(event) >= current]
    recent = [event for event in unique if _event_datetime_utc(event) < current]
    upcoming.sort(key=_market_event_sort_key)
    recent.sort(key=_market_event_sort_key, reverse=True)
    selected_recent = recent[: min(recent_limit, limit)]
    selected_upcoming = upcoming[: max(limit - len(selected_recent), 0)]
    return sorted(selected_upcoming + selected_recent, key=_market_event_sort_key)


def _event_datetime_utc(event: MarketEvent) -> datetime:
    timestamp = event.timestamp or datetime.max.replace(tzinfo=timezone.utc)
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def nfin_symbol_from_instrument(instrument: Instrument) -> str:
    symbol = instrument.symbol.strip().upper()
    if not symbol or symbol == PORTFOLIO_INDEX_SYMBOL:
        return ""
    if any(marker in symbol for marker in (".", "=", "^", "/", " ")):
        return ""
    quote_type = instrument.quote_type.upper()
    if quote_type and quote_type not in {"EQUITY", "STOCK", "ETF", "FUND", "MUTUALFUND"}:
        return ""
    return symbol


def _nfin_rows(payload: object) -> list[dict]:
    rows = _nfin_find_rows(payload)
    return [row for row in rows if isinstance(row, dict)]


def _nfin_find_rows(value: object) -> list:
    if isinstance(value, list):
        if all(isinstance(item, dict) for item in value):
            return value
        for item in value:
            rows = _nfin_find_rows(item)
            if rows:
                return rows
        return []
    if not isinstance(value, dict):
        return []
    rows = value.get("rows")
    if isinstance(rows, list):
        return rows
    for key in ("table", "data", "calendar", "earnings", "dividends", "splits", "ipos"):
        rows = _nfin_find_rows(value.get(key))
        if rows:
            return rows
    for nested in value.values():
        rows = _nfin_find_rows(nested)
        if rows:
            return rows
    return []


def _nfin_row_symbol(row: dict) -> str:
    for key in (
        "symbol",
        "ticker",
        "tickerSymbol",
        "proposedTickerSymbol",
        "companySymbol",
    ):
        value = str(row.get(key, "") or "").strip().upper()
        if value:
            return value
    url = str(row.get("url", "") or "")
    match = re.search(r"/stocks/([A-Za-z0-9]+)", url)
    return match.group(1).upper() if match else ""


def _nfin_matching_rows(rows: list[dict], symbol: str) -> list[dict]:
    return [row for row in rows if _nfin_row_symbol(row) == symbol]


def _nfin_quote_dividend_events(payload: object, symbol: str) -> list[MarketEvent]:
    data = _nfin_payload_data(payload)
    rows = _nfin_find_rows(data.get("dividends") if isinstance(data, dict) else data)
    events = []
    for row in [item for item in rows if isinstance(item, dict)]:
        for event, keys in (
            ("Ex-dividend", ("exOrEffDate", "exDivDate", "exDividendDate")),
            ("Dividend record", ("recordDate",)),
            ("Dividend payable", ("paymentDate", "dividendPaymentDate")),
        ):
            timestamp, is_date_only = _nfin_row_timestamp(row, keys)
            if timestamp is None:
                continue
            note_parts = _nfin_note_parts(
                row,
                (
                    ("Amount", "amount"),
                    ("Type", "type"),
                    ("Currency", "currency"),
                ),
            )
            events.append(
                MarketEvent(
                    timestamp=timestamp,
                    event=event,
                    event_type="Dividend",
                    source="nfin Nasdaq quote dividends",
                    note=_nfin_note(note_parts),
                    is_date_only=is_date_only,
                )
            )
    if events:
        return events
    if not isinstance(data, dict):
        return []
    for event, keys in (
        ("Ex-dividend", ("exDividendDate",)),
        ("Dividend payable", ("dividendPaymentDate",)),
    ):
        timestamp, is_date_only = _nfin_row_timestamp(data, keys)
        if timestamp is None:
            continue
        note_parts = _nfin_note_parts(
            data,
            (
                ("Annualized", "annualizedDividend"),
                ("Yield", "yield"),
            ),
        )
        events.append(
            MarketEvent(
                timestamp=timestamp,
                event=event,
                event_type="Dividend",
                source="nfin Nasdaq quote dividends",
                note=_nfin_note(note_parts),
                is_date_only=is_date_only,
            )
        )
    return events


def _nfin_upcoming_recent_events(payload: object, symbol: str) -> list[MarketEvent]:
    data = _nfin_payload_data(payload)
    if not isinstance(data, dict):
        return []
    events = []
    for group_key, group_label in (("upcomingEvents", "Upcoming"), ("recentEvents", "Recent")):
        group = data.get(group_key)
        rows = _nfin_find_rows(group)
        for row in [item for item in rows if isinstance(item, dict)]:
            if _nfin_row_symbol(row) != symbol:
                continue
            timestamp, is_date_only = _nfin_row_timestamp(row, ("eventDate", "date"))
            if timestamp is None:
                continue
            name = str(row.get("eventName") or row.get("name") or "Event").strip()
            event_type = _nfin_event_type(name)
            events.append(
                MarketEvent(
                    timestamp=timestamp,
                    event=name,
                    event_type=event_type,
                    source="nfin Nasdaq upcoming/recent",
                    note=f"Best-effort Nasdaq {group_label.lower()} event via nfin",
                    is_date_only=is_date_only,
                )
            )
    return events


def _nfin_payload_data(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data")
    if isinstance(data, dict) and "data" in data:
        return data.get("data")
    return data if data is not None else payload


def _nfin_event_type(name: str) -> str:
    normalized = name.casefold()
    if "earn" in normalized:
        return "Earnings"
    if "dividend" in normalized:
        return "Dividend"
    if "split" in normalized:
        return "Split"
    if "ipo" in normalized:
        return "IPO"
    if "filing" in normalized:
        return "Filing"
    return "Event"


def _nfin_earnings_events(rows: list[dict], symbol: str) -> list[MarketEvent]:
    events = []
    for row in _nfin_matching_rows(rows, symbol):
        timestamp, is_date_only = _nfin_row_timestamp(
            row,
            ("date", "earningsDate", "reportDate", "eventDate"),
        )
        if timestamp is None:
            continue
        note_parts = _nfin_note_parts(
            row,
            (
                ("Fiscal Q", "fiscalQuarterEnding"),
                ("Revenue est", "revenueForecast"),
                ("Time", "time"),
            ),
        )
        prediction = _nfin_prediction(row, ("epsForecast", "revenueForecast"))
        actual = _nfin_actual(row, ("eps", "revenue"))
        events.append(
            MarketEvent(
                timestamp=timestamp,
                event="Earnings",
                event_type="Earnings",
                source="nfin Nasdaq calendar",
                note=_nfin_note(note_parts),
                is_date_only=is_date_only,
                prediction=prediction,
                actual=actual,
            )
        )
    return events


def _nfin_dividend_events(rows: list[dict], symbol: str) -> list[MarketEvent]:
    events = []
    for row in _nfin_matching_rows(rows, symbol):
        for event, event_type, keys in (
            ("Ex-dividend", "Dividend", ("exDivDate", "exDividendDate", "exDate")),
            ("Dividend record", "Dividend", ("recordDate",)),
            ("Dividend payable", "Dividend", ("paymentDate", "payDate")),
        ):
            timestamp, is_date_only = _nfin_row_timestamp(row, keys)
            if timestamp is None:
                continue
            note_parts = _nfin_note_parts(
                row,
                (
                    ("Amount", "dividend"),
                    ("Amount", "amount"),
                    ("Annualized", "annualizedDividend"),
                ),
            )
            events.append(
                MarketEvent(
                    timestamp=timestamp,
                    event=event,
                    event_type=event_type,
                    source="nfin Nasdaq calendar",
                    note=_nfin_note(note_parts),
                    is_date_only=is_date_only,
                )
            )
    return events


def _nfin_split_events(rows: list[dict], symbol: str) -> list[MarketEvent]:
    events = []
    for row in _nfin_matching_rows(rows, symbol):
        timestamp, is_date_only = _nfin_row_timestamp(
            row,
            ("executionDate", "splitDate", "exDate", "eventDate"),
        )
        if timestamp is None:
            continue
        note_parts = _nfin_note_parts(
            row,
            (
                ("Ratio", "ratio"),
                ("From", "fromFactor"),
                ("To", "toFactor"),
            ),
        )
        events.append(
            MarketEvent(
                timestamp=timestamp,
                event="Split",
                event_type="Split",
                source="nfin Nasdaq calendar",
                note=_nfin_note(note_parts),
                is_date_only=is_date_only,
            )
        )
    return events


def _nfin_ipo_events(rows: list[dict], symbol: str) -> list[MarketEvent]:
    events = []
    for row in _nfin_matching_rows(rows, symbol):
        timestamp, is_date_only = _nfin_row_timestamp(
            row,
            ("pricedDate", "expectedDate", "ipoDate", "date"),
        )
        if timestamp is None:
            continue
        note_parts = _nfin_note_parts(
            row,
            (
                ("Company", "companyName"),
                ("Exchange", "exchange"),
                ("Price", "price"),
                ("Shares", "shares"),
            ),
        )
        events.append(
            MarketEvent(
                timestamp=timestamp,
                event="IPO",
                event_type="IPO",
                source="nfin Nasdaq calendar",
                note=_nfin_note(note_parts),
                is_date_only=is_date_only,
            )
        )
    return events


def _nfin_row_timestamp(row: dict, keys: tuple[str, ...]) -> tuple[datetime | None, bool]:
    for key in keys:
        timestamp, is_date_only = _event_timestamp(row.get(key))
        if timestamp is not None:
            return timestamp, is_date_only
    return None, False


def _nfin_note_parts(row: dict, fields: tuple[tuple[str, str], ...]) -> list[str]:
    parts = []
    seen: set[str] = set()
    for label, key in fields:
        value = str(row.get(key, "") or "").strip()
        if not value or value in {"-", "--", "N/A"}:
            continue
        part = f"{label} {value}"
        if part not in seen:
            parts.append(part)
            seen.add(part)
    return parts


def _nfin_note(parts: list[str]) -> str:
    base = "Best-effort Nasdaq calendar via nfin"
    return f"{base} | {' | '.join(parts)}" if parts else base


def _nfin_prediction(row: dict, keys: tuple[str, ...]) -> str:
    return _nfin_metric_text(row, keys)


def _nfin_actual(row: dict, keys: tuple[str, ...]) -> str:
    return _nfin_metric_text(row, keys)


def _nfin_metric_text(row: dict, keys: tuple[str, ...]) -> str:
    parts = []
    for key in keys:
        value = str(row.get(key, "") or "").strip()
        if not value or value in {"-", "--", "N/A", "0"}:
            continue
        label = "EPS" if "eps" in key.casefold() else "Rev"
        parts.append(f"{label} {value}")
    return " | ".join(parts)


def _market_event_sort_key(event: MarketEvent) -> tuple[int, datetime, str]:
    timestamp = event.timestamp or datetime.max.replace(tzinfo=timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return (0 if event.timestamp else 1, timestamp, event.event)


def _events_from_yahoo_calendar(calendar: object) -> list[MarketEvent]:
    if not isinstance(calendar, dict):
        return []
    events: list[MarketEvent] = []
    for key, value in calendar.items():
        event_type = _calendar_event_type(str(key))
        if not event_type:
            continue
        for timestamp, is_date_only in _calendar_timestamps(value):
            events.append(
                MarketEvent(
                    timestamp=timestamp,
                    event=_calendar_event_name(str(key)),
                    event_type=event_type,
                    source="Yahoo Finance calendar",
                    note="Best-effort public Yahoo calendar field",
                    is_date_only=is_date_only,
                )
            )
    return events


def _events_from_yahoo_earnings_dates(frame: object) -> list[MarketEvent]:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    events: list[MarketEvent] = []
    for index, row in frame.iterrows():
        timestamp, is_date_only = _event_timestamp(index)
        if timestamp is None:
            continue
        note_parts = []
        prediction_parts = []
        actual_parts = []
        for label, column in (
            ("EPS est", "EPS Estimate"),
            ("EPS actual", "Reported EPS"),
            ("Surprise", "Surprise(%)"),
        ):
            value = row.get(column) if hasattr(row, "get") else None
            parsed = _as_optional_float(value)
            if parsed is not None:
                note_parts.append(f"{label} {parsed:g}")
                if column == "EPS Estimate":
                    prediction_parts.append(f"EPS {parsed:g}")
                elif column == "Reported EPS":
                    actual_parts.append(f"EPS {parsed:g}")
                elif column == "Surprise(%)":
                    actual_parts.append(f"Surp {parsed:g}%")
        events.append(
            MarketEvent(
                timestamp=timestamp,
                event="Earnings",
                event_type="Earnings",
                source="Yahoo Finance earnings dates",
                note=" | ".join(note_parts) or "Best-effort public Yahoo earnings date",
                is_date_only=is_date_only,
                prediction=" | ".join(prediction_parts),
                actual=" | ".join(actual_parts),
            )
        )
    return events


def _calendar_event_type(key: str) -> str:
    normalized = key.strip().lower()
    if "earnings" in normalized and "date" in normalized:
        return "Earnings"
    if "ex-dividend" in normalized or "dividend date" in normalized:
        return "Dividend"
    if "split" in normalized and "date" in normalized:
        return "Split"
    return ""


def _calendar_event_name(key: str) -> str:
    normalized = key.strip()
    names = {
        "Earnings Date": "Earnings",
        "Ex-Dividend Date": "Ex-dividend",
        "Dividend Date": "Dividend payable",
        "Split Date": "Split",
    }
    return names.get(normalized, normalized)


def _calendar_timestamps(value: object) -> list[tuple[datetime, bool]]:
    if isinstance(value, (list, tuple, set, pd.Series, pd.Index)):
        timestamps = []
        for item in value:
            timestamp, is_date_only = _event_timestamp(item)
            if timestamp is not None:
                timestamps.append((timestamp, is_date_only))
        return timestamps
    timestamp, is_date_only = _event_timestamp(value)
    return [(timestamp, is_date_only)] if timestamp is not None else []


def _event_timestamp(value: object) -> tuple[datetime | None, bool]:
    if value in (None, "", "-"):
        return None, False
    try:
        timestamp = pd.Timestamp(value)
    except Exception:
        return None, False
    if pd.isna(timestamp):
        return None, False
    is_date_only = timestamp.hour == 0 and timestamp.minute == 0 and timestamp.second == 0
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.to_pydatetime(), is_date_only


def _prioritize_symbol(instruments: list[Instrument], symbol: str) -> list[Instrument]:
    exact = [
        instrument
        for instrument in instruments
        if instrument.symbol.upper() == symbol.upper()
    ]
    others = [
        instrument
        for instrument in instruments
        if instrument.symbol.upper() != symbol.upper()
    ]
    return exact + others


def _as_optional_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _first_optional_float(*mappings_and_keys) -> float | None:
    *mappings, keys = mappings_and_keys
    for key in keys:
        for mapping in mappings:
            value = mapping.get(key) if hasattr(mapping, "get") else None
            parsed = _as_optional_float(value)
            if parsed is not None:
                return parsed
    return None


def _first_optional_string(*mappings_and_keys) -> str:
    *mappings, keys = mappings_and_keys
    for key in keys:
        for mapping in mappings:
            value = mapping.get(key) if hasattr(mapping, "get") else None
            if value not in (None, ""):
                return str(value)
    return ""


def binance_symbol_from_query(query: str) -> str:
    value = query.strip().upper().replace("/", "-").replace(" ", "")
    if not value:
        return ""
    if "-" in value:
        base, quote = value.split("-", 1)
        quote = "USDT" if quote == "USD" else quote
        if base and quote in _BINANCE_QUOTES and base.isalnum():
            return f"{base}{quote}"
        return ""
    for quote in _BINANCE_QUOTES:
        if len(value) > len(quote) and value.endswith(quote):
            base = value[: -len(quote)]
            if base.isalnum():
                return value
    if value in _BINANCE_DEFAULT_BASES:
        return f"{value}USDT"
    return ""


def binance_symbol_from_instrument(instrument: Instrument) -> str:
    if instrument.source == "Binance Spot" or instrument.exchange.upper() == "BINANCE":
        return binance_symbol_from_query(instrument.symbol)
    quote_type = instrument.quote_type.upper()
    symbol = instrument.symbol.upper()
    if "CRYPTO" in quote_type or "-" in symbol or "/" in symbol:
        return binance_symbol_from_query(symbol)
    return ""


def split_binance_symbol(symbol: str) -> tuple[str, str]:
    for quote in _BINANCE_QUOTES:
        if len(symbol) > len(quote) and symbol.endswith(quote):
            return symbol[: -len(quote)], quote
    return symbol, ""


def _binance_time_bounds(range_spec: RangeSpec) -> tuple[int | None, int | None]:
    if range_spec.start and range_spec.end:
        start = pd.Timestamp(range_spec.start, tz="UTC")
        end = pd.Timestamp(range_spec.end, tz="UTC") + pd.Timedelta(days=1)
        return int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    if range_spec.period == "max":
        return None, None
    now = pd.Timestamp.now(tz="UTC")
    if range_spec.period == "ytd":
        start = pd.Timestamp(f"{now.year}-01-01", tz="UTC")
    else:
        offsets = {
            "1d": pd.Timedelta(days=1),
            "5d": pd.Timedelta(days=5),
            "1mo": pd.DateOffset(months=1),
            "3mo": pd.DateOffset(months=3),
            "6mo": pd.DateOffset(months=6),
            "1y": pd.DateOffset(years=1),
            "5y": pd.DateOffset(years=5),
        }
        start = now - offsets.get(range_spec.period, pd.DateOffset(months=3))
    return int(start.timestamp() * 1000), int(now.timestamp() * 1000)


def _lookup_isin_by_euronext_listing(symbol: str) -> str:
    base_symbol, separator, suffix = symbol.upper().partition(".")
    mic = _YAHOO_TO_EURONEXT_MIC.get(f".{suffix}") if separator else None
    if not base_symbol or not mic:
        return ""
    try:
        response = requests.get(
            _EURONEXT_SEARCH_ENDPOINT,
            params={"q": base_symbol},
            timeout=8,
        )
        response.raise_for_status()
    except requests.RequestException:
        return ""
    try:
        records = response.json()
    except ValueError:
        return ""
    for record in records:
        isin = str(record.get("isin", "")).upper()
        label = str(record.get("label", ""))
        exact_symbol = re.search(
            rf"class=['\"]symbol['\"]>\s*{re.escape(base_symbol)}\s*<",
            label,
            re.IGNORECASE,
        )
        if record.get("mic") == mic and exact_symbol and _ISIN.fullmatch(isin):
            return isin
    return ""


def build_market_session(
    metadata: dict,
    now: pd.Timestamp | None = None,
    local_timezone=None,
) -> MarketSession:
    periods = metadata.get("currentTradingPeriod", {})
    regular = periods.get("regular", {})
    if not regular.get("start") or not regular.get("end"):
        return MarketSession()
    exchange_timezone = str(metadata.get("exchangeTimezoneName", ""))
    start = _as_timestamp(regular["start"], exchange_timezone)
    end = _as_timestamp(regular["end"], exchange_timezone)
    now = now or pd.Timestamp.now(tz="UTC")
    now_exchange = now.tz_convert(exchange_timezone)
    status = "OPEN - REGULAR SESSION" if start <= now_exchange <= end else "CLOSED"
    for label, session_name in (("pre", "OPEN - PRE-MARKET"), ("post", "OPEN - POST-MARKET")):
        session = periods.get(label, {})
        if session.get("start") and session.get("end"):
            session_start = _as_timestamp(session["start"], exchange_timezone)
            session_end = _as_timestamp(session["end"], exchange_timezone)
            if session_start <= now_exchange <= session_end:
                status = session_name
    local_timezone = local_timezone or datetime.now().astimezone().tzinfo
    local_start = start.tz_convert(local_timezone)
    local_end = end.tz_convert(local_timezone)
    has_extended = bool(metadata.get("hasPrePostMarketData", False))
    return MarketSession(
        status=status,
        exchange_timezone=exchange_timezone,
        regular_exchange_hours=f"{start:%H:%M}-{end:%H:%M} {exchange_timezone}",
        regular_local_hours=f"{local_start:%H:%M}-{local_end:%H:%M} {local_start:%Z}",
        extended_session="Pre/Post available" if has_extended else "No pre/post indicated",
        overnight_session="Overnight not indicated by Yahoo",
    )


def _as_timestamp(value, timezone: str) -> pd.Timestamp:
    if isinstance(value, (int, float)):
        return pd.Timestamp(value, unit="s", tz="UTC").tz_convert(timezone)
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize(timezone) if timestamp.tzinfo is None else timestamp.tz_convert(timezone)


def _twelve_interval(interval: str) -> str:
    return {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "60m": "1h",
        "1d": "1day",
        "1wk": "1week",
        "1mo": "1month",
    }[interval]


def _twelve_output_size(range_spec: RangeSpec) -> int:
    return {
        "1d": 500,
        "5d": 2000,
        "1mo": 5000,
        "3mo": 100,
        "6mo": 150,
        "ytd": 300,
        "1y": 300,
        "5y": 1400,
        "max": 5000,
        "custom": 5000,
    }.get(range_spec.period, 500)


def _stooq_symbol(instrument: Instrument) -> str:
    symbol = instrument.symbol.lower()
    if any(character in symbol for character in ("=", "-", "^")):
        return ""
    if "." in symbol:
        return symbol
    return f"{symbol}.us"


def _stooq_start_date(range_spec: RangeSpec) -> str:
    if range_spec.start:
        return range_spec.start.replace("-", "")
    now = pd.Timestamp.now()
    if range_spec.period == "max":
        return "19000101"
    if range_spec.period == "ytd":
        return f"{now.year}0101"
    offsets = {
        "3mo": pd.DateOffset(months=3),
        "6mo": pd.DateOffset(months=6),
        "1y": pd.DateOffset(years=1),
        "5y": pd.DateOffset(years=5),
    }
    return (now - offsets.get(range_spec.period, pd.DateOffset(months=3))).strftime("%Y%m%d")


def _stooq_end_date(range_spec: RangeSpec) -> str:
    return range_spec.end.replace("-", "") if range_spec.end else pd.Timestamp.now().strftime("%Y%m%d")


def _clip_custom_range(frame: pd.DataFrame, range_spec: RangeSpec) -> pd.DataFrame:
    if not range_spec.start or not range_spec.end or frame.empty:
        return frame
    dates = pd.Index(pd.to_datetime(frame.index).date)
    start = pd.Timestamp(range_spec.start).date()
    end = pd.Timestamp(range_spec.end).date()
    result = frame.loc[(dates >= start) & (dates <= end)].copy()
    result.attrs.update(frame.attrs)
    return result


def _resample_ohlcv(frame: pd.DataFrame, frequency: str) -> pd.DataFrame:
    return frame.resample(frequency).agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    )


def score_history_frame(
    frame: pd.DataFrame,
    range_spec: RangeSpec,
    now: pd.Timestamp | None = None,
) -> DataQuality:
    source = str(frame.attrs.get("data_source", "Unknown source"))
    notes: list[str] = []
    if frame.empty or "Close" not in frame:
        return DataQuality(source, 0.0, False, 0, 0.0, 0.0, 0.0, 0.0, ("No close bars",))
    close = pd.to_numeric(frame["Close"], errors="coerce")
    bar_count = len(frame)
    valid = close.notna() & (close > 0)
    validity_score = float(valid.mean())
    if validity_score < 1:
        notes.append("Invalid closes")
    expected_columns = {"Open", "High", "Low", "Close", "Volume"}
    available = expected_columns.intersection(frame.columns)
    column_score = len(available) / len(expected_columns)
    value_score = float(frame[list(available)].notna().mean().mean()) if available else 0.0
    completeness_score = (column_score + value_score) / 2
    if completeness_score < 0.9:
        notes.append("Incomplete OHLCV")
    sorted_index = frame.index.is_monotonic_increasing and not frame.index.has_duplicates
    regularity_score = _regularity_score(frame.index, range_spec) if sorted_index else 0.0
    if regularity_score < 0.75:
        notes.append("Irregular timestamps")
    freshness_score = _freshness_score(frame.index[-1], range_spec, now)
    if freshness_score < 0.5:
        notes.append("Stale latest bar")
    count_score = min(1.0, bar_count / _minimum_useful_bars(range_spec))
    if count_score < 1:
        notes.append("Sparse history")
    score = round(
        100
        * (
            0.28 * freshness_score
            + 0.25 * validity_score
            + 0.20 * completeness_score
            + 0.17 * regularity_score
            + 0.10 * count_score
        ),
        1,
    )
    usable = validity_score == 1.0 and bar_count >= 2
    return DataQuality(
        source=source,
        score=score,
        usable=usable,
        bar_count=bar_count,
        freshness_score=freshness_score,
        completeness_score=completeness_score,
        validity_score=validity_score,
        regularity_score=regularity_score,
        notes=tuple(notes),
    )


def _quality_summary(quality: DataQuality) -> str:
    suffix = f" ({', '.join(quality.notes)})" if quality.notes else ""
    return f"{quality.source} {quality.score:.1f}/100{suffix}"


def _minimum_useful_bars(range_spec: RangeSpec) -> int:
    return {
        "1d": 20,
        "5d": 20,
        "1mo": 15,
        "3mo": 20,
        "6mo": 20,
        "ytd": 20,
        "1y": 20,
        "5y": 20,
        "max": 20,
    }.get(range_spec.period, 10)


def _freshness_score(
    last_index,
    range_spec: RangeSpec,
    now: pd.Timestamp | None = None,
) -> float:
    now = now or pd.Timestamp.now(tz="UTC")
    last = pd.Timestamp(last_index)
    if last.tzinfo is None:
        last = last.tz_localize("UTC")
    else:
        last = last.tz_convert("UTC")
    age = now - last
    tolerance = (
        pd.Timedelta(days=7)
        if range_spec.interval in {"1d", "1wk", "1mo"}
        else pd.Timedelta(days=4)
    )
    if age <= tolerance:
        return 1.0
    if age <= tolerance * 4:
        return 0.4
    return 0.0


def _regularity_score(index: pd.Index, range_spec: RangeSpec) -> float:
    if len(index) < 3:
        return 1.0
    deltas = pd.Series(index).diff().dropna().dt.total_seconds()
    if range_spec.interval.endswith("m") or range_spec.interval == "60m":
        expected = pd.Timedelta(range_spec.interval).total_seconds()
        deltas = deltas[deltas <= expected * 2.1]
    elif range_spec.interval == "1d":
        deltas = deltas[deltas <= pd.Timedelta(days=4).total_seconds()]
    elif range_spec.interval == "1wk":
        deltas = deltas[deltas <= pd.Timedelta(days=10).total_seconds()]
    if deltas.empty:
        return 0.5
    median = float(deltas.median())
    if median <= 0:
        return 0.0
    variation = float((deltas - median).abs().median() / median)
    return max(0.0, 1.0 - variation)
