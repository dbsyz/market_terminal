# AGENTS.md

## Mission

Build Market Terminal into the closest possible free/public-data alternative to
a Bloomberg-style dashboard and Godel-style terminal. The app should maximize
financial data coverage, analysis quality, monitoring ergonomics, and
accessibility while staying honest about data provenance, delay, licensing, and
coverage limits.

The current product is a PC desktop app. The long-term product should become
highly accessible on mobile or near-mobile surfaces, with brainstorming and
experimentation expected around the best delivery path: native mobile app,
responsive web app, Telegram bot/channel integration, alerts, or another
lightweight monitor.

## Current Strategy

The work has two priorities:

1. Get the best and fullest publicly and freely available financial data.
2. Build a robust application framework that can grow into a phone-friendly
   monitor without making the current desktop app fragile.

Prefer changes that improve the platform over one-off demos. When a feature
needs a tradeoff between speed and future extensibility, choose the smallest
implementation that still leaves a clean path to scale.

## Core Product Areas

Expected product modules include, but are not limited to:

- Watchlist
- Charting interface
- Portfolio analysis tool
- Ticker-level and macro real-time news section
- AI-generated quick financial analysis
- Live selected-ticker pricing
- Alerts and monitoring
- Macro dashboard
- Cross-asset search and discovery
- Free-data provider coverage and quality diagnostics
- Mobile or phone-friendly monitoring surface

Do not assume this list is complete. The product plan will evolve as the app
develops.

## Data Principles

The app should seek broad, high-quality free/public data coverage across:

- Equities
- ETFs and funds
- Indices
- Rates and yield curves
- FX
- Crypto
- Commodities
- Macro indicators
- News
- Corporate actions
- Fundamentals
- Portfolio holdings and local user data

For every provider or dataset:

- Document the source, API/key requirements, limits, delay, terms concerns, and
  practical coverage.
- Be explicit when data is delayed, incomplete, best-effort, or unavailable.
- Prefer source-specific series over silently merging incompatible vendor bars.
- Keep provider ranking and fallback behavior explainable.
- Do not commit secrets, API keys, local `.env` files, caches, downloaded
  market data, or private portfolio data.
- Avoid adding providers that look free but are effectively unusable for the
  intended feature without clearly documenting the limitation.

## App Architecture

The current app is a desktop Python application using Tkinter and matplotlib.
Preserve the current working app while making room for future surfaces.

Existing responsibilities:

- `run.py`: app entry point
- `app.py`: desktop UI and chart behavior
- `models.py`: shared data models
- `providers.py`: market data search/history/provider logic
- `provider_registry.py`: provider metadata, credentials, roadmap status, and health reporting
- `portfolio_index.py`: local portfolio index and monitoring helpers
- `agent_loop.py`: reusable agentic market-review loop
- `sec_edgar.py`: SEC company ticker, submissions, filing, and XBRL company-facts client
- `DATA_ROADMAP.md`: free/public data-source roadmap and integration priorities
- `tests/`: unit tests
- `out/`: generated runtime state, local caches, and outputs

When adding new capabilities:

- Keep provider/data acquisition logic out of UI code where practical.
- Prefer reusable service modules for analysis, agent workflows, alerts, and
  provider normalization.
- Add UI hooks after the underlying workflow is testable.
- Keep desktop UI dense, functional, and market-terminal oriented.
- Avoid broad rewrites unless they unlock a clearly needed architecture step.
- Think ahead to mobile-friendly monitoring: separate data/workflow state from
  the current Tkinter presentation when feasible.

## Agent Autonomy

Agents have maximum flexibility for building this app only. They may inspect,
edit, test, refactor, and create files as needed inside this project.

Agents should:

- Make reasonable implementation decisions without asking for permission on
  routine development work.
- Ask before major product-direction changes, destructive git operations,
  deleting user data, moving the app to a new framework, or introducing paid or
  legally sensitive data dependencies.
- Request additional command/network/filesystem access only when needed for the
  current app-building task.
- Keep changes scoped to the app and its documentation.
- Favor robust foundations over superficial features.

Agents should not:

- Use granted access for unrelated projects or personal files.
- Commit secrets, local caches, private data, or generated market files.
- Hide provider limitations behind polished UI.
- Break existing desktop functionality while exploring future interfaces.

## Git And Version Management

Agents are responsible for creating and maintaining a proper version-management
workflow because the user does not want to manage Git details manually.

Expected practices:

- Keep the repository in a state where rollback is easy.
- Check `git status` before and after work.
- Review diffs before final handoff.
- Preserve user changes; never reset or revert work that was not explicitly
  requested.
- Use small, meaningful commits when the user asks for commits or when a
  session explicitly includes version-management work.
- Prefer clear commit messages describing the product or technical outcome.
- Do not run destructive commands such as `git reset --hard`, forced checkout,
  or recursive deletion unless the user explicitly requests it and the target
  has been verified.
- Recommend tags or release checkpoints after meaningful stable milestones.

If Git is not initialized or the remote is not configured, agents should
propose or perform the setup when the task is version-management related.

## Development Commands

From `C:\Users\syzdy\python` using the shared virtual environment:

```powershell
test_venv\Scripts\python.exe market_terminal\run.py
test_venv\Scripts\python.exe -m unittest market_terminal.tests.test_agent_loop market_terminal.tests.test_chart_tools market_terminal.tests.test_providers
```

From inside `market_terminal`, imports may require the package parent on
`PYTHONPATH`. If using an activated environment from the package parent, the
same tests can be run with:

```powershell
python -m unittest market_terminal.tests.test_agent_loop market_terminal.tests.test_chart_tools market_terminal.tests.test_providers
```

Use the existing virtual environment unless a project-local environment is
explicitly chosen.

## Testing And Definition Of Done

For most code changes:

- Add or update focused tests when behavior changes.
- Run the relevant test subset.
- Run the full unit suite when touching shared provider, model, app, or
  workflow code.
- Launch the app for a manual smoke test when UI behavior changes and the
  environment allows it.
- Update README, AGENTS, PROJECT, TASKS, or `.env.example` when commands,
  dependencies, providers, architecture, or operating assumptions change.

A change is not done until the agent can state what changed, how it was
verified, and what risks or limitations remain.

## Product Planning

The product plan is expected to evolve. Agents should help shape it by
identifying:

- Best available free/public data sources by asset class and feature.
- Provider gaps and quality risks.
- Architecture needed for alerts, news, AI analysis, and mobile delivery.
- A practical path from desktop terminal to phone-friendly monitor.
- Milestones that create stable rollback points.

When brainstorming is required, provide concrete options with tradeoffs and a
recommended next step.

Use `DATA_ROADMAP.md` as the working map for provider and data-platform
implementation. Update it whenever a source is validated, rejected, integrated,
or materially changes its free/public access model.
