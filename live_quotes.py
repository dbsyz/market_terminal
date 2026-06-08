from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import requests

from .models import Instrument, QuoteSnapshot


_US_STYLE_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_CRYPTO_QUOTES = ("USDT", "USD", "USDC", "EUR", "GBP", "BTC", "ETH")
_CRYPTO_DEFAULT_BASES = {
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "BNB",
    "ADA",
    "DOGE",
    "AVAX",
    "LINK",
    "DOT",
    "LTC",
    "BCH",
    "TRX",
    "TON",
    "UNI",
    "AAVE",
}
_KRAKEN_BASE_ALIASES = {"BTC": "XBT"}


class LiveQuoteService:
    """Best-effort live snapshot router.

    The desktop app refresh loop polls this service. Public WebSocket feeds can
    later update the same QuoteSnapshot contract without changing the UI.
    """

    def __init__(
        self,
        binance: "BinanceLiveQuoteClient | None" = None,
        kraken: "KrakenLiveQuoteClient | None" = None,
        coinbase: "CoinbaseLiveQuoteClient | None" = None,
        nfin: "NfinLiveQuoteClient | None" = None,
        alpaca: "AlpacaLiveQuoteClient | None" = None,
        finnhub: "FinnhubLiveQuoteClient | None" = None,
        twelve: "TwelveDataLiveQuoteClient | None" = None,
    ) -> None:
        self.binance = binance or BinanceLiveQuoteClient()
        self.kraken = kraken or KrakenLiveQuoteClient()
        self.coinbase = coinbase or CoinbaseLiveQuoteClient()
        self.nfin = nfin or NfinLiveQuoteClient()
        self.alpaca = alpaca or AlpacaLiveQuoteClient()
        self.finnhub = finnhub or FinnhubLiveQuoteClient()
        self.twelve = twelve or TwelveDataLiveQuoteClient()

    def quote_snapshot(self, instrument: Instrument) -> QuoteSnapshot | None:
        providers = (
            (self.binance, self.kraken, self.coinbase)
            if is_crypto_instrument(instrument)
            else (self.nfin, self.alpaca, self.finnhub, self.twelve)
        )
        for provider in providers:
            if not provider.enabled:
                continue
            try:
                quote = provider.quote_snapshot(instrument)
            except Exception:
                continue
            if quote.last is not None or quote.bid is not None or quote.ask is not None:
                return quote
        return None


class BinanceLiveQuoteClient:
    endpoint = "https://api.binance.com"

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return os.getenv("BINANCE_DISABLE", "0") != "1"

    def quote_snapshot(self, instrument: Instrument) -> QuoteSnapshot:
        symbol = binance_symbol_from_instrument(instrument)
        if not symbol:
            raise RuntimeError("Instrument is not a Binance spot crypto pair")
        response = self.session.get(
            f"{self.endpoint}/api/v3/ticker/24hr",
            params={"symbol": symbol},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        return QuoteSnapshot(
            last=_as_float(payload.get("lastPrice")),
            bid=_as_float(payload.get("bidPrice")),
            ask=_as_float(payload.get("askPrice")),
            change=_as_float(payload.get("priceChange")),
            change_percent=_as_float(payload.get("priceChangePercent")),
            volume=_as_float(payload.get("volume")),
            market_state="OPEN - 24/7 CRYPTO",
            source="Binance Spot",
            source_detail="Public 24h ticker snapshot",
            as_of=_epoch_millis(payload.get("closeTime")),
        )


class KrakenLiveQuoteClient:
    endpoint = "https://api.kraken.com"

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return os.getenv("KRAKEN_DISABLE", "0") != "1"

    def quote_snapshot(self, instrument: Instrument) -> QuoteSnapshot:
        pair = kraken_pair_from_instrument(instrument)
        if not pair:
            raise RuntimeError("Instrument is not a Kraken spot crypto pair")
        response = self.session.get(
            f"{self.endpoint}/0/public/Ticker",
            params={"pair": pair},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError("; ".join(payload["error"]))
        rows = payload.get("result") or {}
        row = next(iter(rows.values())) if isinstance(rows, dict) and rows else {}
        last = _first_row_float(row, "c", 0)
        open_price = _as_float(row.get("o"))
        change = last - open_price if last is not None and open_price is not None else None
        change_percent = (
            change / open_price * 100 if change is not None and open_price not in (None, 0) else None
        )
        return QuoteSnapshot(
            last=last,
            bid=_first_row_float(row, "b", 0),
            ask=_first_row_float(row, "a", 0),
            change=change,
            change_percent=change_percent,
            volume=_first_row_float(row, "v", 1),
            market_state="OPEN - 24/7 CRYPTO",
            source="Kraken",
            source_detail="Public REST ticker snapshot",
            as_of=datetime.now(timezone.utc),
        )


class CoinbaseLiveQuoteClient:
    endpoint = "https://api.exchange.coinbase.com"

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return os.getenv("COINBASE_DISABLE", "0") != "1"

    def quote_snapshot(self, instrument: Instrument) -> QuoteSnapshot:
        product = coinbase_product_from_instrument(instrument)
        if not product:
            raise RuntimeError("Instrument is not a Coinbase spot crypto product")
        response = self.session.get(
            f"{self.endpoint}/products/{product}/ticker",
            params={},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        return QuoteSnapshot(
            last=_as_float(payload.get("price")),
            bid=_as_float(payload.get("bid")),
            ask=_as_float(payload.get("ask")),
            volume=_as_float(payload.get("volume")),
            market_state="OPEN - 24/7 CRYPTO",
            source="Coinbase Exchange",
            source_detail="Public ticker snapshot",
            as_of=_parse_datetime(payload.get("time")) or datetime.now(timezone.utc),
        )


class NfinLiveQuoteClient:
    endpoint = "https://api.nfin.dev"

    def __init__(
        self,
        api_key: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("NFIN_API_KEY", "")
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return os.getenv("NFIN_DISABLE", "0") != "1"

    def quote_snapshot(self, instrument: Instrument) -> QuoteSnapshot:
        symbol = nfin_quote_symbol(instrument)
        if not symbol:
            raise RuntimeError("Instrument is not a US-style listed symbol")
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-Nfin-Key"] = self.api_key
        response = self.session.get(
            f"{self.endpoint}/v1/quote/{symbol}",
            headers=headers,
            timeout=8,
        )
        response.raise_for_status()
        row = _first_mapping(response.json())
        last = _first_payload_float(
            row,
            "lastSalePrice",
            "lastPrice",
            "regularMarketPrice",
            "price",
            "close",
            "last",
        )
        previous = _first_payload_float(row, "previousClose", "prevClose", "priorClose")
        change = _first_payload_float(row, "netChange", "change", "regularMarketChange")
        change_percent = _first_payload_float(
            row, "percentageChange", "percentChange", "changePercent", "regularMarketChangePercent"
        )
        if change is None and last is not None and previous not in (None, 0):
            change = last - previous
        if change_percent is None and change is not None and previous not in (None, 0):
            change_percent = change / previous * 100
        return QuoteSnapshot(
            last=last,
            bid=_first_payload_float(row, "bidPrice", "bid"),
            ask=_first_payload_float(row, "askPrice", "ask"),
            change=change,
            change_percent=change_percent,
            volume=_first_payload_float(row, "volume", "shareVolume", "totalVolume"),
            market_state=_first_payload_string(row, "marketStatus", "marketState") or "US QUOTE",
            source="nfin Nasdaq",
            source_detail="No-key Nasdaq quote snapshot",
            as_of=_first_payload_datetime(row, "lastTradeTimestamp", "lastSaleTime", "timestamp"),
        )


class AlpacaLiveQuoteClient:
    endpoint = "https://data.alpaca.markets"

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("ALPACA_API_KEY_ID", "")
        self.api_secret = (
            api_secret if api_secret is not None else os.getenv("ALPACA_API_SECRET_KEY", "")
        )
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def quote_snapshot(self, instrument: Instrument) -> QuoteSnapshot:
        symbol = nfin_quote_symbol(instrument)
        if not symbol:
            raise RuntimeError("Instrument is not a US-style listed symbol")
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }
        quote = self._get(f"/v2/stocks/{symbol}/quotes/latest", headers).get("quote") or {}
        trade = self._get(f"/v2/stocks/{symbol}/trades/latest", headers).get("trade") or {}
        return QuoteSnapshot(
            last=_first_payload_float(trade, "p", "price"),
            bid=_first_payload_float(quote, "bp", "bidPrice"),
            ask=_first_payload_float(quote, "ap", "askPrice"),
            volume=_first_payload_float(trade, "s", "size"),
            market_state="IEX REAL-TIME",
            source="Alpaca IEX",
            source_detail="Free Basic IEX latest quote/trade",
            as_of=_first_payload_datetime(trade, "t") or _first_payload_datetime(quote, "t"),
        )

    def _get(self, route: str, headers: dict[str, str]) -> dict:
        response = self.session.get(f"{self.endpoint}{route}", headers=headers, timeout=8)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}


class FinnhubLiveQuoteClient:
    endpoint = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("FINNHUB_API_KEY", "")
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def quote_snapshot(self, instrument: Instrument) -> QuoteSnapshot:
        symbol = instrument.symbol.strip().upper()
        response = self.session.get(
            f"{self.endpoint}/quote",
            params={"symbol": symbol, "token": self.api_key},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        return QuoteSnapshot(
            last=_as_float(payload.get("c")),
            change=_as_float(payload.get("d")),
            change_percent=_as_float(payload.get("dp")),
            market_state="FINNHUB QUOTE",
            source="Finnhub",
            source_detail="Free-tier quote endpoint",
            as_of=_epoch_seconds(payload.get("t")),
        )


class TwelveDataLiveQuoteClient:
    endpoint = "https://api.twelvedata.com"

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("TWELVE_DATA_API_KEY", "")
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def quote_snapshot(self, instrument: Instrument) -> QuoteSnapshot:
        response = self.session.get(
            f"{self.endpoint}/quote",
            params={"symbol": instrument.symbol.strip(), "apikey": self.api_key},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("status", "")).lower() == "error":
            raise RuntimeError(str(payload.get("message", "Twelve Data quote error")))
        last = _first_payload_float(payload, "close", "price", "last")
        previous = _first_payload_float(payload, "previous_close", "previousClose")
        change = _first_payload_float(payload, "change")
        change_percent = _first_payload_float(payload, "percent_change", "percentChange")
        if change is None and last is not None and previous not in (None, 0):
            change = last - previous
        if change_percent is None and change is not None and previous not in (None, 0):
            change_percent = change / previous * 100
        return QuoteSnapshot(
            last=last,
            bid=_first_payload_float(payload, "bid"),
            ask=_first_payload_float(payload, "ask"),
            change=change,
            change_percent=change_percent,
            volume=_first_payload_float(payload, "volume"),
            market_state="TWELVE DATA QUOTE",
            source="Twelve Data",
            source_detail="Configured quote endpoint",
            as_of=_first_payload_datetime(payload, "timestamp", "datetime"),
        )


def is_crypto_instrument(instrument: Instrument) -> bool:
    quote_type = instrument.quote_type.upper()
    symbol = instrument.symbol.upper()
    base_quote = crypto_base_quote(instrument)
    return (
        "CRYPTO" in quote_type
        or instrument.exchange.upper() in {"BINANCE", "KRAKEN", "COINBASE"}
        or (base_quote is not None and (("-" in symbol) or ("/" in symbol)))
        or symbol in _CRYPTO_DEFAULT_BASES
        or any(symbol.endswith(quote) and symbol[: -len(quote)] in _CRYPTO_DEFAULT_BASES for quote in _CRYPTO_QUOTES)
    )


def binance_symbol_from_instrument(instrument: Instrument) -> str:
    base_quote = crypto_base_quote(instrument)
    if not base_quote:
        return ""
    base, quote = base_quote
    if quote == "USD":
        quote = "USDT"
    if quote not in {"USDT", "FDUSD", "USDC", "BTC", "ETH", "BNB", "EUR", "GBP"}:
        return ""
    return f"{base}{quote}"


def kraken_pair_from_instrument(instrument: Instrument) -> str:
    base_quote = crypto_base_quote(instrument)
    if not base_quote:
        return ""
    base, quote = base_quote
    if quote == "USDC":
        return ""
    base = _KRAKEN_BASE_ALIASES.get(base, base)
    return f"{base}{quote}"


def coinbase_product_from_instrument(instrument: Instrument) -> str:
    base_quote = crypto_base_quote(instrument)
    if not base_quote:
        return ""
    base, quote = base_quote
    if quote == "USDT":
        quote = "USD"
    if quote not in {"USD", "USDC", "EUR", "GBP", "BTC", "ETH"}:
        return ""
    return f"{base}-{quote}"


def crypto_base_quote(instrument: Instrument) -> tuple[str, str] | None:
    symbol = instrument.symbol.strip().upper().replace("/", "-")
    if "-" in symbol:
        base, quote = symbol.split("-", 1)
        if base and quote in _CRYPTO_QUOTES:
            return base, quote
    for quote in _CRYPTO_QUOTES:
        if len(symbol) > len(quote) and symbol.endswith(quote):
            base = symbol[: -len(quote)]
            if base:
                return base, quote
    if symbol in _CRYPTO_DEFAULT_BASES:
        return symbol, "USD"
    return None


def nfin_quote_symbol(instrument: Instrument) -> str:
    symbol = instrument.symbol.strip().upper()
    quote_type = instrument.quote_type.upper()
    if is_crypto_instrument(instrument) or "." in symbol or "/" in symbol:
        return ""
    if quote_type and quote_type not in {"EQUITY", "STOCK", "ETF", "FUND", "MUTUALFUND"}:
        return ""
    return symbol if _US_STYLE_SYMBOL.fullmatch(symbol) else ""


def _as_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _first_row_float(row: dict, key: str, index: int) -> float | None:
    values = row.get(key)
    if not isinstance(values, list) or len(values) <= index:
        return None
    return _as_float(values[index])


def _first_payload_float(row: dict, *keys: str) -> float | None:
    for key in keys:
        value = _nested_get(row, key)
        result = _as_float(value)
        if result is not None:
            return result
    return None


def _first_payload_string(row: dict, *keys: str) -> str:
    for key in keys:
        value = _nested_get(row, key)
        if value not in (None, ""):
            return str(value)
    return ""


def _first_payload_datetime(row: dict, *keys: str) -> datetime | None:
    for key in keys:
        parsed = _parse_datetime(_nested_get(row, key))
        if parsed is not None:
            return parsed
    return None


def _nested_get(row: dict, key: str) -> object:
    if key in row:
        return row[key]
    lower = key.lower()
    for current_key, value in row.items():
        if str(current_key).lower() == lower:
            return value
    return None


def _first_mapping(value: object) -> dict:
    if isinstance(value, dict):
        for key in ("data", "quote", "summary", "rows", "result"):
            nested = value.get(key)
            found = _first_mapping(nested)
            if found:
                return found
        if any(_as_float(v) is not None for v in value.values()):
            return value
        for nested in value.values():
            found = _first_mapping(nested)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _first_mapping(item)
            if found:
                return found
    return {}


def _parse_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return _epoch_seconds(value)
    text = str(value).strip()
    if text.isdigit():
        number = int(text)
        return _epoch_millis(number) if number > 10_000_000_000 else _epoch_seconds(number)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _epoch_seconds(value: object) -> datetime | None:
    number = _as_float(value)
    if number is None or number <= 0:
        return None
    return datetime.fromtimestamp(number, tz=timezone.utc)


def _epoch_millis(value: object) -> datetime | None:
    number = _as_float(value)
    if number is None or number <= 0:
        return None
    return datetime.fromtimestamp(number / 1000, tz=timezone.utc)
