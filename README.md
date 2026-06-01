# Market Terminal

A desktop charting workspace for intraday and historical market views. It
searches equities, funds, indices, currencies,
cryptocurrencies, and other assets exposed through Yahoo Finance, then plots a
fast price-and-volume chart in a native desktop window.

This is not a Bloomberg Terminal replacement or a licensed real-time feed.
Yahoo Finance data may be delayed, incomplete, or unavailable for some
instruments; confirm Yahoo's terms before any non-personal or production use.
OpenFIGI supplies identifier mapping, not prices.

## Features

- Hover the compact chart-surface `Time Range` chip to open both the intraday
  window/bar matrix and the historical horizon selectors.
- Intraday views use a window-by-bar matrix offering Yahoo-supported
  granularities from `1m` through `60m` where the selected horizon permits.
- Historical views provide `3M`, `6M`, `YTD`, `1Y`, `5Y`, and `MAX` ranges.
- The `Time Range` flyout also accepts inclusive custom start/end dates:
  selectable intraday bars or daily historical bars.
- Hover `Technical` on the chart toolbar to add `RSI` (7/14/21), percentage
  momentum (5/10/20 bars), or rolling return sigma (10/20/60 bars) over the
  volume pane on a separate scale; volume remains visible throughout.
- Search by ticker or company/asset name through Yahoo Finance.
- Enter terminal-style ticker/venue searches such as `KRW FP` or
  `KRW FP Equity`; supported exchange mnemonics are translated to the
  corresponding Yahoo-listed symbol, such as `KRW.PA`.
- Search by ISIN, CUSIP, or FIGI through OpenFIGI mapping followed by Yahoo
  price-symbol matching.
- Search results appear automatically as a temporary security-suggestion
  window while typing, remain inside the visible app area when adding
  comparisons, and disappear when the search field is cleared.
- Search suggestions begin unselected; use arrow keys to highlight a security
  and `Enter` to open it or add it while in comparison-add mode.
- Search suggestions can be ordered by provider relevance, reported market
  cap, or exchange, and filtered to a selected market.
- The selected-security header displays asset type and ISIN when available
  from an identifier lookup or public instrument metadata; Euronext listings
  such as `KRW.PA` retry against Euronext's public instrument search.
- Drag across displayed text such as labels, table rows, or chart annotations
  to draw a rectangular selection and copy the selected visible text block
  to the clipboard; dragging the chart plot background continues to zoom.
- Press `Ctrl+F` anywhere in the app to focus and select the main search field.
- While developing, edits to runtime source files trigger a `New version
  available` banner with a one-click app reload action.
- The desktop window restores its exact last normal size and position, or
  returns maximized if it was closed maximized.
- Optional pre/post-market display where Yahoo provides extended-hours bars.
- Market footer showing reported open/closed state, pre/post-market
  availability, and regular-session hours in exchange and local time.
- Data-source attribution in the status bar and configured provider fallback
  for bars: Yahoo Finance first, optional Twelve Data, then optional Stooq
  end-of-day history.
- Dark market-terminal UI with price change, volume, and stale-request
  protection when switching charts quickly.
- `Open` a primary asset and `Add` up to nine additional chart series for
  ten-line comparison overlays.
- Tick `Compare [n]` in the chart toolbar to reveal a slim chart-right series
  column; type directly into its `Add series` field and select a result.
- Tick `Rebase series to 100` inside the comparison column when comparing
  normalized performance instead of raw prices.
- Tick `Betas` in the comparison column to run a joint OLS of the primary
  series' active-bar returns against all added series' returns for the
  selected time range and bar size; the table shows beta, standard error,
  t-statistic, and p-value for each X plus compact model fit and intercept
  statistics.
- In `MAX` multi-series historical views, history starts at the newest inception date
  among the visible assets so every line is comparable from the first point.
- Rectangle zoom by dragging over the price chart; right-click to reset zoom.
- Hover over the price chart to see the nearest date and visible value for
  every plotted series, including indexed comparison values.
- Right-click `% / Points Return` tool for selecting two close-price dots and
  displaying both point and percentage return for the primary (`Open`ed)
  series.
- Search `FORT_PNL` to open the local custom portfolio index built from
  `portfolio_review\out\fort_pnl_index_levels.csv`; its monitoring helper
  also reads constituents, index summary, and 2026 trade files for
  trade-aware PnL and concentration reporting.

## Run

The existing workspace virtual environment already contains the dependencies:

```powershell
test_venv\Scripts\python.exe market_terminal\run.py
```

For a new virtual environment:

```powershell
python -m pip install -r market_terminal\requirements.txt
python market_terminal\run.py
```

Enter examples such as `AAPL`, `Apple`, `EURUSD=X`, `BTC-USD`,
`US0378331005` (ISIN), `037833100` (CUSIP), or `FORT_PNL`.

## Agentic Loop

`market_terminal.agent_loop` provides a reusable observe-act-reflect process
for market review workflows. It uses the existing `MarketDataProvider` to
search instruments, fetch history, build observations, reflect after each
step, and synthesize a compact review.

```python
from market_terminal.agent_loop import AgentLoopTask, AgenticMarketLoop

result = AgenticMarketLoop().run(
    AgentLoopTask("Review portfolio leaders", ("AAPL", "MSFT", "FORT_PNL"))
)
print(result.final_answer)
```

Use `iter_events()` instead of `run()` when wiring the loop into a UI,
scheduler, or streaming process.

## Local Portfolio Index

`FORT_PNL` is a local custom index sourced from:

- `C:\Users\syzdy\python\portfolio_review\out\fort_pnl_index_constituents.csv`
- `C:\Users\syzdy\python\portfolio_review\out\fort_pnl_index_summary.csv`
- `C:\Users\syzdy\python\portfolio_review\out\fort_pnl_index_levels.csv`
- `C:\Users\syzdy\python\portfolio_review\out\portfolio_new_trades_2026.csv`

The app charts the index level as a portfolio-index series. The helper
`build_portfolio_monitor_report()` in `market_terminal.portfolio_index`
generates top movers, PnL contribution, risk concentration, trade-aware
activity, realized PnL, and benchmark workflow notes from those files.

To point the app at a different output directory:

```powershell
$env:FORT_PNL_OUT_DIR = "C:\path\to\portfolio_review\out"
test_venv\Scripts\python.exe market_terminal\run.py
```

## Identifier Mapping

Ticker and free-text queries go straight to Yahoo Finance. Queries shaped as
an ISIN, CUSIP, or FIGI are mapped through the public OpenFIGI API first.
Unauthenticated OpenFIGI requests have a lower rate limit. To use your key:

```powershell
$env:OPENFIGI_API_KEY = "your-key"
test_venv\Scripts\python.exe market_terminal\run.py
```

An ISIN or CUSIP can map to multiple listings. Select the desired market in the
results list before reading the chart.

## Data Sources

- Yahoo Finance is the primary no-key provider for search, intraday and
  historical bars, and session metadata.
- Stooq can be configured as a daily/weekly/monthly historical fallback only.
  Its CSV endpoint currently requires a free key obtained from Stooq's
  captcha-protected download workflow; it is not used for the intraday matrix.
- Twelve Data is an optional configured source for additional search and bar
  coverage. Set a free-tier API key before launching:

```powershell
$env:TWELVE_DATA_API_KEY = "your-key"
test_venv\Scripts\python.exe market_terminal\run.py
```

- To enable Stooq historical fallback after obtaining its CSV download key:

```powershell
$env:STOOQ_API_KEY = "your-key"
test_venv\Scripts\python.exe market_terminal\run.py
```

- OpenFIGI API mapping is used for standardized identifier lookup:
  <https://www.openfigi.com/api/documentation>

The application stores the local `yfinance` cache under
`market_terminal/out/yfinance_cache/`.

The app deliberately does not merge candles from multiple vendors into one
line, because pricing adjustments, session rules, and delays can differ. When
more than one configured provider returns a series, it ranks complete
candidates by freshness, positive/valid closes, OHLCV completeness, timestamp
regularity, and usable bar count. The selected source and its quality score are
shown in the chart status bar. A stale historical series can still display
(for example a delisted asset), but it ranks below fresher equivalent data.

Alpha Vantage currently documents useful intraday stock time-series access as
premium; Finnhub documents stock candle access as premium, so neither is
enabled as a misleading "free" bar source here.

Provider documentation:

- Yahoo access via `yfinance`: <https://ranaroussi.github.io/yfinance/>
- Stooq free historical data: <https://stooq.com/db/h/>
- Twelve Data API: <https://twelvedata.com/docs/advanced>
- Alpha Vantage API: <https://www.alphavantage.co/documentation/>
- Finnhub API: <https://finnhub.io/docs/api>
