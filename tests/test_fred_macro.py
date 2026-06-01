from __future__ import annotations

import unittest

from market_terminal.fred_macro import (
    FredClient,
    curated_fred_series,
    parse_fred_observations,
)


class StubResponse:
    def __init__(self, payload) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self.payload


class StubSession:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.request = None

    def get(self, url, params, timeout):
        self.request = (url, params, timeout)
        return StubResponse(self.payload)


class FredMacroTests(unittest.TestCase):
    def test_curated_series_can_filter_by_category(self) -> None:
        rates = curated_fred_series("rates")

        self.assertIn("DGS10", [series.series_id for series in rates])
        self.assertNotIn("UNRATE", [series.series_id for series in rates])

    def test_parses_observations_and_missing_values(self) -> None:
        observations = parse_fred_observations(
            {
                "observations": [
                    {
                        "date": "2026-05-01",
                        "value": "4.25",
                        "realtime_start": "2026-06-01",
                        "realtime_end": "2026-06-01",
                    },
                    {"date": "2026-05-02", "value": "."},
                ]
            }
        )

        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[0].value, 4.25)
        self.assertIsNone(observations[1].value)

    def test_client_requires_api_key_for_requests(self) -> None:
        client = FredClient(api_key="", session=StubSession({}))

        with self.assertRaises(RuntimeError):
            client.observations("DGS10")

    def test_client_requests_json_observations(self) -> None:
        session = StubSession({"observations": [{"date": "2026-05-01", "value": "4.25"}]})
        client = FredClient(api_key="key", session=session)

        frame = client.observations_frame("DGS10", observation_start="2026-01-01")

        self.assertEqual(frame.attrs["data_source"], "FRED")
        self.assertEqual(frame.attrs["series_id"], "DGS10")
        self.assertEqual(frame["Value"].iloc[0], 4.25)
        self.assertEqual(session.request[1]["api_key"], "key")
        self.assertEqual(session.request[1]["file_type"], "json")
        self.assertEqual(session.request[1]["series_id"], "DGS10")


if __name__ == "__main__":
    unittest.main()
