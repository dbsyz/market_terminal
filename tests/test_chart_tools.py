from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import matplotlib.dates as mdates
import pandas as pd

from market_terminal.app import (
    DOWN,
    PRICE_RENDER_DESCRIPTIONS,
    PRICE_RENDER_LABELS,
    PRICE_RENDER_MODES,
    UP,
    anchored_text_bounds,
    beta_table_column_width,
    calculate_return,
    calculate_beta_model,
    chart_date_bounds,
    comparison_date_bounds,
    comparison_latest_value_label,
    comparison_price_bounds,
    comparison_series_color,
    comparison_series_colors,
    comparison_y_axis_label,
    constrain_window_geometry,
    custom_range_spec,
    displayed_close_series,
    market_event_display_sort_key,
    event_price_move_since_text,
    market_event_is_past,
    filter_and_sort_instruments,
    fit_popup_to_window,
    format_market_cap,
    format_probability,
    format_quote_value,
    instrument_fundamentals_text,
    instrument_from_watchlist_row,
    instrument_identity_text,
    nearest_displayed_values,
    load_window_geometry,
    load_window_state,
    load_layout_state,
    normalized_app_settings,
    normalized_watchlist_column_widths,
    normalized_rectangle,
    ordered_text_blocks,
    price_move_color,
    prepare_comparison_frames,
    quote_allows_realtime_refresh,
    quote_change_tag,
    rectangle_is_drag,
    rectangles_intersect,
    save_window_geometry,
    save_watchlist_state,
    save_layout_state,
    range_spec_state,
    source_file_snapshot,
    technical_indicator,
    technical_study_state,
    technical_study_label,
    watchlist_asset_label,
    watchlist_group_label,
    watchlist_group_name,
    watchlist_group_row,
    watchlist_display_values,
    watchlist_heading_height,
    watchlist_item_tags,
    watchlist_row_from_instrument,
    watchlist_row_stripe,
)
from market_terminal.models import (
    HISTORICAL_RANGES,
    INTRADAY_MATRIX,
    INTRADAY_RANGES,
    Instrument,
    MarketEvent,
    QuoteSnapshot,
)


class ReturnCalculationTests(unittest.TestCase):
    def test_calculates_point_and_percentage_return(self) -> None:
        self.assertEqual(calculate_return(100.0, 105.5), (5.5, 5.5))

    def test_zero_start_price_keeps_percentage_defined(self) -> None:
        self.assertEqual(calculate_return(0.0, 4.0), (4.0, 0.0))


class MoveColorTests(unittest.TestCase):
    def test_price_move_color_uses_latest_close_change(self) -> None:
        up = pd.DataFrame({"Close": [100.0, 101.0]})
        down = pd.DataFrame({"Close": [100.0, 99.0]})

        self.assertEqual(price_move_color(up, "#fallback"), UP)
        self.assertEqual(price_move_color(down, "#fallback"), DOWN)

    def test_price_move_color_keeps_fallback_without_a_directional_move(self) -> None:
        flat = pd.DataFrame({"Close": [100.0, 100.0]})
        sparse = pd.DataFrame({"Close": [100.0]})

        self.assertEqual(price_move_color(flat, "#fallback"), "#fallback")
        self.assertEqual(price_move_color(sparse, "#fallback"), "#fallback")

    def test_quote_change_tag_uses_daily_change_when_no_tick_direction_exists(self) -> None:
        self.assertEqual(quote_change_tag(1.0, None), "tick_up")
        self.assertEqual(quote_change_tag(None, -0.5), "tick_down")
        self.assertEqual(quote_change_tag(0.0, 0.0), "tick_flat")

    def test_watchlist_item_tags_keep_row_stripe_and_tick_direction_separate(self) -> None:
        self.assertEqual(watchlist_row_stripe(0), "watchlist_even")
        self.assertEqual(watchlist_row_stripe(1), "watchlist_odd")
        self.assertEqual(watchlist_item_tags(1, "tick_down"), ("watchlist_odd", "tick_down"))

    def test_watchlist_asset_label_uses_ticker_only(self) -> None:
        self.assertEqual(
            watchlist_asset_label(Instrument("AAPL", "Apple Inc.", exchange="NASDAQ")),
            "AAPL",
        )
        self.assertEqual(watchlist_asset_label(Instrument("BTCUSDT", "BTC/USDT")), "BTCUSDT")

    def test_quote_values_are_rounded_to_two_decimals(self) -> None:
        self.assertEqual(format_quote_value(1.2345), "1.23")
        self.assertEqual(format_quote_value(1234.567), "1,234.57")

    def test_closed_market_quotes_back_off_from_realtime_refresh(self) -> None:
        self.assertFalse(
            quote_allows_realtime_refresh(QuoteSnapshot(last=100.0, market_state="CLOSED"))
        )
        self.assertTrue(
            quote_allows_realtime_refresh(QuoteSnapshot(last=100.0, market_state="REGULAR"))
        )
        self.assertTrue(quote_allows_realtime_refresh(QuoteSnapshot(last=100.0)))

    def test_watchlist_heading_height_uses_first_row_y_offset(self) -> None:
        class TreeStub:
            def get_children(self):
                return ("row1",)

            def bbox(self, _item):
                return (0, 28, 100, 27)

        self.assertEqual(watchlist_heading_height(TreeStub()), 28)

    def test_watchlist_column_widths_keep_valid_saved_values(self) -> None:
        widths = normalized_watchlist_column_widths(
            {"asset": "170", "last": 92, "bid": 10, "unknown": 400}
        )

        self.assertEqual(widths["asset"], 170)
        self.assertEqual(widths["last"], 92)
        self.assertEqual(widths["bid"], 70)
        self.assertNotIn("unknown", widths)

    def test_watchlist_column_widths_fall_back_for_invalid_layout(self) -> None:
        widths = normalized_watchlist_column_widths(None)

        self.assertEqual(widths["asset"], 125)
        self.assertEqual(widths["change"], 80)

    def test_beta_table_column_width_is_uniform_and_compact_for_short_values(self) -> None:
        width = beta_table_column_width(
            [
                ("Y: AAPL", "", "", "", ""),
                ("MSFT", "+1.235", "0.042", "+29.12", "<.001"),
            ]
        )

        self.assertGreaterEqual(width, 46)
        self.assertLessEqual(width, 74)
        self.assertEqual(width, 67)

    def test_beta_table_column_width_caps_long_symbols(self) -> None:
        width = beta_table_column_width([("VERY_LONG_SYMBOL", "+1.235", "0.042", "+29.12", "<.001")])

        self.assertEqual(width, 74)


class ChartDateBoundsTests(unittest.TestCase):
    def test_date_bounds_start_at_first_returned_observation(self) -> None:
        index = pd.to_datetime(["1980-12-12", "2026-05-24"])
        frame = pd.DataFrame({"Close": [1.0, 2.0]}, index=index)

        start, end = chart_date_bounds(frame)

        self.assertEqual(start, pd.Timestamp("1980-12-12"))
        self.assertEqual(end, pd.Timestamp("2026-05-24"))

    def test_single_observation_has_visible_nonzero_range(self) -> None:
        frame = pd.DataFrame({"Close": [1.0]}, index=pd.to_datetime(["2026-05-24"]))

        start, end = chart_date_bounds(frame)

        self.assertGreater(end, start)


class CustomRangeTests(unittest.TestCase):
    def test_builds_intraday_custom_range_with_selected_bar_size(self) -> None:
        spec = custom_range_spec("Intraday", "2026-05-20", "2026-05-22", "15m")

        self.assertEqual((spec.period, spec.interval), ("custom", "15m"))
        self.assertEqual((spec.start, spec.end), ("2026-05-20", "2026-05-22"))

    def test_rejects_reversed_custom_range(self) -> None:
        with self.assertRaises(ValueError):
            custom_range_spec("Historical", "2026-05-22", "2026-05-20", "1d")


class SuggestionPopupLayoutTests(unittest.TestCase):
    def test_compare_suggestions_open_leftward_inside_right_edge(self) -> None:
        x, y, width = fit_popup_to_window(
            anchor_x=965,
            anchor_y=600,
            anchor_width=275,
            anchor_height=28,
            preferred_width=530,
            popup_height=302,
            window_left=50,
            window_right=1250,
            window_top=40,
            window_bottom=780,
            align_right=True,
        )

        self.assertEqual((x, width), (710, 530))
        self.assertEqual(y, 295)
        self.assertLessEqual(x + width, 1250)

    def test_wide_search_results_are_clamped_to_window_width(self) -> None:
        x, _y, width = fit_popup_to_window(
            anchor_x=20,
            anchor_y=70,
            anchor_width=900,
            anchor_height=30,
            preferred_width=900,
            popup_height=302,
            window_left=30,
            window_right=630,
            window_top=20,
            window_bottom=700,
        )

        self.assertEqual((x, width), (30, 600))


class SearchRankingTests(unittest.TestCase):
    def test_market_cap_sort_places_largest_known_cap_first(self) -> None:
        instruments = [
            Instrument("SMALL", "Small", market_cap=10_000_000),
            Instrument("UNKNOWN", "Unknown"),
            Instrument("LARGE", "Large", market_cap=2_000_000_000),
        ]

        sorted_instruments = filter_and_sort_instruments(
            instruments, "Market Cap", "All Markets"
        )

        self.assertEqual([instrument.symbol for instrument in sorted_instruments], ["LARGE", "SMALL", "UNKNOWN"])
        self.assertEqual(format_market_cap(sorted_instruments[0].market_cap), "2.0B")

    def test_exchange_filter_limits_results_before_sorting(self) -> None:
        instruments = [
            Instrument("A", "A", exchange="NYSE"),
            Instrument("B", "B", exchange="NASDAQ"),
        ]

        filtered = filter_and_sort_instruments(instruments, "Relevance", "NASDAQ")

        self.assertEqual([instrument.symbol for instrument in filtered], ["B"])


class InstrumentIdentityTests(unittest.TestCase):
    def test_formats_isin_and_asset_type_for_quote_header(self) -> None:
        instrument = Instrument("KRW.PA", "Amundi Korea", quote_type="ETF", isin="LU1900066975")

        self.assertEqual(
            instrument_identity_text(instrument),
            "ISIN: LU1900066975  |  Asset Type: ETF",
        )

    def test_formats_missing_isin_explicitly(self) -> None:
        self.assertEqual(
            instrument_identity_text(Instrument("AAPL", "Apple", quote_type="Equity")),
            "ISIN: N/A  |  Asset Type: Equity",
        )

    def test_formats_equity_market_cap_for_header(self) -> None:
        self.assertEqual(
            instrument_fundamentals_text(
                Instrument("AAPL", "Apple", quote_type="Equity", market_cap=3_100_000_000_000)
            ),
            "Market Cap: 3.1T",
        )

    def test_formats_etf_market_cap_and_aum_for_header(self) -> None:
        self.assertEqual(
            instrument_fundamentals_text(
                Instrument(
                    "SPY",
                    "SPDR S&P 500 ETF",
                    quote_type="ETF",
                    market_cap=620_000_000_000,
                    aum=650_000_000_000,
                )
            ),
            "Market Cap: 620.0B  |  AUM: 650.0B",
        )

    def test_formats_portfolio_index_value_for_header(self) -> None:
        self.assertEqual(
            instrument_fundamentals_text(
                Instrument(
                    "FORT_PNL",
                    "FORT_PNL custom portfolio index",
                    quote_type="Portfolio Index",
                    aum=143_221.33,
                )
            ),
            "Portfolio Value: 143,221 EUR",
        )

    def test_round_trips_watchlist_instrument_state(self) -> None:
        instrument = Instrument(
            "SPY",
            "SPDR S&P 500 ETF",
            exchange="NYSEArca",
            quote_type="ETF",
            currency="USD",
            market_cap=1_000_000,
            aum=2_000_000,
            isin="US78462F1030",
        )

        restored = instrument_from_watchlist_row(watchlist_row_from_instrument(instrument))

        self.assertEqual(restored, instrument)

    def test_round_trips_watchlist_group_state(self) -> None:
        row = watchlist_group_row("  Core Tech  ")

        self.assertEqual(row, {"type": "group", "name": "Core Tech"})
        self.assertEqual(watchlist_group_name(row), "Core Tech")
        self.assertEqual(watchlist_group_label("Core Tech"), "[ CORE TECH ]")
        self.assertIsNone(instrument_from_watchlist_row(row))

    def test_watchlist_display_values_restore_cached_quote_cells(self) -> None:
        instrument = Instrument("AAPL", "Apple Inc.")
        row = watchlist_row_from_instrument(instrument)
        row["display_values"] = ["AAPL", "195.00", "194.95", "195.05", "+1.20%", "55.2M", "90ms"]

        self.assertEqual(
            watchlist_display_values(row, instrument),
            ("AAPL", "195.00", "194.95", "195.05", "+1.20%", "55.2M", "90ms"),
        )

    def test_watchlist_display_values_fall_back_to_loading(self) -> None:
        instrument = Instrument("AAPL", "Apple Inc.")

        self.assertEqual(
            watchlist_display_values({}, instrument),
            ("AAPL", "Loading", "", "", "", "", ""),
        )

    def test_saves_and_loads_watchlist_state_file(self) -> None:
        from market_terminal.app import load_watchlist_state

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watchlist.json"

            save_watchlist_state(path, [{"symbol": "AAPL"}, {}])

            self.assertEqual(load_watchlist_state(path), [{"symbol": "AAPL"}, {}])

    def test_loads_watchlist_backup_when_primary_is_empty(self) -> None:
        from market_terminal.app import load_watchlist_state

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watchlist.json"
            path.write_text("[]", encoding="utf-8")
            path.with_suffix(".json.bak").write_text('[{"symbol": "AAPL"}]', encoding="utf-8")

            self.assertEqual(load_watchlist_state(path), [{"symbol": "AAPL"}])

    def test_saves_and_loads_layout_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "layout.json"
            layout = {
                "watchlist": {"x": 0, "y": 10, "width": 420, "height": 500},
                "chart": {"x": 430, "y": 10, "width": 900, "height": 650},
            }

            save_layout_state(path, layout)

            self.assertEqual(load_layout_state(path), layout)

    def test_normalizes_saved_app_settings_for_chart_view_restore(self) -> None:
        settings = normalized_app_settings(
            {
                "chart_mode": "Historical",
                "selected_range": range_spec_state(HISTORICAL_RANGES[2]),
                "price_render_mode": "hollow_candles",
                "display_mode": "Rebased 100",
                "compare_panel_visible": True,
                "rebase_comparison": True,
                "betas_comparison": True,
                "technical_study": technical_study_state(("RSI", 14)),
                "extended_hours": True,
                "intraday_custom_bar": "30m",
                "chart_group": "C",
                "watchlist_group": "D",
                "event_group": "E",
                "macro_category": "labor",
                "news_topic": "Macro",
                "search_sort": "Market Cap",
                "chart_instruments": [
                    watchlist_row_from_instrument(Instrument("AAPL", "Apple")),
                    watchlist_row_from_instrument(Instrument("MSFT", "Microsoft")),
                ],
            }
        )

        self.assertEqual(settings["chart_mode"], "Historical")
        self.assertEqual(settings["selected_range"], HISTORICAL_RANGES[2])
        self.assertEqual(settings["price_render_mode"], "hollow_candles")
        self.assertEqual(settings["display_mode"], "Rebased 100")
        self.assertTrue(settings["compare_panel_visible"])
        self.assertTrue(settings["rebase_comparison"])
        self.assertTrue(settings["betas_comparison"])
        self.assertEqual(settings["technical_study"], ("RSI", 14))
        self.assertTrue(settings["extended_hours"])
        self.assertEqual(settings["chart_group"], "C")
        self.assertEqual(settings["macro_category"], "labor")
        self.assertEqual(settings["news_topic"], "Macro")
        self.assertEqual(settings["search_sort"], "Market Cap")
        self.assertEqual([instrument.symbol for instrument in settings["chart_instruments"]], ["AAPL", "MSFT"])

    def test_saved_app_settings_fall_back_for_invalid_values(self) -> None:
        settings = normalized_app_settings(
            {
                "chart_mode": "Bad",
                "selected_range": {"period": "custom", "interval": "2m", "start": "bad", "end": "2026-01-02"},
                "price_render_mode": "bad",
                "display_mode": "Rebased 100",
                "compare_panel_visible": True,
                "rebase_comparison": True,
                "betas_comparison": True,
                "technical_study": {"name": "RSI", "period": 999},
                "chart_group": "Z",
                "macro_category": "bad",
                "news_topic": "bad",
                "search_sort": "bad",
                "chart_instruments": [watchlist_row_from_instrument(Instrument("AAPL", "Apple"))],
            }
        )

        self.assertEqual(settings["chart_mode"], "Intraday")
        self.assertEqual(settings["selected_range"], INTRADAY_RANGES[0])
        self.assertEqual(settings["price_render_mode"], "bars")
        self.assertEqual(settings["display_mode"], "Prices")
        self.assertFalse(settings["compare_panel_visible"])
        self.assertFalse(settings["rebase_comparison"])
        self.assertFalse(settings["betas_comparison"])
        self.assertIsNone(settings["technical_study"])
        self.assertEqual(settings["chart_group"], "A")
        self.assertEqual(settings["macro_category"], "rates")
        self.assertEqual(settings["news_topic"], "Markets")
        self.assertEqual(settings["search_sort"], "Relevance")


class TextSelectionTests(unittest.TestCase):
    def test_normalizes_drag_direction_and_detects_overlapping_text(self) -> None:
        selected = normalized_rectangle((250, 180), (100, 75))

        self.assertEqual(selected, (100, 75, 250, 180))
        self.assertTrue(rectangles_intersect(selected, (120, 90, 180, 110)))
        self.assertFalse(rectangles_intersect(selected, (260, 90, 300, 110)))

    def test_copied_text_is_ordered_by_screen_location(self) -> None:
        text = ordered_text_blocks(
            [
                (80, 10, "Last: 148.00"),
                (30, 10, "ISIN: LU1900066975"),
                (30, 190, "Asset Type: ETF"),
            ]
        )

        self.assertEqual(
            text,
            "ISIN: LU1900066975\nAsset Type: ETF\nLast: 148.00",
        )

    def test_click_is_not_treated_as_a_rectangle_selection(self) -> None:
        self.assertFalse(rectangle_is_drag((100, 100, 102, 101)))
        self.assertTrue(rectangle_is_drag((100, 100, 112, 101)))

    def test_full_width_label_only_selects_its_rendered_text_region(self) -> None:
        text_bounds = anchored_text_bounds((10, 20, 410, 45), (95, 15), "w")

        self.assertTrue(rectangles_intersect(text_bounds, (45, 25, 45, 25)))
        self.assertFalse(rectangles_intersect(text_bounds, (300, 25, 300, 25)))


class EventDisplayTests(unittest.TestCase):
    def test_event_price_move_since_uses_next_available_close(self) -> None:
        frame = pd.DataFrame(
            {"Close": [100.0, 105.0, 110.0]},
            index=pd.to_datetime(["2026-05-08", "2026-05-11", "2026-05-12"]),
        )
        event = MarketEvent(
            timestamp=pd.Timestamp("2026-05-10", tz="UTC").to_pydatetime(),
            event="Earnings",
            event_type="Earnings",
            is_date_only=True,
        )

        self.assertEqual(event_price_move_since_text(event, frame), "+4.76%")

    def test_event_price_move_since_omits_future_events(self) -> None:
        frame = pd.DataFrame(
            {"Close": [100.0, 105.0]},
            index=pd.to_datetime(["2026-05-08", "2026-05-11"]),
        )
        event = MarketEvent(
            timestamp=pd.Timestamp("2026-05-12", tz="UTC").to_pydatetime(),
            event="Earnings",
            event_type="Earnings",
        )

        self.assertEqual(event_price_move_since_text(event, frame), "")

    def test_market_events_sort_oldest_to_newest_for_display(self) -> None:
        events = [
            MarketEvent(pd.Timestamp("2026-06-10", tz="UTC").to_pydatetime(), "Future", "Event"),
            MarketEvent(pd.Timestamp("2026-05-01", tz="UTC").to_pydatetime(), "Past", "Event"),
        ]

        self.assertEqual(
            [event.event for event in sorted(events, key=market_event_display_sort_key)],
            ["Past", "Future"],
        )

    def test_market_event_is_past_uses_current_utc_time(self) -> None:
        event = MarketEvent(
            pd.Timestamp("2026-05-01", tz="UTC").to_pydatetime(),
            "Past",
            "Event",
        )

        self.assertTrue(
            market_event_is_past(event, pd.Timestamp("2026-06-01", tz="UTC").to_pydatetime())
        )


class SourceUpdateTests(unittest.TestCase):
    def test_runtime_file_snapshot_changes_after_source_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "app.py"
            source.write_text("one", encoding="utf-8")
            original = source_file_snapshot((source,))

            source.write_text("updated source", encoding="utf-8")

            self.assertNotEqual(original, source_file_snapshot((source,)))


class WindowGeometryTests(unittest.TestCase):
    def test_persists_last_normal_window_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "window_state.json"

            save_window_geometry(state_path, "1380x900+24+36")

            self.assertEqual(load_window_geometry(state_path), "1380x900+24+36")

    def test_restored_window_preserves_saved_position_and_shape(self) -> None:
        geometry = constrain_window_geometry("1300x820+3000+2000", 1920, 1080)

        self.assertEqual(geometry, "1300x820+3000+2000")

    def test_persists_maximized_window_state_with_normal_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "window_state.json"

            save_window_geometry(state_path, "1380x900+24+36", "zoomed")

            self.assertEqual(
                load_window_state(state_path),
                {"geometry": "1380x900+24+36", "state": "zoomed"},
            )


class ComparisonSeriesTests(unittest.TestCase):
    def test_max_starts_all_series_at_latest_first_observation(self) -> None:
        long_history = pd.DataFrame(
            {"Close": [10.0, 11.0, 12.0]},
            index=pd.to_datetime(["2000-01-01", "2020-01-01", "2026-01-01"]),
        )
        recent_history = pd.DataFrame(
            {"Close": [20.0, 21.0]},
            index=pd.to_datetime(["2020-01-01", "2026-01-01"]),
        )

        frames = prepare_comparison_frames(
            {"OLD": long_history, "NEW": recent_history}, HISTORICAL_RANGES[-1]
        )

        self.assertEqual(frames["OLD"].index[0], pd.Timestamp("2020-01-01"))
        self.assertEqual(frames["NEW"].index[0], pd.Timestamp("2020-01-01"))

    def test_rebased_series_begin_at_100(self) -> None:
        frame = pd.DataFrame(
            {"Close": [50.0, 60.0]}, index=pd.to_datetime(["2025-01-01", "2026-01-01"])
        )

        displayed = displayed_close_series({"A": frame}, "Rebased 100")

        self.assertEqual(list(displayed["A"]), [100.0, 120.0])

    def test_comparison_y_axis_label_lists_all_symbols(self) -> None:
        self.assertEqual(
            comparison_y_axis_label(["AAPL", "MSFT", "SPY"], "Prices"),
            "Price: AAPL, MSFT, SPY",
        )
        self.assertEqual(
            comparison_y_axis_label(["AAPL", "MSFT"], "Rebased 100"),
            "Indexed (100): AAPL, MSFT",
        )

    def test_single_series_y_axis_label_keeps_short_unit_label(self) -> None:
        self.assertEqual(comparison_y_axis_label(["AAPL"], "Prices"), "Price")
        self.assertEqual(comparison_y_axis_label(["AAPL"], "Rebased 100"), "Indexed (100)")

    def test_comparison_value_labels_include_symbol_and_latest_value(self) -> None:
        self.assertEqual(comparison_latest_value_label("MSFT", 432.123), "MSFT 432.12")

    def test_comparison_colors_are_stable_across_directional_moves(self) -> None:
        rising = pd.DataFrame({"Close": [100.0, 101.0]})
        falling = pd.DataFrame({"Close": [200.0, 199.0]})

        colors = comparison_series_colors(
            [Instrument("AAPL", "Apple"), Instrument("MSFT", "Microsoft")],
            {"AAPL": rising, "MSFT": falling},
        )

        self.assertEqual(colors["AAPL"], "#f6a400")
        self.assertEqual(colors["MSFT"], "#42a5f5")
        self.assertNotEqual(colors["AAPL"], colors["MSFT"])

    def test_single_series_color_still_reflects_price_move(self) -> None:
        rising = pd.DataFrame({"Close": [100.0, 101.0]})

        self.assertEqual(comparison_series_color(rising, 0, 1), UP)

    def test_comparison_bounds_include_all_displayed_series(self) -> None:
        frames = {
            "A": pd.DataFrame(
                {"Close": [1.0, 2.0]}, index=pd.to_datetime(["2020-01-01", "2025-01-01"])
            ),
            "B": pd.DataFrame(
                {"Close": [3.0, 4.0]}, index=pd.to_datetime(["2020-01-01", "2026-01-01"])
            ),
        }

        start, end = comparison_date_bounds(frames)

        self.assertEqual(start, pd.Timestamp("2020-01-01"))
        self.assertEqual(end, pd.Timestamp("2026-01-01"))

    def test_comparison_frames_normalize_mixed_timezones(self) -> None:
        local = pd.DataFrame(
            {"Close": [100.0, 101.0]},
            index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
        )
        yahoo = pd.DataFrame(
            {"Close": [200.0, 201.0]},
            index=pd.to_datetime(["2026-01-02", "2026-01-05"]).tz_localize("America/New_York"),
        )

        frames = prepare_comparison_frames(
            {"FORT_PNL": local, "SPY": yahoo},
            HISTORICAL_RANGES[2],
        )
        start, end = comparison_date_bounds(frames)

        self.assertEqual(start, pd.Timestamp("2026-01-02"))
        self.assertEqual(end, pd.Timestamp("2026-01-05"))


    def test_price_bounds_focus_on_intraday_trading_range(self) -> None:
        closes = pd.Series([308.0, 309.0, 308.5])

        lower, upper = comparison_price_bounds({"AAPL": closes})

        self.assertGreater(lower, 300.0)
        self.assertLess(upper, 310.0)

    def test_flat_price_range_still_has_visible_padding(self) -> None:
        lower, upper = comparison_price_bounds({"CASH": pd.Series([100.0, 100.0])})

        self.assertLess(lower, 100.0)
        self.assertGreater(upper, 100.0)

    def test_hover_values_snap_each_comparison_series_to_guide_date(self) -> None:
        primary = pd.Series(
            [100.0, 102.0], index=pd.to_datetime(["2026-05-20", "2026-05-22"])
        )
        comparison = pd.Series(
            [50.0, 60.0], index=pd.to_datetime(["2026-05-20", "2026-05-23"])
        )

        timestamp, values = nearest_displayed_values(
            {"AAPL": primary, "MSFT": comparison},
            mdates.date2num(pd.Timestamp("2026-05-22").to_pydatetime()),
        )

        self.assertEqual(timestamp, pd.Timestamp("2026-05-22"))
        self.assertEqual(values, [("AAPL", 102.0), ("MSFT", 60.0)])

    def test_beta_model_runs_joint_ols_on_aligned_bar_returns(self) -> None:
        x1 = [0.010, -0.015, 0.020, 0.004, -0.009, 0.013, -0.006, 0.018]
        x2 = [-0.005, 0.012, 0.003, -0.016, 0.010, 0.007, -0.011, 0.002]
        y = [0.001 + 1.5 * first - 0.75 * second for first, second in zip(x1, x2)]
        frames = {
            "Y": _returns_frame(y),
            "X1": _returns_frame(x1),
            "X2": _returns_frame(x2),
        }

        stats = calculate_beta_model(frames, ["Y", "X1", "X2"])

        self.assertIsNotNone(stats)
        assert stats is not None
        self.assertEqual(stats.observations, 8)
        self.assertAlmostEqual(stats.alpha.estimate, 0.001)
        self.assertAlmostEqual(stats.betas["X1"].estimate, 1.5)
        self.assertAlmostEqual(stats.betas["X2"].estimate, -0.75)
        self.assertAlmostEqual(stats.r_squared, 1.0)
        self.assertEqual(format_probability(stats.betas["X1"].p_value), "<.001")

    def test_beta_model_requires_enough_returns_for_full_model(self) -> None:
        frames = {"Y": _returns_frame([0.01]), "X": _returns_frame([0.02])}

        self.assertIsNone(calculate_beta_model(frames, ["Y", "X"]))


class TechnicalStudyTests(unittest.TestCase):
    def test_momentum_is_percentage_change_over_selected_bars(self) -> None:
        closes = pd.Series([100.0, 110.0, 121.0])

        momentum = technical_indicator(closes, ("MOM", 1))

        self.assertAlmostEqual(float(momentum.iloc[-1]), 10.0)

    def test_rsi_for_rising_series_reaches_100(self) -> None:
        closes = pd.Series([100.0, 101.0, 102.0, 103.0])

        rsi = technical_indicator(closes, ("RSI", 2))

        self.assertEqual(float(rsi.iloc[-1]), 100.0)

    def test_sigma_is_rolling_percentage_return_volatility(self) -> None:
        closes = pd.Series([100.0, 110.0, 121.0, 133.1])

        sigma = technical_indicator(closes, ("SIGMA", 2))

        self.assertAlmostEqual(float(sigma.iloc[-1]), 0.0)
        self.assertEqual(technical_study_label(("SIGMA", 20)), "SIGMA 20")
        self.assertEqual(technical_study_label(None), "Volume")


class IntradayMatrixTests(unittest.TestCase):
    def test_one_day_offers_one_minute_bars(self) -> None:
        one_day = dict(INTRADAY_MATRIX)["1D"]

        self.assertEqual(one_day[0].interval, "1m")

    def test_one_month_does_not_offer_unsupported_one_minute_bars(self) -> None:
        one_month = dict(INTRADAY_MATRIX)["1M"]

        self.assertNotIn("1m", [spec.interval for spec in one_month])


class PriceRenderModeTests(unittest.TestCase):
    def test_price_render_modes_cover_supported_chart_types(self) -> None:
        self.assertEqual(
            PRICE_RENDER_MODES,
            (
                "bars",
                "candles",
                "hollow_candles",
                "hlc_bars",
                "line",
                "line_markers",
                "step_line",
            ),
        )
        self.assertEqual(set(PRICE_RENDER_LABELS), set(PRICE_RENDER_MODES))
        self.assertEqual(set(PRICE_RENDER_DESCRIPTIONS), set(PRICE_RENDER_MODES))


def _returns_frame(returns: list[float]) -> pd.DataFrame:
    closes = [100.0]
    for value in returns:
        closes.append(closes[-1] * (1 + value))
    return pd.DataFrame(
        {"Close": closes},
        index=pd.date_range("2026-05-01", periods=len(closes), freq="D"),
    )


if __name__ == "__main__":
    unittest.main()
