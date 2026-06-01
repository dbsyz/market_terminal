from __future__ import annotations

import unittest

import pandas as pd

from market_terminal.fred_macro import FredSeriesSpec
from market_terminal.macro_dashboard import (
    MacroDashboardService,
    build_macro_series_snapshot,
    format_macro_dashboard_snapshot,
)


class StubFred:
    def observations_frame(
        self,
        series_id: str,
        observation_start: str = "",
        observation_end: str = "",
        units: str = "lin",
    ) -> pd.DataFrame:
        frame = pd.DataFrame(
            {"Value": [4.0, 4.25]},
            index=pd.to_datetime(["2026-04-01", "2026-05-01"]),
        )
        frame.attrs["series_id"] = series_id
        return frame


class MacroDashboardTests(unittest.TestCase):
    def test_builds_latest_and_previous_series_snapshot(self) -> None:
        spec = FredSeriesSpec("DGS10", "10-Year Treasury", "rates")
        frame = pd.DataFrame(
            {"Value": [4.0, 4.25]},
            index=pd.to_datetime(["2026-04-01", "2026-05-01"]),
        )

        snapshot = build_macro_series_snapshot(spec, frame)

        self.assertEqual(snapshot.latest_value, 4.25)
        self.assertEqual(snapshot.previous_value, 4.0)
        self.assertEqual(snapshot.change, 0.25)

    def test_service_builds_category_snapshot_from_fred_client(self) -> None:
        service = MacroDashboardService(fred=StubFred())

        snapshot = service.snapshot("rates", observation_start="2026-01-01")

        self.assertEqual(snapshot.category, "rates")
        self.assertTrue(snapshot.series)
        self.assertEqual(snapshot.series[0].latest_value, 4.25)

    def test_formats_macro_snapshot(self) -> None:
        service = MacroDashboardService(fred=StubFred())

        text = format_macro_dashboard_snapshot(service.snapshot("rates"))

        self.assertIn("Macro dashboard (rates) via FRED", text)
        self.assertIn("DGS10", text)


if __name__ == "__main__":
    unittest.main()
