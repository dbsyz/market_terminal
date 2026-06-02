from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf

from .models import Instrument, MarketSession, RangeSpec


PORTFOLIO_INDEX_SYMBOL = "FORT_PNL"
PORTFOLIO_INDEX_NAME = "FORT_PNL custom portfolio index"
BASE_LEVEL = 100.0
DEFAULT_PORTFOLIO_OUT_DIR = Path(r"C:\Users\syzdy\python\portfolio_review\out")
_YAHOO_SYMBOL_ALIASES = {
    "ASML": "ASML.AS",
    "ENR": "ENR.DE",
    "ESD": "ESD.PA",
    "GOLD-EUR": "GOLD.PA",
    "HY9H": "HY9H.F",
    "IBGM": "IBGM.MI",
    "KI5": "KI5.F",
    "KRW": "KRW.PA",
    "MEUD": "MEUD.PA",
    "R2US": "R2US.PA",
    "SEME": "SEME.PA",
    "TPXE": "TPXE.PA",
}


@dataclass(frozen=True)
class PortfolioIndexFiles:
    constituents: Path
    summary: Path
    levels: Path
    trades: Path
    full_trades: Path


@dataclass(frozen=True)
class PortfolioConstituentQuote:
    ticker: str
    yahoo_symbol: str
    name: str
    weight_pct: float
    last_price: float | None
    last_updated: str
    snapshot_price: float | None
    snapshot_date: str


def portfolio_index_files() -> PortfolioIndexFiles:
    out_dir = Path(os.getenv("FORT_PNL_OUT_DIR", str(DEFAULT_PORTFOLIO_OUT_DIR)))
    return PortfolioIndexFiles(
        constituents=out_dir / "fort_pnl_index_constituents.csv",
        summary=out_dir / "fort_pnl_index_summary.csv",
        levels=out_dir / "fort_pnl_index_levels.csv",
        trades=out_dir / "portfolio_new_trades_2026.csv",
        full_trades=out_dir / "fort_pnl_trade_table.csv",
    )


def portfolio_index_instrument() -> Instrument:
    market_value = _portfolio_market_value()
    return Instrument(
        PORTFOLIO_INDEX_SYMBOL,
        PORTFOLIO_INDEX_NAME,
        exchange="USER OWNED",
        quote_type="Portfolio Index",
        currency="EUR",
        source="FORT_PNL CSV",
        market_cap=market_value,
        aum=market_value,
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
    if len(levels) <= 2 and os.getenv("FORT_PNL_DISABLE_SYNTHETIC_HISTORY") != "1":
        synthetic = _build_current_weight_index_history(files, levels)
        if not synthetic.empty:
            return _clip_index_history(synthetic, range_spec)
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
    frame = _attach_trade_cash_columns(frame, files)
    frame.attrs["data_source"] = "FORT_PNL local index levels"
    return _clip_index_history(frame, range_spec)


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


def portfolio_constituent_quotes(lookback_days: int = 10) -> tuple[PortfolioConstituentQuote, ...]:
    files = portfolio_index_files()
    constituents = _read_constituents(files.constituents)
    end = _current_date()
    start = end - pd.Timedelta(days=lookback_days)
    symbols = sorted(
        {
            _portfolio_yahoo_symbol(str(row.ticker).strip())
            for row in constituents.itertuples()
            if _portfolio_yahoo_symbol(str(row.ticker).strip())
        }
    )
    closes = _download_constituent_closes(symbols, start, end) if symbols else pd.DataFrame()
    quotes = []
    for row in constituents.sort_values("weight_pct", ascending=False).itertuples():
        ticker = str(row.ticker).strip()
        yahoo_symbol = _portfolio_yahoo_symbol(ticker)
        latest = _latest_close(closes[yahoo_symbol]) if yahoo_symbol in closes else None
        quotes.append(
            PortfolioConstituentQuote(
                ticker=ticker,
                yahoo_symbol=yahoo_symbol,
                name=str(row.name).strip(),
                weight_pct=float(row.weight_pct),
                last_price=latest[1] if latest else None,
                last_updated=_format_price_timestamp(latest[0]) if latest else "",
                snapshot_price=_as_optional_float(getattr(row, "price", None)),
                snapshot_date=str(getattr(row, "as_of_date", "")).strip(),
            )
        )
    return tuple(quotes)


def _portfolio_market_value() -> float | None:
    try:
        summary = _read_summary(portfolio_index_files().summary)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return None
    return _as_optional_float(summary.get("market_value_eur"))


def _build_current_weight_index_history(
    files: PortfolioIndexFiles, levels: pd.DataFrame
) -> pd.DataFrame:
    constituents = _read_constituents(files.constituents)
    snapshot_date = pd.to_datetime(levels["date"]).max().normalize()
    live_end = max(snapshot_date, _current_date())
    start = _first_trade_date(files) or pd.Timestamp(f"{snapshot_date.year}-01-01")
    latest_level = _latest_numeric(levels, "index_level")
    weighted_prices = _download_weighted_constituent_prices(constituents, start, live_end)
    if weighted_prices.empty:
        return pd.DataFrame()
    normalized = weighted_prices / weighted_prices.iloc[0]
    raw_path = normalized.sum(axis=1)
    if raw_path.empty or not float(raw_path.iloc[0]):
        return pd.DataFrame()
    raw_return = raw_path / float(raw_path.iloc[0])
    snapshot_return = _latest_at_or_before(raw_return, snapshot_date)
    if snapshot_return is None:
        snapshot_return = float(raw_return.iloc[-1])
    snapshot_total_return = float(snapshot_return - 1)
    if abs(snapshot_total_return) < 1e-12:
        index_path = latest_level * raw_return / float(snapshot_return or 1)
    else:
        scaled_progress = (raw_return - 1) / snapshot_total_return
        index_path = BASE_LEVEL + scaled_progress * (latest_level - BASE_LEVEL)
    frame = pd.DataFrame(
        {
            "Open": index_path.to_numpy(),
            "High": index_path.to_numpy(),
            "Low": index_path.to_numpy(),
            "Close": index_path.to_numpy(),
            "Volume": 0.0,
        },
        index=index_path.index,
    )
    frame = _attach_trade_cash_columns(frame, files)
    if frame.index.max().normalize() > snapshot_date:
        frame.attrs["data_source"] = (
            "FORT_PNL live-estimated current-weight constituent history via Yahoo Finance"
        )
    else:
        frame.attrs["data_source"] = "FORT_PNL synthesized current-weight constituent history"
    frame.attrs["portfolio_snapshot_date"] = snapshot_date.date().isoformat()
    return frame.dropna(subset=["Close"])


def _current_date() -> pd.Timestamp:
    return pd.Timestamp.today().normalize()


def _latest_at_or_before(series: pd.Series, date: pd.Timestamp) -> float | None:
    values = series.loc[series.index <= date].dropna()
    if values.empty:
        return None
    return float(values.iloc[-1])


def _attach_trade_cash_columns(frame: pd.DataFrame, files: PortfolioIndexFiles) -> pd.DataFrame:
    if frame.empty:
        return frame
    trades = _read_full_trade_cash(files.full_trades)
    if trades.empty:
        trades = _read_trades(files.trades) if files.trades.exists() else pd.DataFrame()
    if trades.empty:
        return frame
    trades["trade_date"] = pd.to_datetime(trades["trade_date"], errors="coerce")
    trades = trades.dropna(subset=["trade_date"])
    buys = (
        trades.loc[trades["quantity_signed"] > 0]
        .groupby("trade_date")["net_trade_cash_eur"]
        .sum()
        .abs()
    )
    sells = (
        trades.loc[trades["quantity_signed"] < 0]
        .groupby("trade_date")["net_trade_cash_eur"]
        .sum()
        .abs()
    )
    result = frame.copy()
    result["BuyCashEUR"] = buys.reindex(result.index, fill_value=0.0).to_numpy()
    result["SellCashEUR"] = sells.reindex(result.index, fill_value=0.0).to_numpy()
    result["Volume"] = result["BuyCashEUR"] + result["SellCashEUR"]
    return result


def _read_full_trade_cash(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        frame = pd.read_csv(path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return pd.DataFrame()
    required = {"trade_date", "side", "net_cash_in_eur"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    net_cash = pd.to_numeric(frame["net_cash_in_eur"], errors="coerce").fillna(0.0)
    is_buy = frame["side"].fillna("").astype(str).str.lower().str.startswith("achat")
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(frame["trade_date"], errors="coerce", dayfirst=True),
            "quantity_signed": is_buy.map({True: 1.0, False: -1.0}),
            "net_trade_cash_eur": net_cash,
        }
    )


def _first_trade_date(files: PortfolioIndexFiles) -> pd.Timestamp | None:
    for path, column, dayfirst in (
        (files.full_trades, "trade_date", True),
        (files.trades, "trade_date", False),
    ):
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, usecols=[column])
        except (ValueError, FileNotFoundError, pd.errors.EmptyDataError):
            continue
        dates = pd.to_datetime(frame[column], errors="coerce", dayfirst=dayfirst).dropna()
        if not dates.empty:
            return pd.Timestamp(dates.min()).normalize()
    return None


def _download_weighted_constituent_prices(
    constituents: pd.DataFrame, start: pd.Timestamp, as_of: pd.Timestamp
) -> pd.DataFrame:
    symbols = []
    weights = {}
    for row in constituents.itertuples():
        ticker = str(row.ticker).strip()
        yahoo_symbol = _portfolio_yahoo_symbol(ticker)
        if not yahoo_symbol:
            continue
        symbols.append(yahoo_symbol)
        weights[yahoo_symbol] = float(row.weight_pct)
    if not symbols:
        return pd.DataFrame()
    closes = _download_constituent_closes(sorted(set(symbols)), start, as_of)
    closes = closes.dropna(axis=1, how="all").ffill().dropna(how="all")
    if closes.empty:
        return closes
    available_weights = pd.Series(
        {symbol: weights[symbol] for symbol in closes.columns}, dtype=float
    )
    available_weights = available_weights / available_weights.sum()
    return closes.mul(available_weights, axis=1)


def _download_constituent_closes(
    symbols: list[str], start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    prices = yf.download(
        symbols,
        start=start.strftime("%Y-%m-%d"),
        end=(end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )
    return _extract_download_closes(prices, symbols)


def _latest_close(series: pd.Series) -> tuple[pd.Timestamp, float] | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return None
    return pd.Timestamp(values.index[-1]), float(values.iloc[-1])


def _format_price_timestamp(value: pd.Timestamp) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("Europe/Paris")
    return timestamp.strftime("%Y-%m-%d %H:%M")


def _extract_download_closes(download: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    if download.empty:
        return pd.DataFrame()
    if isinstance(download.columns, pd.MultiIndex):
        closes = {}
        for symbol in symbols:
            if (symbol, "Close") in download.columns:
                closes[symbol] = pd.to_numeric(download[(symbol, "Close")], errors="coerce")
        return pd.DataFrame(closes)
    if "Close" in download:
        return pd.DataFrame({symbols[0]: pd.to_numeric(download["Close"], errors="coerce")})
    return pd.DataFrame()


def _portfolio_yahoo_symbol(ticker: str) -> str:
    if not ticker:
        return ""
    return _YAHOO_SYMBOL_ALIASES.get(ticker.upper(), ticker.upper())


def _clip_index_history(frame: pd.DataFrame, range_spec: RangeSpec) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame
    if range_spec.start and range_spec.end:
        start = pd.Timestamp(range_spec.start)
        end = pd.Timestamp(range_spec.end)
        result = frame.loc[(frame.index >= start) & (frame.index <= end)].copy()
    elif range_spec.period == "ytd":
        start = pd.Timestamp(f"{frame.index[-1].year}-01-01")
        result = frame.loc[frame.index >= start].copy()
    elif range_spec.period in {"1d", "5d"}:
        periods = 1 if range_spec.period == "1d" else 5
        result = frame.tail(periods).copy()
    elif range_spec.period not in {"max", "1d", "5d"}:
        offsets = {
            "1mo": pd.DateOffset(months=1),
            "3mo": pd.DateOffset(months=3),
            "6mo": pd.DateOffset(months=6),
            "1y": pd.DateOffset(years=1),
            "5y": pd.DateOffset(years=5),
        }
        start = frame.index[-1] - offsets.get(range_spec.period, pd.DateOffset(months=3))
        result = frame.loc[frame.index >= start].copy()
    result.attrs.update(frame.attrs)
    return result


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


def _as_optional_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


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
