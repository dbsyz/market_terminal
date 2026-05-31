from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .models import Instrument, MarketSession, RangeSpec


PORTFOLIO_INDEX_SYMBOL = "FORT_PNL"
PORTFOLIO_INDEX_NAME = "FORT_PNL custom portfolio index"
DEFAULT_PORTFOLIO_OUT_DIR = Path(r"C:\Users\syzdy\python\portfolio_review\out")


@dataclass(frozen=True)
class PortfolioIndexFiles:
    constituents: Path
    summary: Path
    levels: Path
    trades: Path


def portfolio_index_files() -> PortfolioIndexFiles:
    out_dir = Path(os.getenv("FORT_PNL_OUT_DIR", str(DEFAULT_PORTFOLIO_OUT_DIR)))
    return PortfolioIndexFiles(
        constituents=out_dir / "fort_pnl_index_constituents.csv",
        summary=out_dir / "fort_pnl_index_summary.csv",
        levels=out_dir / "fort_pnl_index_levels.csv",
        trades=out_dir / "portfolio_new_trades_2026.csv",
    )


def portfolio_index_instrument() -> Instrument:
    return Instrument(
        PORTFOLIO_INDEX_SYMBOL,
        PORTFOLIO_INDEX_NAME,
        exchange="Local Portfolio",
        quote_type="Portfolio Index",
        currency="EUR",
        source="FORT_PNL CSV",
    )


def search_portfolio_index(query: str) -> list[Instrument]:
    normalized = query.strip().upper().replace(" ", "_").replace("-", "_")
    if normalized in {PORTFOLIO_INDEX_SYMBOL, "FORTPNL"}:
        return [portfolio_index_instrument()]
    return []


def load_portfolio_index_history(range_spec: RangeSpec) -> pd.DataFrame:
    files = portfolio_index_files()
    levels = pd.read_csv(files.levels)
    if levels.empty:
        return pd.DataFrame()
    index_levels = pd.to_numeric(levels["index_level"], errors="coerce").to_numpy()
    frame = pd.DataFrame(
        {
            "Open": index_levels,
            "High": index_levels,
            "Low": index_levels,
            "Close": index_levels,
            "Volume": 0.0,
        },
        index=pd.to_datetime(levels["date"]),
    ).dropna(subset=["Close"])
    frame = frame.sort_index()
    if range_spec.start and range_spec.end:
        start = pd.Timestamp(range_spec.start)
        end = pd.Timestamp(range_spec.end)
        frame = frame.loc[(frame.index >= start) & (frame.index <= end)]
    frame.attrs["data_source"] = "FORT_PNL local index levels"
    return frame


def portfolio_market_session() -> MarketSession:
    return MarketSession(
        status="LOCAL INDEX",
        exchange_timezone="Europe/Paris",
        extended_session="Not applicable",
        overnight_session="Not applicable",
    )


def build_portfolio_monitor_report() -> str:
    files = portfolio_index_files()
    constituents = _read_constituents(files.constituents)
    summary = _read_summary(files.summary)
    levels = pd.read_csv(files.levels)
    trades = _read_trades(files.trades)

    latest_level = _latest_numeric(levels, "index_level")
    as_of = str(summary.get("as_of_date", _latest_value(levels, "date", "")))
    market_value = float(summary.get("market_value_eur", constituents["market_value_eur"].sum()))
    total_pnl = float(summary.get("total_2026_pnl_eur", 0.0))
    realized_pnl = float(summary.get("realized_2026_pnl_eur", 0.0))
    ytd_pnl_pct = float(summary.get("ytd_pnl_pct", 0.0))

    report = [
        f"# {PORTFOLIO_INDEX_SYMBOL} Monitor",
        "",
        f"As of: {as_of}",
        f"Index level: {latest_level:,.4f}",
        f"Market value: EUR {market_value:,.2f}",
        f"YTD PnL: EUR {total_pnl:,.2f} ({ytd_pnl_pct:+.2f}%)",
        f"Realized 2026 PnL: EUR {realized_pnl:,.2f}",
        "",
        "## Top Movers",
        _markdown_table(
            _top_movers(constituents),
            ("ticker", "name", "weight_pct", "broker_unrealized_pnl_pct", "broker_unrealized_pnl_eur"),
            {
                "weight_pct": "{:,.2f}%",
                "broker_unrealized_pnl_pct": "{:+,.2f}%",
                "broker_unrealized_pnl_eur": "EUR {:+,.2f}",
            },
        ),
        "",
        "## PnL Contribution",
        _markdown_table(
            _pnl_contributors(constituents),
            ("ticker", "name", "weight_pct", "broker_unrealized_pnl_eur", "pnl_contribution_pct"),
            {
                "weight_pct": "{:,.2f}%",
                "broker_unrealized_pnl_eur": "EUR {:+,.2f}",
                "pnl_contribution_pct": "{:+,.2f}%",
            },
        ),
        "",
        "## Risk Concentration",
        *_risk_concentration_lines(constituents),
        "",
        "## 2026 Trade-Aware Monitoring",
        *_trade_lines(trades, constituents),
        "",
        "## Benchmark Workflow",
        "Open `FORT_PNL`, then add benchmark series in Compare mode. Use `Rebased 100` for level changes and `Betas` for return sensitivity versus the selected benchmark set.",
    ]
    return "\n".join(report).strip() + "\n"


def _read_constituents(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in (
        "market_value_eur",
        "weight_pct",
        "broker_unrealized_pnl_eur",
        "broker_unrealized_pnl_pct",
        "reconstructed_unrealized_pnl_eur",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    frame["ticker"] = frame["ticker"].fillna("").astype(str)
    frame["name"] = frame["name"].fillna("").astype(str)
    return frame


def _read_summary(path: Path) -> dict[str, str]:
    frame = pd.read_csv(path)
    return dict(zip(frame["metric"].astype(str), frame["value"].astype(str)))


def _read_trades(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in (
        "quantity_signed",
        "trade_price",
        "commission_eur",
        "net_trade_cash_eur",
        "realized_pnl_eur",
        "position_after_qty",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    frame["action"] = frame["action"].fillna("").astype(str).str.upper()
    frame["ticker"] = frame["ticker"].fillna("").astype(str)
    frame["isin"] = frame["isin"].fillna("").astype(str)
    frame["name"] = frame["name"].fillna("").astype(str)
    return frame


def _latest_numeric(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").dropna().iloc[-1])


def _latest_value(frame: pd.DataFrame, column: str, default: str) -> str:
    if frame.empty or column not in frame:
        return default
    return str(frame[column].iloc[-1])


def _top_movers(constituents: pd.DataFrame) -> pd.DataFrame:
    return constituents.assign(
        abs_move=constituents["broker_unrealized_pnl_pct"].abs()
    ).sort_values("abs_move", ascending=False).head(8)


def _pnl_contributors(constituents: pd.DataFrame) -> pd.DataFrame:
    total = constituents["broker_unrealized_pnl_eur"].sum()
    if not total:
        contribution = constituents["broker_unrealized_pnl_eur"] * 0
    else:
        contribution = constituents["broker_unrealized_pnl_eur"] / total * 100
    return constituents.assign(pnl_contribution_pct=contribution).sort_values(
        "broker_unrealized_pnl_eur", ascending=False
    ).head(10)


def _risk_concentration_lines(constituents: pd.DataFrame) -> list[str]:
    weights = constituents["weight_pct"].fillna(0.0)
    top_5 = weights.nlargest(5).sum()
    top_10 = weights.nlargest(10).sum()
    hhi = float(((weights / 100) ** 2).sum())
    currency = constituents.groupby("currency", dropna=False)["weight_pct"].sum().sort_values(
        ascending=False
    )
    largest = constituents.sort_values("weight_pct", ascending=False).head(5)
    lines = [
        f"- Largest position: {largest.iloc[0]['ticker']} at {largest.iloc[0]['weight_pct']:.2f}%",
        f"- Top 5 concentration: {top_5:.2f}%",
        f"- Top 10 concentration: {top_10:.2f}%",
        f"- Herfindahl-Hirschman index: {hhi:.4f}",
        "- Currency exposure: "
        + ", ".join(f"{currency_name or 'N/A'} {weight:.2f}%" for currency_name, weight in currency.items()),
        "- Largest weights: "
        + ", ".join(f"{row.ticker} {row.weight_pct:.2f}%" for row in largest.itertuples()),
    ]
    return lines


def _trade_lines(trades: pd.DataFrame, constituents: pd.DataFrame) -> list[str]:
    buys = trades[trades["quantity_signed"] > 0]
    sells = trades[trades["quantity_signed"] < 0]
    open_keys = set(zip(constituents["isin"].astype(str), constituents["ticker"].astype(str)))
    buy_groups = buys.groupby(["isin", "ticker", "name"], dropna=False).agg(
        buy_qty=("quantity_signed", "sum"),
        buy_cash_eur=("net_trade_cash_eur", "sum"),
    ).reset_index()
    sell_groups = sells.groupby(["isin", "ticker", "name"], dropna=False).agg(
        sell_qty=("quantity_signed", "sum"),
        sell_cash_eur=("net_trade_cash_eur", "sum"),
        realized_pnl_eur=("realized_pnl_eur", "sum"),
    ).reset_index()
    new_positions = [
        row
        for row in buy_groups.itertuples()
        if (str(row.isin), str(row.ticker)) in open_keys
    ]
    closed_or_reduced = sell_groups.sort_values("realized_pnl_eur", ascending=True).head(5)
    lines = [
        f"- 2026 buys/adds: {len(buys)} trades, EUR {-buys['net_trade_cash_eur'].sum():,.2f} deployed.",
        f"- 2026 sells/reductions: {len(sells)} trades, EUR {sells['net_trade_cash_eur'].sum():,.2f} proceeds.",
        f"- Realized PnL from 2026 sells: EUR {sells['realized_pnl_eur'].sum():+,.2f}.",
        f"- Open positions touched by 2026 buys/adds: {len(new_positions)}.",
    ]
    if not closed_or_reduced.empty:
        lines.append(
            "- Sell/reduction realized PnL: "
            + ", ".join(
                f"{row.ticker or row.isin} EUR {row.realized_pnl_eur:+,.2f}"
                for row in closed_or_reduced.itertuples()
            )
        )
    return lines


def _markdown_table(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    formats: dict[str, str],
) -> str:
    if frame.empty:
        return "_No rows._"
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _column in columns) + " |"
    rows = []
    for row in frame.loc[:, columns].itertuples(index=False, name=None):
        values = []
        for column, value in zip(columns, row):
            template = formats.get(column)
            if template:
                values.append(template.format(float(value)))
            else:
                values.append(str(value))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, separator, *rows])
