# Market Terminal Tasks

## Active

- Add desktop UI entry point for curated FRED macro dashboard.

## Backlog

- Add FRED and ECB macro clients with curated series packs.
- Add GDELT-based ticker/macro news monitoring with noise filtering.
- Evaluate Telegram alerts versus responsive local web UI as the first phone-friendly monitor surface.
- Consider moving source files into a package layout only if packaging/distribution becomes a real need.
- Add a concise architecture section covering app, provider, and model responsibilities.
- Add CI later if this project is pushed to GitHub.

## Done

- Added project-local ignore rules for generated output, caches, virtual environments, and secrets.
- Added `.env.example` for optional provider keys.
- Added `PROJECT.md` as agent-facing operating context.
- Added `DATA_ROADMAP.md` for free/public data-source priorities.
- Added `provider_registry.py` for provider metadata and non-network health checks.
- Exposed provider health/status reporting through the desktop app header.
- Added `sec_edgar.py` for SEC ticker lookup, recent filing parsing, and XBRL company-facts snapshots.
- Exposed SEC company facts and recent filings as a non-blocking selected-ticker context line.
- Established Git rollback checkpoints for the agent workflow and data-provider foundation.
- Kept the shared `test_venv` as the current working environment.
- Added a manual UI smoke-test checklist to `PROJECT.md`.
- Added a richer SEC details popup with fundamentals, filings, and filing-link opening.
- Added in-session and ignored disk caching for SEC EDGAR JSON responses.
- Added SEC cache age display and clear-cache control.
- Added `fred_macro.py` for FRED observations and curated macro/rates series metadata.
- Added `macro_dashboard.py` for UI-independent macro dashboard snapshots.
