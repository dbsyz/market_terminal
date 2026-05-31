from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import matplotlib.dates as mdates
import pandas as pd

from market_terminal.app import (
    anchored_text_bounds,
    calculate_return,
    calculate_beta_model,
    chart_date_bounds,
    comparison_date_bounds,
    comparison_price_bounds,
    constrain_window_geometry,
    custom_range_spec,
    displayed_close_series,
    filter_and_sort_instruments,
    fit_popup_to_window,
    format_market_cap,
    format_probability,
    instrument_identity_text,
    nearest_displayed_values,
    load_window_geometry,
    load_window_state,
    normalized_rectangle,
    ordered_text_blocks,
    prepare_comparison_frames,
    rectangle_is_drag,
    rectangles_intersect,
    save_window_geometry,
    source_file_snapshot,
    technical_indicator,
    technical_study_label,
)
from market_terminal.models import HISTORICAL_RANGES, INTRADAY_MATRIX, Instrument


class ReturnCalculationTests(unittest.TestCase):
    def test_calculates_point_and_percentage_return(self) -> None:
        self.assertEqual(calculate_return(100.0, 105.5), (5.5, 5.5))

    def test_zero_start_price_keeps_percentage_defined(self) -> None:
        self.assertEqual(calculate_return(0.0, 4.0), (4.0, 0.0))


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
