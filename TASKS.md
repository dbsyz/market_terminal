# Market Terminal Tasks

## Active

- Add a richer SEC filings/fundamentals panel with clickable filing links and caching.
- Add a small smoke-test note for manual UI verification after chart or provider changes.

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
