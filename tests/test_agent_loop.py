from __future__ import annotations

import unittest

import pandas as pd

from market_terminal.agent_loop import (
    AgentLoopConfig,
    AgentLoopTask,
    AgenticMarketLoop,
    build_market_observation,
)
from market_terminal.models import HISTORICAL_RANGES, Instrument


class StubProvider:
    def __init__(self) -> None:
        self.search_requests: list[str] = []
        self.history_requests: list[str] = []

    def search(self, query: str) -> list[Instrument]:
        self.search_requests.append(query)
        if query == "MISSING":
            return []
        return [Instrument(query.upper(), f"{query.upper()} Inc", exchange="NASDAQ")]

    def history(
        self,
        instrument: Instrument,
        range_spec,
        include_extended_hours: bool = False,
    ) -> pd.DataFrame:
        self.history_requests.append(instrument.symbol)
        if instrument.symbol == "FAIL":
            raise RuntimeError("provider down")
        frame = pd.DataFrame(
            {"Close": [100.0, 105.0, 103.0]},
            index=pd.to_datetime(["2026-05-27", "2026-05-28", "2026-05-29"]),
        )
        frame.attrs["data_source"] = "Stub"
        return frame


class AgentLoopTests(unittest.TestCase):
    def test_runs_search_observe_reflect_and_synthesize_cycle(self) -> None:
        provider = StubProvider()
        loop = AgenticMarketLoop(provider=provider, config=AgentLoopConfig(max_iterations=8))

        result = loop.run(AgentLoopTask("Review names", ("aapl", "msft")))

        self.assertTrue(result.completed)
        self.assertEqual(provider.search_requests, ["aapl", "msft"])
        self.assertEqual(provider.history_requests, ["AAPL", "MSFT"])
        self.assertEqual([item.instrument.symbol for item in result.observations], ["AAPL", "MSFT"])
        self.assertIn("AAPL: 103.00", result.final_answer)
        self.assertIn("via Stub", result.final_answer)
        self.assertIn("synthesize", [event.action for event in result.events])
        self.assertIn("reflect", [event.phase for event in result.events])

    def test_records_failures_and_continues_remaining_work(self) -> None:
        provider = StubProvider()
        loop = AgenticMarketLoop(provider=provider, config=AgentLoopConfig(max_iterations=8))

        result = loop.run(AgentLoopTask("Review names", ("MISSING", "FAIL", "AAPL")))

        self.assertTrue(result.completed)
        self.assertEqual([item.instrument.symbol for item in result.observations], ["AAPL"])
        self.assertIn("MISSING: no matching instrument found", result.final_answer)
        self.assertIn("FAIL: history failed: provider down", result.final_answer)

    def test_stops_at_iteration_limit_with_partial_result(self) -> None:
        provider = StubProvider()
        loop = AgenticMarketLoop(provider=provider, config=AgentLoopConfig(max_iterations=1))

        result = loop.run(AgentLoopTask("Review names", ("AAPL", "MSFT")))

        self.assertFalse(result.completed)
        self.assertIn("No usable market observations", result.final_answer)
        self.assertEqual(result.events[-1].phase, "stop")

    def test_market_observation_requires_usable_close_bars(self) -> None:
        frame = pd.DataFrame({"Close": [10.0]}, index=pd.to_datetime(["2026-05-29"]))

        with self.assertRaises(ValueError):
            build_market_observation(Instrument("AAPL", "Apple"), frame, min_bars=2)

    def test_task_defaults_to_one_year_historical_range(self) -> None:
        task = AgentLoopTask("Default range", ("AAPL",))

        self.assertEqual(task.range_spec, HISTORICAL_RANGES[3])


if __name__ == "__main__":
    unittest.main()
