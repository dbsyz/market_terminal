# Market Terminal Tasks

## Active

- Expose provider registry and health/status reporting in the desktop app.
- Validate and implement SEC EDGAR company facts and recent filings as the next high-value free data source.
- Initialize this folder as its own Git repository after reviewing the first standardized metadata pass.
- Decide whether to keep using the shared `test_venv` or move to a project-local `.venv`.
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
