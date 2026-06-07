from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import market_terminal.portfolio_index as portfolio_index
from market_terminal.models import HISTORICAL_RANGES, INTRADAY_RANGES, Instrument, RangeSpec
from market_terminal.models import QuoteSnapshot
from market_terminal.portfolio_index import _first_trade_date, build_portfolio_monitor_report, portfolio_index_files
from market_terminal.providers import (
    BinanceSpotClient,
    MarketDataProvider,
    OpenFigiClient,
    StooqClient,
    TwelveDataClient,
    _unique_instruments,
    binance_symbol_from_query,
    build_market_session,
    detect_identifier,
    score_history_frame,
    yahoo_symbol_from_terminal_query,
)

import pandas as pd


class IdentifierDetectionTests(unittest.TestCase):
    def test_recognizes_supported_identifiers(self) -> None:
        self.assertEqual(detect_identifier("US0378331005"), "ID_ISIN")
        self.assertEqual(detect_identifier("037833100"), "ID_CUSIP")
        self.assertEqual(detect_identifier("BBG000B9XRY4"), "ID_BB_GLOBAL")

    def test_does_not_treat_company_text_as_cusip(self) -> None:
        self.assertIsNone(detect_identifier("MICROSOFT"))
        self.assertIsNone(detect_identifier("AAPL"))

    def test_translates_terminal_ticker_and_exchange_mnemonic(self) -> None:
        self.assertEqual(yahoo_symbol_from_terminal_query("KRW FP"), "KRW.PA")
        self.assertEqual(yahoo_symbol_from_terminal_query("KRW FP Equity"), "KRW.PA")
        self.assertIsNone(yahoo_symbol_from_terminal_query("KRW ZZ"))

    def test_translates_crypto_symbols_to_binance_spot_pairs(self) -> None:
        self.assertEqual(binance_symbol_from_query("BTC-USD"), "BTCUSDT")
        self.assertEqual(binance_symbol_from_query("ETH/EUR"), "ETHEUR")
        self.assertEqual(binance_symbol_from_query("SOL"), "SOLUSDT")
        self.assertEqual(binance_symbol_from_query("BTCUSDT"), "BTCUSDT")
        self.assertEqual(binance_symbol_from_query("AAPL"), "")
        self.assertEqual(binance_symbol_from_query("BRK-B"), "")

    def test_de_duplicates_symbols_without_reordering(self) -> None:
        apple = Instrument("AAPL", "Apple")
        duplicate = Instrument("AAPL", "Apple Inc")
        microsoft = Instrument("MSFT", "Microsoft")
        self.assertEqual(_unique_instruments([apple, duplicate, microsoft]), [apple, microsoft])


class StubResponse:
    def raise_for_status(self) -> None:
        pass

    def json(self):
        return [
            {
                "data": [
                    {
                        "ticker": "AAPL",
                        "name": "APPLE INC",
                        "exchCode": "US",
                        "marketSector": "Equity",
                        "figi": "BBG000B9XRY4",
                    }
                ]
            }
        ]


class StubSession:
    def __init__(self) -> None:
        self.request = None

    def post(self, endpoint, json, headers, timeout):
        self.request = (endpoint, json, headers, timeout)
        return StubResponse()


class RequestResponse:
    def __init__(self, json_payload=None, text="") -> None:
        self.json_payload = json_payload
        self.text = text

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self.json_payload


class GetSession:
    def __init__(self, response) -> None:
        self.response = response
        self.request = None

    def get(self, endpoint, params, timeout):
        self.request = (endpoint, params, timeout)
        return self.response


class OpenFigiTests(unittest.TestCase):
    def test_maps_identifier_and_passes_optional_key(self) -> None:
        session = StubSession()
        client = OpenFigiClient(api_key="key", session=session)

        instruments = client.map_identifier("us0378331005", "ID_ISIN")

        self.assertEqual(instruments[0].symbol, "AAPL")
        self.assertEqual(instruments[0].figi, "BBG000B9XRY4")
        self.assertEqual(instruments[0].isin, "US0378331005")
        self.assertEqual(session.request[1], [{"idType": "ID_ISIN", "idValue": "US0378331005"}])
        self.assertEqual(session.request[2]["X-OPENFIGI-APIKEY"], "key")


class StubFigi:
    def map_identifier(self, query, identifier_type):
        return [Instrument("AAPL", "APPLE INC", figi="BBG000B9XRY4", source="OpenFIGI")]


class StubMarketProvider(MarketDataProvider):
    def __init__(self) -> None:
        self.figi = StubFigi()

    def _search_yahoo(self, query):
        return [
            Instrument("AAPL", "Apple Inc.", exchange="NASDAQ", currency="USD"),
            Instrument("AAPU", "Direxion Daily AAPL Bull 2X Shares", exchange="NASDAQ"),
        ]


class MarketDataProviderTests(unittest.TestCase):
    def test_identifier_mapping_does_not_return_fuzzy_ticker_matches(self) -> None:
        instruments = StubMarketProvider().search("US0378331005")

        self.assertEqual([instrument.symbol for instrument in instruments], ["AAPL"])
        self.assertEqual(instruments[0].figi, "BBG000B9XRY4")
        self.assertEqual(instruments[0].isin, "US0378331005")
        self.assertEqual(instruments[0].source, "Yahoo Finance + OpenFIGI")

    def test_configured_secondary_search_survives_yahoo_failure(self) -> None:
        class TwelveStub:
            enabled = True

            def search(self, query):
                return [Instrument("MSFT", "Microsoft", source="Twelve Data")]

        provider = MarketDataProvider.__new__(MarketDataProvider)
        provider.twelve = TwelveStub()
        provider.figi = StubFigi()
        provider._search_yahoo = lambda query: (_ for _ in ()).throw(RuntimeError("down"))

        instruments = provider.search("Microsoft")

        self.assertEqual(instruments[0].symbol, "MSFT")
        self.assertEqual(instruments[0].source, "Twelve Data")

    def test_terminal_ticker_search_prioritizes_translated_listing(self) -> None:
        class TwelveStub:
            enabled = False

        requests = []
        provider = MarketDataProvider.__new__(MarketDataProvider)
        provider.twelve = TwelveStub()

        def search_yahoo(query):
            requests.append(query)
            if query == "KRW.PA":
                return [Instrument("KRW.PA", "Amundi Korea", exchange="Paris")]
            return []

        provider._search_yahoo = search_yahoo

        instruments = provider.search("KRW FP")

        self.assertEqual(requests[0], "KRW.PA")
        self.assertEqual(instruments[0].symbol, "KRW.PA")

    def test_identifier_mapping_uses_listing_venue_for_yahoo_enrichment(self) -> None:
        class FrenchListingFigi:
            def map_identifier(self, query, identifier_type):
                return [
                    Instrument(
                        "KRW",
                        "Amundi Korea",
                        exchange="FP",
                        source="OpenFIGI",
                        figi="BBG00EXAMPLE",
                    )
                ]

        requests = []
        provider = MarketDataProvider.__new__(MarketDataProvider)
        provider.figi = FrenchListingFigi()

        def search_yahoo(query):
            requests.append(query)
            return [Instrument("KRW.PA", "Amundi Korea", exchange="Paris")]

        provider._search_yahoo = search_yahoo

        instruments = provider.search("LU1900066975")

        self.assertEqual(requests, ["KRW.PA"])
        self.assertEqual(instruments[0].symbol, "KRW.PA")
        self.assertEqual(instruments[0].source, "Yahoo Finance + OpenFIGI")

    def test_searches_local_fort_pnl_portfolio_index(self) -> None:
        with temporary_portfolio_index_dir() as out_dir:
            original = os.environ.get("FORT_PNL_OUT_DIR")
            os.environ["FORT_PNL_OUT_DIR"] = str(out_dir)
            try:
                provider = MarketDataProvider.__new__(MarketDataProvider)
                instruments = provider.search("FORT_PNL")
            finally:
                if original is None:
                    os.environ.pop("FORT_PNL_OUT_DIR", None)
                else:
                    os.environ["FORT_PNL_OUT_DIR"] = original

        self.assertEqual(instruments[0].symbol, "FORT_PNL")
        self.assertEqual(instruments[0].quote_type, "Portfolio Index")
        self.assertEqual(instruments[0].exchange, "USER OWNED")
        self.assertEqual(instruments[0].currency, "EUR")
        self.assertEqual(instruments[0].market_cap, 1666.67)
        self.assertEqual(instruments[0].aum, 1666.67)

    def test_loads_local_fort_pnl_index_levels_as_history(self) -> None:
        with temporary_portfolio_index_dir() as out_dir:
            original = os.environ.get("FORT_PNL_OUT_DIR")
            original_disable = os.environ.get("FORT_PNL_DISABLE_SYNTHETIC_HISTORY")
            os.environ["FORT_PNL_OUT_DIR"] = str(out_dir)
            os.environ["FORT_PNL_DISABLE_SYNTHETIC_HISTORY"] = "1"
            try:
                provider = MarketDataProvider.__new__(MarketDataProvider)
                frame = provider.history(Instrument("FORT_PNL", "FORT PNL"), HISTORICAL_RANGES[-1])
            finally:
                if original is None:
                    os.environ.pop("FORT_PNL_OUT_DIR", None)
                else:
                    os.environ["FORT_PNL_OUT_DIR"] = original
                if original_disable is None:
                    os.environ.pop("FORT_PNL_DISABLE_SYNTHETIC_HISTORY", None)
                else:
                    os.environ["FORT_PNL_DISABLE_SYNTHETIC_HISTORY"] = original_disable

        self.assertEqual(list(frame["Close"]), [100.0, 123.5])
        self.assertEqual(frame.attrs["data_source"], "FORT_PNL local index levels")

    def test_local_fort_pnl_index_respects_selected_short_range(self) -> None:
        with temporary_portfolio_index_dir() as out_dir:
            original = os.environ.get("FORT_PNL_OUT_DIR")
            original_disable = os.environ.get("FORT_PNL_DISABLE_SYNTHETIC_HISTORY")
            os.environ["FORT_PNL_OUT_DIR"] = str(out_dir)
            os.environ["FORT_PNL_DISABLE_SYNTHETIC_HISTORY"] = "1"
            try:
                provider = MarketDataProvider.__new__(MarketDataProvider)
                frame = provider.history(Instrument("FORT_PNL", "FORT PNL"), INTRADAY_RANGES[0])
            finally:
                if original is None:
                    os.environ.pop("FORT_PNL_OUT_DIR", None)
                else:
                    os.environ["FORT_PNL_OUT_DIR"] = original
                if original_disable is None:
                    os.environ.pop("FORT_PNL_DISABLE_SYNTHETIC_HISTORY", None)
                else:
                    os.environ["FORT_PNL_DISABLE_SYNTHETIC_HISTORY"] = original_disable

        self.assertEqual(len(frame), 1)
        self.assertEqual(float(frame["Close"].iloc[-1]), 123.5)

    def test_local_fort_pnl_extends_snapshot_with_current_weight_prices(self) -> None:
        prices = pd.DataFrame(
            {
                "AAA": [0.6, 0.72, 0.78],
                "BBB": [0.4, 0.48, 0.52],
            },
            index=pd.to_datetime(["2026-01-01", "2026-05-29", "2026-06-02"]),
        )

        def download_stub(_constituents, _start, _as_of):
            return prices

        with temporary_portfolio_index_dir() as out_dir:
            original = os.environ.get("FORT_PNL_OUT_DIR")
            original_download = portfolio_index._download_weighted_constituent_prices
            original_current_date = portfolio_index._current_date
            os.environ["FORT_PNL_OUT_DIR"] = str(out_dir)
            portfolio_index._download_weighted_constituent_prices = download_stub
            portfolio_index._current_date = lambda: pd.Timestamp("2026-06-02")
            try:
                provider = MarketDataProvider.__new__(MarketDataProvider)
                frame = provider.history(Instrument("FORT_PNL", "FORT PNL"), HISTORICAL_RANGES[-1])
            finally:
                portfolio_index._download_weighted_constituent_prices = original_download
                portfolio_index._current_date = original_current_date
                if original is None:
                    os.environ.pop("FORT_PNL_OUT_DIR", None)
                else:
                    os.environ["FORT_PNL_OUT_DIR"] = original

        self.assertEqual(frame.index[-1], pd.Timestamp("2026-06-02"))
        self.assertAlmostEqual(float(frame.loc[pd.Timestamp("2026-05-29"), "Close"]), 123.5)
        self.assertGreater(float(frame["Close"].iloc[-1]), 123.5)
        self.assertIn("live-estimated", frame.attrs["data_source"])
        self.assertEqual(frame.attrs["portfolio_snapshot_date"], "2026-05-31")

    def test_local_fort_pnl_appends_market_extension_after_latest_official_level(self) -> None:
        prices = pd.DataFrame(
            {
                "AAA": [0.72, 0.78],
                "BBB": [0.48, 0.52],
            },
            index=pd.to_datetime(["2026-05-29", "2026-06-02"]),
        )

        def download_stub(_constituents, _start, _as_of):
            return prices

        with temporary_portfolio_index_dir() as out_dir:
            (out_dir / "fort_pnl_index_levels.csv").write_text(
                "\n".join(
                    [
                        "index_name,date,index_level,note",
                        "FORT_PNL,2026-01-01,100,Base level",
                        "FORT_PNL,2026-03-31,110,Official quarter level",
                        "FORT_PNL,2026-05-31,123.5,Latest official level",
                    ]
                ),
                encoding="utf-8",
            )
            original = os.environ.get("FORT_PNL_OUT_DIR")
            original_download = portfolio_index._download_weighted_constituent_prices
            original_current_date = portfolio_index._current_date
            os.environ["FORT_PNL_OUT_DIR"] = str(out_dir)
            portfolio_index._download_weighted_constituent_prices = download_stub
            portfolio_index._current_date = lambda: pd.Timestamp("2026-06-02")
            try:
                provider = MarketDataProvider.__new__(MarketDataProvider)
                frame = provider.history(Instrument("FORT_PNL", "FORT PNL"), HISTORICAL_RANGES[-1])
            finally:
                portfolio_index._download_weighted_constituent_prices = original_download
                portfolio_index._current_date = original_current_date
                if original is None:
                    os.environ.pop("FORT_PNL_OUT_DIR", None)
                else:
                    os.environ["FORT_PNL_OUT_DIR"] = original

        self.assertEqual(frame.index[-1], pd.Timestamp("2026-06-02"))
        self.assertEqual(float(frame.loc[pd.Timestamp("2026-05-31"), "Close"]), 123.5)
        self.assertGreater(float(frame["Close"].iloc[-1]), 123.5)
        self.assertIn("live-estimated", frame.attrs["data_source"])
        self.assertEqual(frame.attrs["portfolio_snapshot_date"], "2026-05-31")

    def test_local_fort_pnl_refreshes_outputs_when_pt_xls_is_newer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out_dir = root / "out"
            out_dir.mkdir()
            with temporary_portfolio_index_dir() as source_out:
                for path in source_out.iterdir():
                    (out_dir / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            workbook = root / "pt.xls"
            workbook.write_text("newer workbook", encoding="utf-8")
            os.utime(workbook, (4_000_000_000, 4_000_000_000))
            exporter = root / "export_fort_pnl_index.py"
            exporter.write_text("raise SystemExit(0)\n", encoding="utf-8")
            calls = []

            def run_stub(command, **kwargs):
                calls.append((command, kwargs))
                return None

            original_out = os.environ.get("FORT_PNL_OUT_DIR")
            original_root = os.environ.get("FORT_PNL_ROOT")
            original_run = portfolio_index.subprocess.run
            original_download = portfolio_index._download_weighted_constituent_prices
            original_disable = os.environ.get("FORT_PNL_DISABLE_SYNTHETIC_HISTORY")
            os.environ["FORT_PNL_OUT_DIR"] = str(out_dir)
            os.environ["FORT_PNL_ROOT"] = str(root)
            os.environ["FORT_PNL_DISABLE_SYNTHETIC_HISTORY"] = "1"
            portfolio_index.subprocess.run = run_stub
            portfolio_index._download_weighted_constituent_prices = lambda *_args: pd.DataFrame()
            try:
                provider = MarketDataProvider.__new__(MarketDataProvider)
                frame = provider.history(Instrument("FORT_PNL", "FORT PNL"), HISTORICAL_RANGES[-1])
            finally:
                portfolio_index.subprocess.run = original_run
                portfolio_index._download_weighted_constituent_prices = original_download
                for key, value in (
                    ("FORT_PNL_OUT_DIR", original_out),
                    ("FORT_PNL_ROOT", original_root),
                    ("FORT_PNL_DISABLE_SYNTHETIC_HISTORY", original_disable),
                ):
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(len(calls), 1)
        self.assertIn("export_fort_pnl_index.py", str(calls[0][0][1]))
        self.assertEqual(frame.attrs["portfolio_refresh_status"], "Refreshed FORT_PNL CSVs from latest pt.xls")

    def test_local_fort_pnl_constituent_quotes_use_latest_available_closes(self) -> None:
        closes = pd.DataFrame(
            {
                "AAA": [10.0, 10.5],
                "BBB": [20.0, None],
            },
            index=pd.to_datetime(["2026-06-01", "2026-06-02"]),
        )

        def closes_stub(_symbols, _start, _end):
            return closes

        with temporary_portfolio_index_dir() as out_dir:
            original = os.environ.get("FORT_PNL_OUT_DIR")
            original_closes = portfolio_index._download_constituent_closes
            original_current_date = portfolio_index._current_date
            os.environ["FORT_PNL_OUT_DIR"] = str(out_dir)
            portfolio_index._download_constituent_closes = closes_stub
            portfolio_index._current_date = lambda: pd.Timestamp("2026-06-02")
            try:
                quotes = portfolio_index.portfolio_constituent_quotes()
            finally:
                portfolio_index._download_constituent_closes = original_closes
                portfolio_index._current_date = original_current_date
                if original is None:
                    os.environ.pop("FORT_PNL_OUT_DIR", None)
                else:
                    os.environ["FORT_PNL_OUT_DIR"] = original

        self.assertEqual([quote.ticker for quote in quotes], ["AAA", "BBB"])
        self.assertEqual(quotes[0].last_price, 10.5)
        self.assertEqual(quotes[0].last_updated, "2026-06-02 00:00")
        self.assertEqual(quotes[0].snapshot_price, 10.0)
        self.assertEqual(quotes[1].last_price, 20.0)
        self.assertEqual(quotes[1].last_updated, "2026-06-01 00:00")

    def test_local_fort_pnl_inception_date_comes_from_first_trade(self) -> None:
        with temporary_portfolio_index_dir() as out_dir:
            original = os.environ.get("FORT_PNL_OUT_DIR")
            os.environ["FORT_PNL_OUT_DIR"] = str(out_dir)
            try:
                inception = _first_trade_date(portfolio_index_files())
            finally:
                if original is None:
                    os.environ.pop("FORT_PNL_OUT_DIR", None)
                else:
                    os.environ["FORT_PNL_OUT_DIR"] = original

        self.assertEqual(inception, pd.Timestamp("2025-02-10"))

    def test_builds_trade_aware_portfolio_monitor_report(self) -> None:
        with temporary_portfolio_index_dir() as out_dir:
            original = os.environ.get("FORT_PNL_OUT_DIR")
            os.environ["FORT_PNL_OUT_DIR"] = str(out_dir)
            try:
                report = build_portfolio_monitor_report()
            finally:
                if original is None:
                    os.environ.pop("FORT_PNL_OUT_DIR", None)
                else:
                    os.environ["FORT_PNL_OUT_DIR"] = original

        self.assertIn("# FORT_PNL Monitor", report)
        self.assertIn("Top Movers", report)
        self.assertIn("Risk Concentration", report)
        self.assertIn("2026 Trade-Aware Monitoring", report)
        self.assertIn("Realized PnL from 2026 sells: EUR -25.00", report)

    def test_yahoo_search_carries_market_cap_for_result_sorting(self) -> None:
        class SearchStub:
            quotes = [
                {
                    "symbol": "AAPL",
                    "shortname": "Apple",
                    "exchDisp": "NASDAQ",
                    "marketCap": 3_000_000_000_000,
                }
            ]

        import market_terminal.providers as providers

        original_search = providers.yf.Search
        providers.yf.Search = lambda *_args, **_kwargs: SearchStub()
        try:
            provider = MarketDataProvider.__new__(MarketDataProvider)
            instruments = provider._search_yahoo("Apple")
        finally:
            providers.yf.Search = original_search

        self.assertEqual(instruments[0].market_cap, 3_000_000_000_000)

    def test_quote_snapshot_reads_fast_info_and_info_fallbacks(self) -> None:
        class TickerStub:
            fast_info = {"last_price": 101.5, "regular_market_change": 1.25}
            info = {
                "bid": 101.4,
                "ask": 101.6,
                "marketState": "REGULAR",
                "regularMarketChangePercent": 1.24,
                "regularMarketVolume": 123456,
            }

        import market_terminal.providers as providers

        original_ticker = providers.yf.Ticker
        providers.yf.Ticker = lambda _symbol: TickerStub()
        try:
            provider = MarketDataProvider.__new__(MarketDataProvider)
            quote = provider.quote_snapshot(Instrument("AAPL", "Apple"))
        finally:
            providers.yf.Ticker = original_ticker

        self.assertEqual(quote.last, 101.5)
        self.assertEqual(quote.bid, 101.4)
        self.assertEqual(quote.ask, 101.6)
        self.assertEqual(quote.change, 1.25)
        self.assertEqual(quote.change_percent, 1.24)
        self.assertEqual(quote.volume, 123456)
        self.assertEqual(quote.market_state, "REGULAR")

    def test_quote_snapshot_can_skip_slow_info_for_watchlist_ticks(self) -> None:
        class TickerStub:
            fast_info = {"last_price": 101.5, "previous_close": 100.0}

            @property
            def info(self):
                raise AssertionError("slow info should not be fetched")

        import market_terminal.providers as providers

        original_ticker = providers.yf.Ticker
        providers.yf.Ticker = lambda _symbol: TickerStub()
        try:
            provider = MarketDataProvider.__new__(MarketDataProvider)
            quote = provider.quote_snapshot(
                Instrument("AAPL", "Apple"),
                include_slow_info=False,
            )
        finally:
            providers.yf.Ticker = original_ticker

        self.assertEqual(quote.last, 101.5)
        self.assertIsNone(quote.bid)
        self.assertEqual(quote.change, 1.5)
        self.assertEqual(quote.change_percent, 1.5)

    def test_quote_snapshot_uses_short_lived_cache(self) -> None:
        calls = 0

        class TickerStub:
            @property
            def fast_info(self):
                nonlocal calls
                calls += 1
                return {"last_price": 101.5, "previous_close": 100.0}

        import market_terminal.providers as providers

        original_ticker = providers.yf.Ticker
        providers.yf.Ticker = lambda _symbol: TickerStub()
        try:
            provider = MarketDataProvider.__new__(MarketDataProvider)
            provider.binance = None
            provider._quote_info_cache = {}
            provider._quote_snapshot_cache = {}
            first = provider.quote_snapshot(Instrument("AAPL", "Apple"), include_slow_info=False)
            second = provider.quote_snapshot(Instrument("AAPL", "Apple"), include_slow_info=False)
        finally:
            providers.yf.Ticker = original_ticker

        self.assertEqual(first.last, 101.5)
        self.assertEqual(second.last, 101.5)
        self.assertEqual(calls, 1)

    def test_selected_yahoo_asset_is_enriched_with_isin(self) -> None:
        class TickerStub:
            info = {}

            def get_isin(self):
                return "LU1900066975"

        import market_terminal.providers as providers

        original_ticker = providers.yf.Ticker
        providers.yf.Ticker = lambda _symbol: TickerStub()
        try:
            provider = MarketDataProvider.__new__(MarketDataProvider)
            enriched = provider.instrument_details(Instrument("KRW.PA", "Amundi Korea"))
        finally:
            providers.yf.Ticker = original_ticker

        self.assertEqual(enriched.isin, "LU1900066975")

    def test_selected_yahoo_asset_is_enriched_with_market_cap_and_aum(self) -> None:
        class TickerStub:
            info = {"marketCap": 123_000_000_000, "totalAssets": 456_000_000_000}

            def get_isin(self):
                return "-"

        import market_terminal.providers as providers

        original_ticker = providers.yf.Ticker
        original_lookup = providers._lookup_isin_by_euronext_listing
        providers.yf.Ticker = lambda _symbol: TickerStub()
        providers._lookup_isin_by_euronext_listing = lambda _symbol: ""
        try:
            provider = MarketDataProvider.__new__(MarketDataProvider)
            enriched = provider.instrument_details(Instrument("SPY", "SPDR", quote_type="ETF"))
        finally:
            providers.yf.Ticker = original_ticker
            providers._lookup_isin_by_euronext_listing = original_lookup

        self.assertEqual(enriched.market_cap, 123_000_000_000)
        self.assertEqual(enriched.aum, 456_000_000_000)

    def test_market_events_normalizes_yahoo_calendar_and_earnings_dates(self) -> None:
        class TickerStub:
            def get_calendar(self):
                return {
                    "Earnings Date": [pd.Timestamp("2026-07-25 12:30", tz="UTC")],
                    "Ex-Dividend Date": pd.Timestamp("2026-08-01"),
                }

            def get_earnings_dates(self, limit=16):
                return pd.DataFrame(
                    {"EPS Estimate": [1.25]},
                    index=pd.DatetimeIndex([pd.Timestamp("2026-07-25 12:30", tz="UTC")]),
                )

        import market_terminal.providers as providers

        original_ticker = providers.yf.Ticker
        providers.yf.Ticker = lambda _symbol: TickerStub()
        try:
            provider = MarketDataProvider.__new__(MarketDataProvider)
            provider.binance = None
            events = provider.market_events(Instrument("AAPL", "Apple"))
        finally:
            providers.yf.Ticker = original_ticker

        self.assertEqual([event.event_type for event in events], ["Earnings", "Dividend"])
        self.assertEqual(events[0].event, "Earnings")
        self.assertEqual(events[0].source, "Yahoo Finance calendar")
        self.assertEqual(events[1].event, "Ex-dividend")
        self.assertTrue(events[1].is_date_only)

    def test_euronext_listing_uses_official_search_isin_fallback(self) -> None:
        class TickerStub:
            def get_isin(self):
                return "-"

        class IsinResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return [
                    {
                        "isin": "LU1900066975",
                        "mic": "XPAR",
                        "label": "<span class='symbol'>KRW</span>",
                    }
                ]

        import market_terminal.providers as providers

        original_ticker = providers.yf.Ticker
        original_get = providers.requests.get
        providers.yf.Ticker = lambda _symbol: TickerStub()
        providers.requests.get = lambda *_args, **_kwargs: IsinResponse()
        try:
            provider = MarketDataProvider.__new__(MarketDataProvider)
            enriched = provider.instrument_details(Instrument("KRW.PA", "Amundi Korea"))
        finally:
            providers.yf.Ticker = original_ticker
            providers.requests.get = original_get

        self.assertEqual(enriched.isin, "LU1900066975")

    def test_quality_ranking_selects_fresher_secondary_frame(self) -> None:
        recent_end = pd.Timestamp.now(tz="UTC").normalize()
        recent_start = recent_end - pd.Timedelta(days=2)
        stale_start = recent_end - pd.Timedelta(days=120)
        stale_end = recent_end - pd.Timedelta(days=118)

        class TwelveStub:
            enabled = True

            def history(self, instrument, range_spec, include_extended_hours=False):
                frame = _quality_frame(recent_start, recent_end)
                frame.attrs["data_source"] = "Twelve Data"
                return frame

        class StooqStub:
            enabled = False

        provider = MarketDataProvider.__new__(MarketDataProvider)
        provider.twelve = TwelveStub()
        provider.stooq = StooqStub()
        provider._history_yahoo = lambda *args: _quality_frame(stale_start, stale_end)

        frame = provider.history(Instrument("AAPL", "Apple"), HISTORICAL_RANGES[0])

        self.assertEqual(frame.attrs["data_source"], "Twelve Data")
        self.assertGreater(frame.attrs["quality"].score, 90)
        self.assertEqual(len(frame.attrs["quality_candidates"]), 2)

    def test_crypto_history_prefers_binance_spot_when_available(self) -> None:
        recent_end = pd.Timestamp.now(tz="UTC").normalize()
        recent_start = recent_end - pd.Timedelta(days=2)

        class BinanceStub:
            enabled = True

            def history(self, instrument, range_spec, include_extended_hours=False):
                frame = _quality_frame(recent_start, recent_end)
                frame.attrs["data_source"] = "Binance Spot"
                return frame

        class TwelveStub:
            enabled = False

        class StooqStub:
            enabled = False

        provider = MarketDataProvider.__new__(MarketDataProvider)
        provider.binance = BinanceStub()
        provider.twelve = TwelveStub()
        provider.stooq = StooqStub()
        provider._history_yahoo = lambda *args: pd.DataFrame()

        frame = provider.history(
            Instrument("BTC-USD", "Bitcoin", quote_type="Cryptocurrency"), HISTORICAL_RANGES[0]
        )

        self.assertEqual(frame.attrs["data_source"], "Binance Spot")

    def test_crypto_quote_prefers_binance_spot_when_available(self) -> None:
        class BinanceStub:
            enabled = True

            def quote_snapshot(self, instrument):
                return QuoteSnapshot(last=101.0, change_percent=1.5, market_state="OPEN - 24/7 CRYPTO")

        provider = MarketDataProvider.__new__(MarketDataProvider)
        provider.binance = BinanceStub()

        quote = provider.quote_snapshot(Instrument("BTC-USD", "Bitcoin", quote_type="Crypto"))

        self.assertEqual(quote.last, 101.0)
        self.assertEqual(quote.market_state, "OPEN - 24/7 CRYPTO")


class FreeSourceAdapterTests(unittest.TestCase):
    def test_binance_parses_spot_klines_with_attribution(self) -> None:
        session = GetSession(
            RequestResponse(
                json_payload=[
                    [
                        1_775_000_000_000,
                        "100.0",
                        "102.0",
                        "99.0",
                        "101.0",
                        "12.5",
                        1_775_000_059_999,
                        "1262.5",
                        10,
                        "6",
                        "606",
                        "0",
                    ]
                ]
            )
        )

        frame = BinanceSpotClient(session=session).history(
            Instrument("BTC-USD", "Bitcoin", quote_type="Crypto"), INTRADAY_RANGES[0]
        )

        self.assertEqual(float(frame["Close"].iloc[0]), 101.0)
        self.assertEqual(frame.attrs["data_source"], "Binance Spot")
        self.assertEqual(frame.attrs["binance_symbol"], "BTCUSDT")
        self.assertEqual(session.request[1]["symbol"], "BTCUSDT")
        self.assertEqual(session.request[1]["interval"], "1m")

    def test_binance_parses_24h_ticker_quote(self) -> None:
        session = GetSession(
            RequestResponse(
                json_payload={
                    "lastPrice": "101.0",
                    "bidPrice": "100.9",
                    "askPrice": "101.1",
                    "priceChange": "1.0",
                    "priceChangePercent": "1.0",
                    "volume": "12.5",
                }
            )
        )

        quote = BinanceSpotClient(session=session).quote_snapshot(
            Instrument("BTCUSDT", "BTC/USDT", exchange="Binance", quote_type="Crypto")
        )

        self.assertEqual(quote.last, 101.0)
        self.assertEqual(quote.bid, 100.9)
        self.assertEqual(quote.ask, 101.1)
        self.assertEqual(quote.change_percent, 1.0)
        self.assertEqual(quote.market_state, "OPEN - 24/7 CRYPTO")

    def test_stooq_parses_daily_fallback_bars_with_attribution(self) -> None:
        session = GetSession(
            RequestResponse(
                text="Date,Open,High,Low,Close,Volume\n2026-05-21,100,102,99,101,12\n"
            )
        )

        frame = StooqClient(api_key="key", session=session).history(
            Instrument("AAPL", "Apple"), HISTORICAL_RANGES[0]
        )

        self.assertEqual(float(frame["Close"].iloc[0]), 101.0)
        self.assertEqual(frame.attrs["data_source"], "Stooq (EOD)")
        self.assertEqual(session.request[1]["s"], "aapl.us")
        self.assertEqual(session.request[1]["apikey"], "key")

    def test_stooq_does_not_claim_intraday_coverage(self) -> None:
        frame = StooqClient(api_key="key", session=GetSession(RequestResponse())).history(
            Instrument("AAPL", "Apple"), INTRADAY_RANGES[0]
        )

        self.assertTrue(frame.empty)

    def test_twelve_data_parses_configured_time_series(self) -> None:
        session = GetSession(
            RequestResponse(
                json_payload={
                    "status": "ok",
                    "values": [
                        {
                            "datetime": "2026-05-21",
                            "open": "100",
                            "high": "102",
                            "low": "99",
                            "close": "101",
                            "volume": "12",
                        }
                    ],
                }
            )
        )

        frame = TwelveDataClient(api_key="key", session=session).history(
            Instrument("AAPL", "Apple"), HISTORICAL_RANGES[0]
        )

        self.assertEqual(float(frame["Close"].iloc[0]), 101.0)
        self.assertEqual(frame.attrs["data_source"], "Twelve Data")
        self.assertEqual(session.request[1]["interval"], "1day")

    def test_custom_dates_are_passed_to_secondary_sources(self) -> None:
        twelve_session = GetSession(RequestResponse(json_payload={"status": "ok", "values": []}))
        custom = RangeSpec(
            "2026-05-01..2026-05-22", "custom", "1d", "2026-05-01", "2026-05-22"
        )

        TwelveDataClient(api_key="key", session=twelve_session).history(
            Instrument("AAPL", "Apple"), custom
        )

        self.assertEqual(twelve_session.request[1]["start_date"], "2026-05-01")
        self.assertEqual(twelve_session.request[1]["end_date"], "2026-05-22")

        stooq_session = GetSession(
            RequestResponse(text="Date,Open,High,Low,Close,Volume\n2026-05-21,100,102,99,101,12\n")
        )
        StooqClient(api_key="key", session=stooq_session).history(Instrument("AAPL", "Apple"), custom)

        self.assertEqual(stooq_session.request[1]["d1"], "20260501")
        self.assertEqual(stooq_session.request[1]["d2"], "20260522")

    def test_yahoo_custom_end_date_is_inclusive(self) -> None:
        class TickerStub:
            request = None

            def history(self, **request):
                TickerStub.request = request
                return pd.DataFrame(
                    {"Close": [101.0], "Volume": [12]},
                    index=pd.to_datetime(["2026-05-22"]),
                )

        import market_terminal.providers as providers

        original_ticker = providers.yf.Ticker
        providers.yf.Ticker = lambda _symbol: TickerStub()
        try:
            custom = RangeSpec(
                "2026-05-01..2026-05-22", "custom", "1d", "2026-05-01", "2026-05-22"
            )
            provider = MarketDataProvider.__new__(MarketDataProvider)
            provider._history_yahoo(Instrument("AAPL", "Apple"), custom)
        finally:
            providers.yf.Ticker = original_ticker

        self.assertEqual(TickerStub.request["start"], "2026-05-01")
        self.assertEqual(TickerStub.request["end"], "2026-05-23")
        self.assertNotIn("period", TickerStub.request)


class DataQualityTests(unittest.TestCase):
    def test_rejects_non_positive_close_values(self) -> None:
        frame = _quality_frame("2026-05-20", "2026-05-22")
        frame.loc[frame.index[-1], "Close"] = 0.0
        frame.attrs["data_source"] = "Invalid"

        quality = score_history_frame(
            frame, HISTORICAL_RANGES[0], now=pd.Timestamp("2026-05-24", tz="UTC")
        )

        self.assertFalse(quality.usable)
        self.assertIn("Invalid closes", quality.notes)


class MarketSessionTests(unittest.TestCase):
    def test_formats_open_session_and_local_regular_hours(self) -> None:
        metadata = {
            "exchangeTimezoneName": "America/New_York",
            "hasPrePostMarketData": True,
            "currentTradingPeriod": {
                "regular": {
                    "start": pd.Timestamp("2026-05-22 09:30", tz="America/New_York"),
                    "end": pd.Timestamp("2026-05-22 16:00", tz="America/New_York"),
                }
            },
        }

        session = build_market_session(
            metadata,
            now=pd.Timestamp("2026-05-22 15:00", tz="UTC"),
            local_timezone="Europe/Paris",
        )

        self.assertEqual(session.status, "OPEN - REGULAR SESSION")
        self.assertEqual(session.extended_session, "Pre/Post available")
        self.assertIn("09:30-16:00 America/New_York", session.regular_exchange_hours)
        self.assertIn("15:30-22:00", session.regular_local_hours)

    def test_reports_closed_outside_regular_session(self) -> None:
        metadata = {
            "exchangeTimezoneName": "America/New_York",
            "currentTradingPeriod": {
                "regular": {
                    "start": 1779456600,
                    "end": 1779480000,
                }
            },
        }

        session = build_market_session(
            metadata,
            now=pd.Timestamp("2026-05-24 12:00", tz="UTC"),
            local_timezone="Europe/Paris",
        )

        self.assertEqual(session.status, "CLOSED")
        self.assertEqual(session.overnight_session, "Overnight not indicated by Yahoo")


@contextmanager
def temporary_portfolio_index_dir():
    with tempfile.TemporaryDirectory() as directory:
        out_dir = Path(directory)
        (out_dir / "fort_pnl_index_constituents.csv").write_text(
            "\n".join(
                [
                    "index_name,as_of_date,isin,ticker,name,currency,quantity,price,market_value_eur,weight_pct,broker_pru,broker_unrealized_pnl_eur,broker_unrealized_pnl_pct,reconstructed_cost_basis_eur,reconstructed_unrealized_pnl_eur",
                    "FORT_PNL,2026-05-31,AAA,AAA,Alpha,EUR,10,10,1000,60,8,200,25,800,200",
                    "FORT_PNL,2026-05-31,BBB,BBB,Beta,USD,10,10,666.67,40,12,-50,-7,716.67,-50",
                ]
            ),
            encoding="utf-8",
        )
        (out_dir / "fort_pnl_index_summary.csv").write_text(
            "\n".join(
                [
                    "metric,value",
                    "index_name,FORT_PNL",
                    "as_of_date,2026-05-31",
                    "market_value_eur,1666.67",
                    "realized_2026_pnl_eur,-25",
                    "total_2026_pnl_eur,125",
                    "ytd_pnl_pct,12.5",
                ]
            ),
            encoding="utf-8",
        )
        (out_dir / "fort_pnl_index_levels.csv").write_text(
            "\n".join(
                [
                    "index_name,date,index_level,note",
                    "FORT_PNL,2026-01-01,100,Base level",
                    "FORT_PNL,2026-05-31,123.5,Latest level",
                ]
            ),
            encoding="utf-8",
        )
        (out_dir / "portfolio_new_trades_2026.csv").write_text(
            "\n".join(
                [
                    "trade_date,trade_time,action,isin,ticker,name,currency,quantity_signed,trade_price,commission_eur,net_trade_cash_eur,realized_pnl_eur,position_after_qty,broker_reference",
                    "2026-01-05,09:00:00,BUY,AAA,AAA,Alpha,EUR,10,8,1,-81,0,10,BUY1",
                    "2026-02-05,09:00:00,SELL,BBB,BBB,Beta,USD,-5,9,1,44,-25,5,SELL1",
                ]
            ),
            encoding="utf-8",
        )
        (out_dir / "fort_pnl_trade_table.csv").write_text(
            "\n".join(
                [
                    "trade_date,trade_time,equity_ticker,side,trade_quantity",
                    "10-02-2025,11:43:56,AAA,Achat - Exécution unique,10",
                    "05-01-2026,09:00:00,BBB,Achat - Exécution unique,5",
                ]
            ),
            encoding="utf-8",
        )
        yield out_dir


def _quality_frame(start: str, end: str) -> pd.DataFrame:
    index = pd.date_range(start, end, periods=20)
    frame = pd.DataFrame(
        {
            "Open": range(100, 120),
            "High": range(101, 121),
            "Low": range(99, 119),
            "Close": range(100, 120),
            "Volume": range(1000, 1020),
        },
        index=index,
    )
    frame.attrs["data_source"] = "Yahoo Finance"
    return frame


if __name__ == "__main__":
    unittest.main()
