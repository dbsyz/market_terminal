# Market Terminal Project Context

## Purpose

Desktop charting workspace for intraday and historical market views across equities, funds, indices, currencies, crypto, and other Yahoo Finance-supported assets.

This project is not intended to be a licensed real-time terminal. Data can be delayed, incomplete, or unavailable depending on provider coverage and terms.

## Current State

- Main app entry point: `run.py`
- UI and chart logic: `app.py`
- Data models: `models.py`
- Provider/search logic: `providers.py`
- Tests: `tests/`
- Generated runtime state and caches: `out/`

The project already has a mature README, dependency file, and focused unit tests. It does not yet have its own Git repository.

## Main Commands

From `C:\Users\syzdy\python` using the shared workspace virtual environment:

```powershell
test_venv\Scripts\python.exe market_terminal\run.py
test_venv\Scripts\python.exe -m unittest discover -s market_terminal\tests
```

From inside `market_terminal` with a project-local or activated environment:

```powershell
python run.py
python -m unittest discover -s tests
```

## Dependencies

Install from:

```powershell
python -m pip install -r requirements.txt
```

Optional environment variables are documented in `.env.example` and `README.md`.

## Data Inputs

- Yahoo Finance data via `yfinance`
- Optional OpenFIGI identifier lookup via `OPENFIGI_API_KEY`
- Optional Twelve Data provider via `TWELVE_DATA_API_KEY`
- Optional Stooq historical fallback via `STOOQ_API_KEY`

## Generated Outputs

- `out/yfinance_cache/`
- `out/window_state.json`
- Python cache directories
- Test/cache artifacts

These should not be committed.

## Constraints

- Keep provider behavior honest about data availability, delays, and licensing limits.
- Avoid adding new live-data providers without documenting API limits, key requirements, and whether prices are delayed or premium.
- Keep desktop UI changes dense, functional, and consistent with the current market-terminal style.
- Keep generated caches, secrets, local state, and fetched market data out of Git.

## Do Not Touch Without Explicit Reason

- Do not commit provider API keys or local `.env` files.
- Do not commit `out/` cache/state files.
- Do not replace the current provider fallback model with undocumented merged data.
- Do not convert this into a web app unless explicitly requested.

## Definition Of Done

- Relevant tests pass with `python -m unittest discover -s tests` or the workspace equivalent.
- README or PROJECT context is updated when run commands, dependencies, providers, or generated files change.
- New generated/private files are covered by `.gitignore`.
- UI behavior changes are manually smoke-tested by launching `run.py` when practical.

## Manual UI Smoke Test

For chart or desktop UI changes, launch from `C:\Users\syzdy\python`:

```powershell
test_venv\Scripts\python.exe market_terminal\run.py
```

Minimum smoke path:

- Search and open `AAPL`.
- Confirm the chart renders, the status bar shows a data source, and the app remains responsive.
- Click `DATA STATUS` and confirm a provider status dialog opens.
- In the `MACRO` window, select a category and click `REFRESH`; with `FRED_API_KEY`
  configured it should populate latest macro values, and without a key it should
  fail without freezing the app.
- For a US equity, confirm the SEC context line either loads facts/filings or stays non-blocking if SEC is unavailable.
- Add one comparison series, then remove it.
- Save layout and close/reopen if layout behavior changed.
