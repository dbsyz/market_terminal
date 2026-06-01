from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .fred_macro import FredClient, FredSeriesSpec, curated_fred_series


@dataclass(frozen=True)
class MacroSeriesSnapshot:
    series: FredSeriesSpec
    latest_date: pd.Timestamp | None
    latest_value: float | None
    previous_value: float | None

    @property
    def change(self) -> float | None:
        if self.latest_value is None or self.previous_value is None:
            return None
        return self.latest_value - self.previous_value


@dataclass(frozen=True)
class MacroDashboardSnapshot:
    category: str
    series: tuple[MacroSeriesSnapshot, ...]
    source: str = "FRED"


class MacroDashboardService:
    def __init__(self, fred: FredClient | None = None) -> None:
        self.fred = fred or FredClient()

    def series_specs(self, category: str = "") -> tuple[FredSeriesSpec, ...]:
        return curated_fred_series(category)

    def snapshot(
        self,
        category: str = "",
        observation_start: str = "",
    ) -> MacroDashboardSnapshot:
        specs = curated_fred_series(category)
        snapshots = tuple(
            build_macro_series_snapshot(
                spec,
                self.fred.observations_frame(
                    spec.series_id,
                    observation_start=observation_start,
                    units=spec.units,
                ),
            )
            for spec in specs
        )
        return MacroDashboardSnapshot(category=category or "all", series=snapshots)


def build_macro_series_snapshot(
    spec: FredSeriesSpec,
    frame: pd.DataFrame,
) -> MacroSeriesSnapshot:
    if frame.empty or "Value" not in frame:
        return MacroSeriesSnapshot(spec, None, None, None)
    values = pd.to_numeric(frame["Value"], errors="coerce").dropna()
    if values.empty:
        return MacroSeriesSnapshot(spec, None, None, None)
    latest_value = float(values.iloc[-1])
    previous_value = float(values.iloc[-2]) if len(values) >= 2 else None
    return MacroSeriesSnapshot(
        series=spec,
        latest_date=pd.Timestamp(values.index[-1]),
        latest_value=latest_value,
        previous_value=previous_value,
    )


def format_macro_dashboard_snapshot(snapshot: MacroDashboardSnapshot) -> str:
    if not snapshot.series:
        return f"Macro dashboard ({snapshot.category}): no configured series."
    lines = [f"Macro dashboard ({snapshot.category}) via {snapshot.source}:"]
    for item in snapshot.series:
        if item.latest_value is None or item.latest_date is None:
            lines.append(f"- {item.series.series_id}: unavailable")
            continue
        change = "" if item.change is None else f" ({item.change:+.2f})"
        lines.append(
            f"- {item.series.series_id} {item.series.title}: "
            f"{item.latest_value:.2f}{change} as of {item.latest_date.date()}"
        )
    return "\n".join(lines)
