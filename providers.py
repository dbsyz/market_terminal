from __future__ import annotations

import os
import re
from datetime import datetime
from dataclasses import dataclass, replace
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
import yfinance.cache as yf_cache

from .models import Instrument, MarketSession, RangeSpec
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
_YAHOO_TO_EURONEXT_MIC = {
    ".AS": "XAMS",
    ".BR": "XBRU",
    ".IR": "XDUB",
    ".MI": "XMIL",
    ".OL": "XOSL",
    ".PA": "XPAR",
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


class MarketDataProvider:
    def __init__(
        self,
        figi: OpenFigiClient | None = None,
        twelve: TwelveDataClient | None = None,
        stooq: StooqClient | None = None,
    ) -> None:
        configure_yfinance_cache()
        self.figi = figi or OpenFigiClient()
        self.twelve = twelve or TwelveDataClient()
        self.stooq = stooq or StooqClient()

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
        if instrument.isin:
            return instrument
        try:
            isin = str(yf.Ticker(instrument.symbol).get_isin() or "").strip()
        except Exception:
            isin = ""
        if not isin or isin == "-":
            isin = _lookup_isin_by_euronext_listing(instrument.symbol)
        if not isin:
            return instrument
        return replace(instrument, isin=isin)

    def _search_yahoo(self, query: str) -> list[Instrument]:
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
        attempts = [self._history_yahoo]
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
        frame = yf.Ticker(instrument.symbol).history(**request)
        if frame.empty:
            return frame
        required_columns = ["Open", "High", "Low", "Close", "Volume"]
        available = [column for column in required_columns if column in frame.columns]
        result = frame[available].dropna(subset=["Close"])
        result.attrs["data_source"] = "Yahoo Finance"
        return result

    def market_session(self, instrument: Instrument) -> MarketSession:
        if instrument.symbol.upper() == PORTFOLIO_INDEX_SYMBOL:
            return portfolio_market_session()
        metadata = yf.Ticker(instrument.symbol).get_history_metadata()
        return build_market_session(metadata)


def _unique_instruments(instruments: list[Instrument]) -> list[Instrument]:
    unique: dict[str, Instrument] = {}
    for instrument in instruments:
        unique.setdefault(instrument.symbol, instrument)
    return list(unique.values())


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
