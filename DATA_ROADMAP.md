# Data Roadmap

Last reviewed: 2026-06-01

This file tracks the best free or publicly available data sources to evaluate
for Market Terminal. Every source must remain honest about coverage, delay,
licensing, key requirements, and practical API limits before it becomes a core
provider.

## Strategy

Prioritize sources in this order:

1. Official public data APIs with clear terms and stable schemas.
2. Free provider APIs with documented free tiers and explicit limits.
3. Unofficial or reverse-engineered sources only when they are clearly labeled
   as best-effort and replaceable.

The goal is not to blend every source into one opaque feed. The goal is to
build a provider layer that can show the best available data, explain why a
source was selected, and expose source quality to the user.

## Current Implemented Sources

| Source | Current Use | Coverage | Status | Caveats |
| --- | --- | --- | --- | --- |
| Yahoo Finance via `yfinance` | Search, chart history, metadata | Equities, ETFs, indices, FX, crypto, funds where Yahoo exposes them | Primary price provider | Unofficial access path; data can be delayed, incomplete, or change without notice. Keep attribution and fallback behavior visible. |
| OpenFIGI | Identifier mapping | FIGI, ISIN/CUSIP/ticker mapping and Open Symbology metadata | Implemented for identifier lookup | Free and public, but rate-limited. No prices. |
| Twelve Data | Optional search/history fallback | Multi-asset market data depending on plan and endpoint | Optional configured provider | Free tier exists but is quota-limited; verify endpoint availability before relying on it. |
| Stooq | Optional historical fallback | Historical EOD data | Optional configured provider | Existing implementation assumes configured API key; use for historical fallback only unless terms/coverage are revalidated. |
| Local `FORT_PNL` files | Portfolio index and monitor | User portfolio data | Implemented | Private/local data; never commit generated files or raw portfolio exports. |

## Priority 0: Robust Provider Framework

These are architecture tasks needed before adding many more feeds.

| Task | Outcome |
| --- | --- |
| Provider registry | Source metadata, key requirements, asset coverage, status, and implementation owner are visible in one place. Initial module: `provider_registry.py`. |
| Provider health checks | App can show whether each configured provider is available, rate-limited, stale, or missing credentials. Initial non-network checks are implemented in `provider_registry.py`. |
| Cache policy | Separate short-lived quote/news cache from longer-lived macro/fundamental cache. |
| Source-quality model | Keep scoring explainable: freshness, completeness, bar count, timestamp regularity, nulls, and provider-specific warnings. |
| Data provenance UI | Every chart/news/analysis panel can show the source and timestamp. |
| Secrets policy | `.env.example` lists optional keys; no key or private local data is committed. |

## Priority 1: Highest-Value Free/Public Integrations

| Source | Best Use | Why It Matters | Requirements | Notes |
| --- | --- | --- | --- | --- |
| SEC EDGAR APIs | US company filings, submissions, XBRL company facts | Core free fundamentals and filing events for US-listed companies | No API key, but requires respectful request headers/rate behavior | Initial client exists in `sec_edgar.py`; next step is selected-ticker UI integration. |
| FRED | US macro, rates, inflation, employment, credit, monetary aggregates | Essential macro dashboard and chart overlays | Free API key | Start with curated series packs instead of exposing the whole catalog immediately. |
| ECB Data Portal | Euro-area rates, money, FX reference, macro statistics | Essential for EUR rates/macro context | No app-specific key expected for SDMX API | Add SDMX client with curated euro dashboard. |
| U.S. Treasury Fiscal Data | Debt, Treasury statements, fiscal datasets | Public debt, deficit, Treasury operations context | No key for public API | Start with public debt and selected daily/monthly fiscal series. |
| World Bank Indicators API | Global country macro/development indicators | Broad international macro context | No key | Good for slower-moving country dashboards, not real-time markets. |
| IMF Data APIs | Global macro, WEO/IFS-style datasets | Global macro and country comparisons | API availability varies by dataset | Use after FRED/ECB/World Bank because SDMX metadata discovery needs careful UX. |
| EIA Open Data API | Energy supply, demand, inventories, prices | Commodity and energy macro dashboard | Free API key may be required for some usage | High value for oil, gas, electricity, and energy-sensitive assets. |
| GDELT DOC API | Global news search and topic monitoring | Free large-scale news monitoring and macro/ticker narrative discovery | No key | Needs filtering/ranking to avoid noisy results. Use as a news-discovery source, not polished financial news by itself. |

## Priority 2: Free-Tier Market Data Candidates

These can be useful, but they need careful validation because free tiers often
limit real-time data, history depth, geography, redistribution, or request
volume.

| Source | Best Use | Requirements | Caveats |
| --- | --- | --- | --- |
| Alpha Vantage | Daily/weekly/monthly prices, fundamentals, FX/crypto, macro, technical indicators | Free API key | Some endpoints are premium or quota-limited. Existing README notes intraday stock access as premium-sensitive; recheck endpoint before implementation. |
| Finnhub | Company news, fundamentals, selected market data | Free API key | Free plan coverage varies by endpoint and geography; stock candles may be premium for target use cases. |
| Nasdaq Data Link | Free open datasets, some central bank/government datasets | API key may be needed | Many valuable datasets are premium; use free/open datasets only unless paid access is explicitly approved. |
| NewsAPI | General live article search | API key | Free developer plan is for development; not a dependable production financial-news backend. |

## Feature-To-Data Map

| Feature | First Data Sources | Follow-Up Sources |
| --- | --- | --- |
| Watchlist | Yahoo/yfinance, OpenFIGI, provider health | Twelve Data, Alpha Vantage, Finnhub |
| Charting | Yahoo/yfinance, Stooq fallback | Alpha Vantage daily, Twelve Data where free tier allows |
| Live selected ticker pricing | Yahoo/yfinance quote metadata first | Finnhub/Twelve Data only after free-tier validation |
| Portfolio analysis | Local portfolio files, Yahoo/yfinance bars | SEC fundamentals, FRED/ECB macro overlays |
| News section | GDELT DOC API, Finnhub company news if available | NewsAPI for development experiments |
| AI quick analysis | Local computed indicators, provider provenance, SEC facts, filings, macro series, news snippets | LLM-backed synthesis after source grounding is reliable |
| Macro dashboard | FRED, ECB, Treasury Fiscal Data, World Bank | IMF, EIA |
| Mobile monitor | Same provider services behind a UI-agnostic layer | Telegram alerts, responsive web, native mobile experiments |

## Suggested Implementation Milestones

### Milestone 1: Provider Registry And Health

- Add provider metadata objects for implemented providers. Done initially in `provider_registry.py`.
- Show configured/missing credentials and source status. Done initially via `provider_health_report()`.
- Add tests for provider status representation. Done initially in `tests/test_provider_registry.py`.
- Keep this UI-light: a diagnostics panel or CLI-like report is enough first.

### Milestone 2: SEC Fundamentals And Filings

- Add SEC client with respectful headers. Done initially in `sec_edgar.py`.
- Implement ticker-to-CIK mapping. Done initially via `SecEdgarClient.lookup_ticker()`.
- Add company facts retrieval for a small curated field set. Done initially via `fundamental_snapshot()`.
- Add recent filings list for selected ticker. Initial chart-header context line is wired in `app.py`; a richer filings panel remains useful.
- Add tests with recorded/minimal fixtures, not live network calls. Done initially in `tests/test_sec_edgar.py`.

### Milestone 3: Macro Data Backbone

- Add FRED client and curated series registry.
- Add ECB SDMX client for key euro-area rates/macro series.
- Add macro dashboard data model independent of Tkinter UI.

### Milestone 4: News Monitoring

- Add GDELT news search client.
- Build ticker/macro query templates.
- Rank/filter noisy articles.
- Feed only sourced snippets and metadata into AI analysis.

### Milestone 5: Mobile-Friendly Monitor

- Extract monitor state and alert workflows from Tkinter assumptions.
- Evaluate Telegram bot/channel alerts versus responsive local web UI.
- Pick one minimum viable phone surface and keep the desktop app intact.

## Source Links

- Yahoo Finance terms: https://legal.yahoo.com/us/en/yahoo/terms/product-atos/finance/index.html
- OpenFIGI API documentation: https://www.openfigi.com/api/documentation
- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- FRED API documentation: https://fred.stlouisfed.org/docs/api/fred/
- ECB Data Portal API: https://data.ecb.europa.eu/help/api/overview
- U.S. Treasury Fiscal Data API: https://fiscaldata.treasury.gov/api-documentation/
- World Bank Indicators API: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392
- IMF DataMapper API: https://www.imf.org/external/datamapper/api/
- EIA API documentation: https://www.eia.gov/opendata/documentation.php
- GDELT DOC API overview: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
- Alpha Vantage documentation: https://www.alphavantage.co/documentation/
- Finnhub API documentation: https://finnhub.io/docs/api
- Nasdaq Data Link documentation: https://docs.data.nasdaq.com/docs/getting-started
- NewsAPI documentation: https://newsapi.org/docs
