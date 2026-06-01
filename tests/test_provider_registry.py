from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from market_terminal.provider_registry import (
    STATUS_LIMITED,
    STATUS_MISSING_FILES,
    STATUS_MISSING_KEY,
    STATUS_PLANNED,
    STATUS_READY,
    provider_health_report,
    provider_health_summary,
    provider_specs,
)


class ProviderRegistryTests(unittest.TestCase):
    def test_lists_implemented_and_planned_provider_specs(self) -> None:
        specs = provider_specs()

        self.assertIn("yahoo", [spec.provider_id for spec in specs])
        self.assertIn("sec_edgar", [spec.provider_id for spec in specs])
        self.assertIn("fred", [spec.provider_id for spec in specs])
        self.assertTrue(next(spec for spec in specs if spec.provider_id == "sec_edgar").implemented)

    def test_marks_keyless_openfigi_as_limited_not_disabled(self) -> None:
        with patch.dict(os.environ, {"OPENFIGI_API_KEY": ""}, clear=False):
            health = _health_by_id()

        self.assertEqual(health["openfigi"].status, STATUS_LIMITED)
        self.assertTrue(health["openfigi"].configured)

    def test_marks_optional_keyed_providers_as_missing_key(self) -> None:
        with patch.dict(os.environ, {"TWELVE_DATA_API_KEY": "", "STOOQ_API_KEY": ""}, clear=False):
            health = _health_by_id()

        self.assertEqual(health["twelve_data"].status, STATUS_MISSING_KEY)
        self.assertEqual(health["stooq"].status, STATUS_MISSING_KEY)

    def test_marks_optional_keyed_provider_as_ready_when_env_is_set(self) -> None:
        with patch.dict(os.environ, {"TWELVE_DATA_API_KEY": "key"}, clear=False):
            health = _health_by_id()

        self.assertEqual(health["twelve_data"].status, STATUS_READY)

    def test_checks_required_local_portfolio_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory)
            (out_dir / "fort_pnl_index_constituents.csv").write_text("symbol\nAAPL\n")
            with patch.dict(os.environ, {"FORT_PNL_OUT_DIR": str(out_dir)}, clear=False):
                health = _health_by_id()

        self.assertEqual(health["fort_pnl"].status, STATUS_MISSING_FILES)
        self.assertIn("fort_pnl_index_summary.csv", health["fort_pnl"].detail)

    def test_marks_sec_edgar_as_limited_without_custom_user_agent(self) -> None:
        health = _health_by_id()

        self.assertEqual(health["sec_edgar"].status, STATUS_LIMITED)

    def test_marks_planned_sources_as_planned(self) -> None:
        health = _health_by_id()

        self.assertEqual(health["fred"].status, STATUS_PLANNED)

    def test_summary_is_human_readable(self) -> None:
        summary = provider_health_summary(include_planned=False)

        self.assertIn("Yahoo Finance via yfinance:", summary)


def _health_by_id():
    return {item.spec.provider_id: item for item in provider_health_report()}


if __name__ == "__main__":
    unittest.main()
