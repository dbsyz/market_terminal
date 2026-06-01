from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests


FRED_API_BASE = "https://api.stlouisfed.org/fred"


@dataclass(frozen=True)
class FredSeriesSpec:
    series_id: str
    title: str
    category: str
    units: str = "lin"
    notes: str = ""


@dataclass(frozen=True)
class FredObservation:
    date: pd.Timestamp
    value: float | None
    realtime_start: str = ""
    realtime_end: str = ""


CURATED_FRED_SERIES = (
    FredSeriesSpec("FEDFUNDS", "Effective Federal Funds Rate", "rates"),
    FredSeriesSpec("DGS10", "10-Year Treasury Constant Maturity Rate", "rates"),
    FredSeriesSpec("DGS2", "2-Year Treasury Constant Maturity Rate", "rates"),
    FredSeriesSpec("T10Y2Y", "10Y-2Y Treasury Spread", "rates"),
    FredSeriesSpec("CPIAUCSL", "Consumer Price Index for All Urban Consumers", "inflation"),
    FredSeriesSpec("PCEPI", "Personal Consumption Expenditures Price Index", "inflation"),
    FredSeriesSpec("UNRATE", "Unemployment Rate", "labor"),
    FredSeriesSpec("PAYEMS", "Nonfarm Payrolls", "labor"),
    FredSeriesSpec("GDP", "Gross Domestic Product", "growth"),
    FredSeriesSpec("INDPRO", "Industrial Production Index", "growth"),
    FredSeriesSpec("M2SL", "M2 Money Stock", "money"),
    FredSeriesSpec("BAMLH0A0HYM2", "ICE BofA US High Yield OAS", "credit"),
)


class FredClient:
    def __init__(
        self,
        api_key: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("FRED_API_KEY", "").strip()
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def observations(
        self,
        series_id: str,
        observation_start: str = "",
        observation_end: str = "",
        units: str = "lin",
    ) -> tuple[FredObservation, ...]:
        payload = self._get(
            "series/observations",
            {
                "series_id": series_id,
                "units": units,
                "observation_start": observation_start,
                "observation_end": observation_end,
            },
        )
        return parse_fred_observations(payload)

    def observations_frame(
        self,
        series_id: str,
        observation_start: str = "",
        observation_end: str = "",
        units: str = "lin",
    ) -> pd.DataFrame:
        observations = self.observations(series_id, observation_start, observation_end, units)
        frame = pd.DataFrame(
            {"Value": [observation.value for observation in observations]},
            index=[observation.date for observation in observations],
        )
        frame.index.name = "Date"
        frame.attrs["data_source"] = "FRED"
        frame.attrs["series_id"] = series_id
        return frame

    def _get(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("FRED_API_KEY is required for FRED requests")
        request_params = {
            "api_key": self.api_key,
            "file_type": "json",
            **{key: value for key, value in params.items() if value},
        }
        response = self.session.get(
            f"{FRED_API_BASE}/{endpoint}",
            params=request_params,
            timeout=15,
        )
        response.raise_for_status()
        return response.json()


def curated_fred_series(category: str = "") -> tuple[FredSeriesSpec, ...]:
    if not category:
        return CURATED_FRED_SERIES
    key = category.strip().lower()
    return tuple(series for series in CURATED_FRED_SERIES if series.category == key)


def parse_fred_observations(payload: dict[str, Any]) -> tuple[FredObservation, ...]:
    observations = []
    for record in payload.get("observations", []):
        value = _optional_float(record.get("value"))
        date = pd.to_datetime(record.get("date"), errors="coerce")
        if pd.isna(date):
            continue
        observations.append(
            FredObservation(
                date=pd.Timestamp(date),
                value=value,
                realtime_start=str(record.get("realtime_start", "")),
                realtime_end=str(record.get("realtime_end", "")),
            )
        )
    return tuple(observations)


def _optional_float(value: Any) -> float | None:
    if value in {None, "", "."}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
