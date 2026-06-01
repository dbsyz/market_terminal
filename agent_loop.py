from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

import pandas as pd

from .models import HISTORICAL_RANGES, Instrument, RangeSpec
from .providers import MarketDataProvider


@dataclass(frozen=True)
class AgentLoopTask:
    goal: str
    queries: tuple[str, ...]
    range_spec: RangeSpec = HISTORICAL_RANGES[3]
    include_extended_hours: bool = False


@dataclass(frozen=True)
class AgentLoopConfig:
    max_iterations: int = 12
    min_bars: int = 2


@dataclass(frozen=True)
class MarketObservation:
    instrument: Instrument
    bars: int
    latest_close: float
    change: float
    change_pct: float
    start: pd.Timestamp
    end: pd.Timestamp
    data_source: str = ""


@dataclass(frozen=True)
class AgentLoopEvent:
    iteration: int
    phase: str
    message: str
    action: str = ""
    symbol: str = ""
    success: bool = True


@dataclass(frozen=True)
class AgentLoopResult:
    task: AgentLoopTask
    observations: tuple[MarketObservation, ...]
    events: tuple[AgentLoopEvent, ...]
    final_answer: str
    completed: bool


@dataclass
class _LoopState:
    pending_queries: list[str]
    instruments: list[Instrument] = field(default_factory=list)
    observed_symbols: set[str] = field(default_factory=set)
    observations: list[MarketObservation] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    final_answer: str = ""


class AgenticMarketLoop:
    """Small observe-act-reflect loop for market review workflows."""

    def __init__(
        self,
        provider: MarketDataProvider | None = None,
        config: AgentLoopConfig | None = None,
    ) -> None:
        self.provider = provider or MarketDataProvider()
        self.config = config or AgentLoopConfig()

    def iter_events(self, task: AgentLoopTask) -> Iterable[AgentLoopEvent]:
        state = _LoopState(pending_queries=_clean_queries(task.queries))
        yield AgentLoopEvent(
            iteration=0,
            phase="start",
            action="initialize",
            message=f"Starting market-agent loop: {task.goal}",
        )

        for iteration in range(1, self.config.max_iterations + 1):
            event = self._step(task, state, iteration)
            yield event
            yield self._reflect(state, iteration, event)
            if state.final_answer:
                break

        if not state.final_answer:
            state.final_answer = self._synthesize(state, completed=False)
            yield AgentLoopEvent(
                iteration=self.config.max_iterations,
                phase="stop",
                action="synthesize",
                message="Stopped after reaching the iteration limit.",
                success=False,
            )

    def run(self, task: AgentLoopTask) -> AgentLoopResult:
        state = _LoopState(pending_queries=_clean_queries(task.queries))
        events = [
            AgentLoopEvent(
                iteration=0,
                phase="start",
                action="initialize",
                message=f"Starting market-agent loop: {task.goal}",
            )
        ]

        for iteration in range(1, self.config.max_iterations + 1):
            event = self._step(task, state, iteration)
            events.append(event)
            events.append(self._reflect(state, iteration, event))
            if state.final_answer:
                break

        completed = bool(state.final_answer)
        if not state.final_answer:
            state.final_answer = self._synthesize(state, completed=False)
            events.append(
                AgentLoopEvent(
                    iteration=self.config.max_iterations,
                    phase="stop",
                    action="synthesize",
                    message="Stopped after reaching the iteration limit.",
                    success=False,
                )
            )

        return AgentLoopResult(
            task=task,
            observations=tuple(state.observations),
            events=tuple(events),
            final_answer=state.final_answer,
            completed=completed,
        )

    def _step(self, task: AgentLoopTask, state: _LoopState, iteration: int) -> AgentLoopEvent:
        if state.pending_queries:
            query = state.pending_queries.pop(0)
            return self._search(query, state, iteration)

        instrument = next(
            (
                value
                for value in state.instruments
                if value.symbol.upper() not in state.observed_symbols
            ),
            None,
        )
        if instrument is not None:
            return self._observe(task, state, iteration, instrument)

        state.final_answer = self._synthesize(state, completed=True)
        return AgentLoopEvent(
            iteration=iteration,
            phase="act",
            action="synthesize",
            message="Synthesized the market review from gathered observations.",
        )

    def _search(self, query: str, state: _LoopState, iteration: int) -> AgentLoopEvent:
        try:
            instruments = self.provider.search(query)
        except Exception as exc:
            detail = f"{query}: search failed: {exc}"
            state.failures.append(detail)
            return AgentLoopEvent(iteration, "act", detail, action="search", success=False)

        instrument = _select_instrument(query, instruments)
        if instrument is None:
            detail = f"{query}: no matching instrument found"
            state.failures.append(detail)
            return AgentLoopEvent(iteration, "act", detail, action="search", success=False)

        if instrument.symbol.upper() not in {value.symbol.upper() for value in state.instruments}:
            state.instruments.append(instrument)
        return AgentLoopEvent(
            iteration=iteration,
            phase="act",
            action="search",
            symbol=instrument.symbol,
            message=f"Selected {instrument.display_name}.",
        )

    def _observe(
        self,
        task: AgentLoopTask,
        state: _LoopState,
        iteration: int,
        instrument: Instrument,
    ) -> AgentLoopEvent:
        state.observed_symbols.add(instrument.symbol.upper())
        try:
            frame = self.provider.history(
                instrument,
                task.range_spec,
                include_extended_hours=task.include_extended_hours,
            )
            observation = build_market_observation(instrument, frame, self.config.min_bars)
        except Exception as exc:
            detail = f"{instrument.symbol}: history failed: {exc}"
            state.failures.append(detail)
            return AgentLoopEvent(
                iteration,
                "act",
                detail,
                action="observe_history",
                symbol=instrument.symbol,
                success=False,
            )

        state.observations.append(observation)
        return AgentLoopEvent(
            iteration=iteration,
            phase="act",
            action="observe_history",
            symbol=instrument.symbol,
            message=(
                f"Observed {instrument.symbol}: {observation.latest_close:.2f}, "
                f"{observation.change_pct:+.2f}% over {observation.bars} bars."
            ),
        )

    def _reflect(
        self,
        state: _LoopState,
        iteration: int,
        event: AgentLoopEvent,
    ) -> AgentLoopEvent:
        if not event.success:
            message = f"Reflection: recorded failure and continuing with remaining work."
        elif state.pending_queries:
            message = f"Reflection: {len(state.pending_queries)} search queries remain."
        else:
            unobserved = len(state.instruments) - len(state.observed_symbols)
            message = f"Reflection: {unobserved} selected instruments still need history."
        return AgentLoopEvent(iteration, "reflect", message)

    def _synthesize(self, state: _LoopState, completed: bool) -> str:
        return synthesize_market_review(state.observations, state.failures, completed)


def build_market_observation(
    instrument: Instrument,
    frame: pd.DataFrame,
    min_bars: int = 2,
) -> MarketObservation:
    if frame.empty or "Close" not in frame:
        raise ValueError("history frame has no close bars")

    close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if len(close) < min_bars:
        raise ValueError(f"history frame has fewer than {min_bars} usable close bars")

    first = float(close.iloc[0])
    latest = float(close.iloc[-1])
    change = latest - first
    change_pct = 0.0 if first == 0 else change / first * 100.0
    index = pd.to_datetime(close.index)
    source = str(frame.attrs.get("data_source", ""))
    return MarketObservation(
        instrument=instrument,
        bars=len(close),
        latest_close=latest,
        change=change,
        change_pct=change_pct,
        start=pd.Timestamp(index[0]),
        end=pd.Timestamp(index[-1]),
        data_source=source,
    )


def synthesize_market_review(
    observations: list[MarketObservation],
    failures: list[str],
    completed: bool = True,
) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not observations:
        suffix = "" if completed else " before the loop stopped"
        return f"No usable market observations were collected{suffix}."

    ranked = sorted(observations, key=lambda value: abs(value.change_pct), reverse=True)
    lines = [f"Market-agent review at {timestamp}:"]
    for observation in ranked:
        source = f" via {observation.data_source}" if observation.data_source else ""
        lines.append(
            "- "
            f"{observation.instrument.symbol}: {observation.latest_close:.2f}, "
            f"{observation.change:+.2f} ({observation.change_pct:+.2f}%) "
            f"from {observation.start.date()} to {observation.end.date()}"
            f"{source}"
        )
    if failures:
        lines.append("Failures: " + "; ".join(failures))
    if not completed:
        lines.append("The loop stopped before all planned work completed.")
    return "\n".join(lines)


def _clean_queries(queries: tuple[str, ...]) -> list[str]:
    seen = set()
    cleaned = []
    for query in queries:
        value = query.strip()
        key = value.upper()
        if value and key not in seen:
            cleaned.append(value)
            seen.add(key)
    return cleaned


def _select_instrument(query: str, instruments: list[Instrument]) -> Instrument | None:
    if not instruments:
        return None
    query_key = query.strip().upper()
    for instrument in instruments:
        if instrument.symbol.upper() == query_key:
            return instrument
    return instruments[0]
