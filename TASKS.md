# Market Terminal Tasks

## Active

- Initialize this folder as its own Git repository after reviewing the first standardized metadata pass.
- Decide whether to keep using the shared `test_venv` or move to a project-local `.venv`.
- Add a small smoke-test note for manual UI verification after chart or provider changes.

## Backlog

- Consider moving source files into a package layout only if packaging/distribution becomes a real need.
- Add a concise architecture section covering app, provider, and model responsibilities.
- Add CI later if this project is pushed to GitHub.

## Done

- Added project-local ignore rules for generated output, caches, virtual environments, and secrets.
- Added `.env.example` for optional provider keys.
- Added `PROJECT.md` as agent-facing operating context.
