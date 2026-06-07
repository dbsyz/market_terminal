# Data Roadmap

Last reviewed: 2026-06-07

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
| Yahoo Finance via `yfinance` | Search, chart history, metadata, best-effort selected-ticker events | Equities, ETFs, indices, FX, crypto, funds where Yahoo exposes them | Primary price provider; initial event calendar source | Unofficial access path; data can be delayed, incomplete, or change without notice. Event calendar shapes vary by ticker and may only provide dates, not exact local times. Keep attribution and fallback behavior visible. |
| nfin Nasdaq API | Best-effort selected-ticker event calendar enrichment | US-listed equities/ETFs where Nasdaq calendar rows expose the symbol | Implemented as no-key enrichment for earnings, dividends, splits, and IPO rows | Anonymous access is IP-rate-limited; Nasdaq payload schemas can vary by route. Use attribution per event and keep Yahoo/SEC fallbacks. |
| Binance Spot public API | Crypto chart history and quotes | Binance-listed spot crypto pairs | Implemented as preferred crypto source | Public market data requires no key, but coverage is exchange-specific and access may be geographically restricted. Yahoo remains fallback for unsupported pairs. |
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
| FRED | US macro, rates, inflation, employment, credit, monetary aggregates | Essential macro dashboard and chart overlays | Public CSV fallback works without a key; free API key enables the official JSON API | Initial curated-series client exists in `fred_macro.py`; next step is macro dashboard/UI integration. |
| ECB Data Portal | Euro-area rates, money, FX reference, macro statistics | Essential for EUR rates/macro context | No app-specific key expected for SDMX API | Add SDMX client with curated euro dashboard. |
| U.S. Treasury Fiscal Data | Debt, Treasury statements, fiscal datasets | Public debt, deficit, Treasury operations context | No key for public API | Start with public debt and selected daily/monthly fiscal series. |
| World Bank Indicators API | Global country macro/development indicators | Broad international macro context | No key | Good for slower-moving country dashboards, not real-time markets. |
| IMF Data APIs | Global macro, WEO/IFS-style datasets | Global macro and country comparisons | API availability varies by dataset | Use after FRED/ECB/World Bank because SDMX metadata discovery needs careful UX. |
| EIA Open Data API | Energy supply, demand, inventories, prices | Commodity and energy macro dashboard | Free API key may be required for some usage | High value for oil, gas, electricity, and energy-sensitive assets. |
| GDELT DOC API | Global news search and topic monitoring | Free large-scale news monitoring and macro/ticker narrative discovery | No key | Initial live news client/window exists via `news_feed.py` and `app.py`; ranking/noise filtering remains important. |

## Priority 2: Free-Tier Market Data Candidates

These can be useful, but they need careful validation because free tiers often
limit real-time data, history depth, geography, redistribution, or request
volume.

| Source | Best Use | Requirements | Caveats |
| --- | --- | --- | --- |
| Alpha Vantage | Daily/weekly/monthly prices, fundamentals, FX/crypto, macro, technical indicators | Free API key | Some endpoints are premium or quota-limited. Existing README notes intraday stock access as premium-sensitive; recheck endpoint before implementation. |
| Finnhub | Company news, fundamentals, selected market data | Free API key | Free plan coverage varies by endpoint and geography; stock candles may be premium for target use cases. |
| Financial Modeling Prep | Equity calendar APIs, fundamentals, news/press releases, corporate actions | Free API key | Broad docs and useful event surface, but free-tier coverage/limits and redistribution terms need endpoint-by-endpoint validation. |
| EODHD | EOD prices, corporate actions, earnings/trends/calendar endpoints | Free API token/starter access | Free starter is very low quota and personal-use oriented; useful as a validation/cross-check provider, not a default free backend. |
| StockData.org | US stock quotes, market/news feeds; possible paid corporate-action add-on | Free API key for all-plan endpoints | Docs show quote/news endpoints on all plans, but stock splits and dividends are documented as Standard-plan-and-above endpoints. Not a first-choice free event-calendar source. |
| Marketstack | End-of-day history and dividend/split fields | Free API key | Free tier is EOD-oriented and quota-limited. Useful as a low-priority historical/corporate-action candidate, not a broad earnings/events source. |
| Nasdaq Data Link | Free open datasets, some central bank/government datasets | API key may be needed | Many valuable datasets are premium; use free/open datasets only unless paid access is explicitly approved. |
| NewsAPI | General live article search | API key | Free developer plan is for development; not a dependable production financial-news backend. |

## Event Calendar Candidates

| Source | Best Use | Requirements | Caveats |
| --- | --- | --- | --- |
| Yahoo Finance via `yfinance` | First selected-ticker earnings/dividend/split event surface | No key | Implemented as best-effort. Unofficial, incomplete, and may return date-only events or no rows for supported tickers. |
| nfin Nasdaq API | No-key Nasdaq-backed earnings, dividends, splits, IPO calendar endpoints | No key for anonymous use; optional API key for higher limits | Implemented as best-effort enrichment. Docs advertise free anonymous access and clear rate limits. Keep schema handling defensive and attribution visible. |
| NASDAQ public calendar endpoints / `finance_calendars` wrapper | US earnings calendar, IPO calendar, dividend dates, split history | No API key indicated by wrapper; dependency or direct endpoint adapter needed | Good next validation target for US stocks. Wrapper is MIT-licensed but relies on public NASDAQ endpoints, so treat access as unofficial/reverse-engineered and label as best-effort. Prefer a small direct adapter or optional dependency after testing endpoint stability. |
| Alpha Vantage `EARNINGS_CALENDAR` | Upcoming earnings dates in CSV form | Free API key | Officially documented endpoint with horizons such as 3-month; rate limits apply and exact time-of-day may be limited. |
| Finnhub earnings calendar | Earnings date plus before/after-market style timing where available | Free API key | Documented earnings calendar endpoint includes fields such as date/hour and estimates, but free-tier availability and terms need validation before enabling by default. |
| Financial Modeling Prep calendars | Earnings calendar, confirmed earnings, IPO calendar, dividends, splits, press releases | Free API key | Very broad event surface, likely useful for enrichment. Validate exact free-plan endpoint access and limits before defaulting to it. |
| EODHD calendar/corporate actions | Earnings trends/upcoming earnings, IPOs, dividends, splits | API token; free starter has very low quota | Good as a keyed fallback or test comparator. Not ideal as a default source because free quota is tight. |
| SEC EDGAR submissions | Actual filing events for US-listed SEC registrants | No key, SEC-compliant user agent/rate behavior | Not an earnings-calendar forecast. Best for actual 8-K, 10-Q, 10-K, proxy, insider, and corporate filing events after they occur. |
| StockData.org splits/dividends | Historical split/dividend lookup | API key; Standard plan or above per docs | Do not treat as free/public event source unless free-plan access is confirmed. No earnings calendar found in current docs. |
| Marketstack dividends/splits | Dividend endpoint and split-factor/history fields | API key; free-plan endpoint availability and quota need validation | Low priority for event calendar because it does not appear to provide earnings/IPO/company calendar events. Could be useful as a corporate-action cross-check. |
| `exchange_calendars` or `pandas_market_calendars` | Exchange holidays, regular sessions, early closes, late opens | Python dependency, no market-data API key | Not company-specific. Use to annotate event/calendar views with trading-session context and to improve market-open/closed logic. |
| Exchange or issuer investor-relations calendars | Official company events, calls, dividends, AGMs | Usually no key, often no stable API | High provenance but fragmented and scraping-heavy; use only source-specific adapters with clear attribution. |

Recommended event-calendar implementation order:

1. Keep Yahoo/yfinance as the current best-effort baseline.
2. Keep strengthening nfin coverage with recorded fixtures for earnings/dividends/splits/IPOs and visible source attribution. Initial adapter is implemented.
3. Validate direct NASDAQ public endpoints or the `finance_calendars` wrapper as an alternate no-key path if nfin schema/terms become unacceptable.
4. Add exchange-session calendars for market holidays and early closes; keep them visually distinct from company events.
5. Add optional keyed providers only after confirming free-tier endpoint access: Finnhub and Financial Modeling Prep first, then Alpha Vantage for broad upcoming earnings CSV.
6. Treat EODHD, StockData.org, and Marketstack as lower-priority corporate-action cross-checks unless their free plans are proven to cover the needed endpoints.

## Feature-To-Data Map

| Feature | First Data Sources | Follow-Up Sources |
| --- | --- | --- |
| Watchlist | Binance for mapped crypto, Yahoo/yfinance, OpenFIGI, provider health | Twelve Data, Alpha Vantage, Finnhub |
| Charting | Binance for mapped crypto, Yahoo/yfinance, Stooq fallback | Alpha Vantage daily, Twelve Data where free tier allows |
| Live selected ticker pricing | Crypto exchange WebSockets, nfin/Nasdaq REST quotes for US-listed names, Yahoo/yfinance quote metadata fallback | Alpaca IEX, Finnhub, Twelve Data after credential/free-tier validation |
| Portfolio analysis | Local portfolio files, Yahoo/yfinance bars | SEC fundamentals, FRED/ECB macro overlays |
| News section | GDELT DOC API via `news_feed.py`, Finnhub company news if available | NewsAPI for development experiments |
| Event calendar | Yahoo/yfinance event calendar, SEC filings for actual filing events | nfin/NASDAQ public calendars, exchange-session calendars, Finnhub/FMP/Alpha Vantage after free-tier validation |
| AI quick analysis | Local computed indicators, provider provenance, SEC facts, filings, macro series, news snippets | LLM-backed synthesis after source grounding is reliable |
| Macro dashboard | FRED, ECB, Treasury Fiscal Data, World Bank | IMF, EIA |
| Mobile monitor | Same provider services behind a UI-agnostic layer | Telegram alerts, responsive web, native mobile experiments |

## Live Ticking Price Candidates

Use Yahoo/yfinance for historical bars and delayed/best-effort fallback quotes. Use
separate live quote providers for ticking prices because real-time market data has
different licensing, latency, and coverage constraints.

| Asset class | Best free/public first source | Coverage | Access | Caveats |
| --- | --- | --- | --- | --- |
| Crypto spot | Kraken WebSocket v2 ticker BBO and Binance Spot WebSocket `bookTicker`/trade streams | Exchange-listed crypto pairs | No key for public market data | Best true free live-tick coverage. Exchange-specific, not consolidated. Existing `mm_core` already has a Kraken BBO collector/adapter and latency QA; reuse its parsing/reconnect patterns. Binance is already used in Market Terminal for crypto bars/quotes and should be extended to WebSocket ticks for Binance-listed pairs. |
| Crypto broad fallback | Coinbase Exchange public WebSocket ticker/ticker_batch | Coinbase-listed crypto pairs | No key for public market data | Useful fallback for USD pairs and US-accessible venue coverage. Still exchange-specific. |
| US equities/ETFs | nfin Nasdaq API batch/current quote routes | Nasdaq-sourced quote/summary data for many US-listed symbols | Anonymous no-key limits; optional key for higher limits | Good no-setup REST quote candidate for desktop watchlist refresh, but not a streaming tick feed. Treat source as Nasdaq/nfin and show freshness. |
| US equities/ETFs streaming | Alpaca Basic IEX WebSocket, Finnhub quote/WebSocket | US-listed names | Free account/API key | Not consolidated SIP. Alpaca Basic is real-time IEX only, limited symbol subscriptions; Finnhub provides real-time US stock quotes and WebSocket but one connection per key and free-tier limits need validation. Good optional sources when the user configures keys. |
| US options | Alpaca Basic indicative options feed | US options | Free account/API key | Indicative only, not OPRA real-time. Use only with explicit labeling. |
| International equities/ETFs/funds | Twelve Data REST/WebSocket trial/free access where plan allows | Broad global symbols, FX, crypto, commodities | Free API key; credits and WebSocket trial limits | Best broad multi-asset candidate, but free credits are limited and plan availability varies by symbol/market. Use as optional keyed fallback, not default. |
| FX | OANDA practice/live account pricing stream, Twelve Data, Finnhub | Major FX pairs | Account/API key | OANDA is broker-pricing, not consolidated interbank; regional API availability varies. Twelve Data free/trial can cover FX but credits apply. Finnhub streaming has broker exclusions for some FX feeds. |
| Indices | nfin/Nasdaq index quote routes, Alpaca/Finnhub where supported, Yahoo fallback | US index snapshots and proxies | No-key/keyed depending on source | True exchange-level real-time index data is licensing-sensitive. Prefer live ETF proxies such as SPY/QQQ for ticking dashboards when exact index tick is unavailable. |
| Commodities/rates | Exchange-listed ETFs/futures proxies via equity/crypto/FX providers | GLD, SLV, USO, TLT, futures ETFs; limited spot proxies | Same as equity/ETF source | Free true real-time futures/commodity feeds are generally not available. Use liquid ETF proxies for live monitoring and official daily sources for macro/rates context. |
| Mutual funds | No strong live-tick source | NAV-based products | Yahoo/Twelve Data snapshot only | Mutual funds do not tick intraday like stocks. Use latest NAV/previous close and label as stale/NAV. |

Recommended live-price implementation order:

1. Build a `live_quotes.py` service with a normalized `LiveQuote` model and source/freshness metadata.
2. Reuse `mm_core` Kraken WebSocket v2 ticker BBO adapter for Kraken-listed crypto, especially BTC/EUR.
3. Add Binance Spot WebSocket ticks for Binance-listed crypto pairs already mapped in Market Terminal.
4. Add nfin REST batch quote refresh for US-listed watchlist/selected-ticker quotes where WebSocket is not configured.
5. Add optional keyed Alpaca IEX and Finnhub adapters for US equities/ETFs streaming, clearly labeled as IEX-only or provider-limited.
6. Add optional Twelve Data for broad international/FX coverage after validating free credit behavior and symbol entitlements.
7. Keep Yahoo/yfinance as fallback and provenance-labeled backup, not the primary live ticker source.

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
- Add recent filings list for selected ticker. Initial chart-header context line and SEC details popup are wired in `app.py`.
- Add persistent cache policy. SEC JSON cache writes to ignored `out/sec_cache`, keeps an in-session memory cache, displays cache age, and can be cleared from the SEC details popup.
- Add tests with recorded/minimal fixtures, not live network calls. Done initially in `tests/test_sec_edgar.py`.

### Milestone 3: Macro Data Backbone

- Add FRED client and curated series registry. Done initially in `fred_macro.py`.
- Add ECB SDMX client for key euro-area rates/macro series.
- Add macro dashboard data model independent of Tkinter UI. Done initially in `macro_dashboard.py`.

### Milestone 4: News Monitoring

- Add GDELT news search client. Done initially in `news_feed.py`.
- Build ticker/macro query templates.
- Rank/filter noisy articles.
- Feed only sourced snippets and metadata into AI analysis.

### Milestone 5: Mobile-Friendly Monitor

- Extract monitor state and alert workflows from Tkinter assumptions.
- Evaluate Telegram bot/channel alerts versus responsive local web UI.
- Pick one minimum viable phone surface and keep the desktop app intact.

## Source Links

- Yahoo Finance terms: https://legal.yahoo.com/us/en/yahoo/terms/product-atos/finance/index.html
- Binance Spot API documentation: https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md
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
- nfin Nasdaq API: https://nfin.dev/
- Financial Modeling Prep API documentation: https://site.financialmodelingprep.com/developer/docs
- EODHD API documentation: https://eodhd.com/financial-apis/
- finance_calendars NASDAQ wrapper: https://github.com/s-kerin/finance_calendars
- StockData.org documentation: https://www.stockdata.org/documentation
- Marketstack documentation: https://docs.apilayer.com/marketstack/docs/api-endpoints-v1/
- exchange_calendars package: https://pypi.org/project/exchange-calendars/
- pandas_market_calendars package: https://pypi.org/project/pandas_market_calendars/
- Nasdaq Data Link documentation: https://docs.data.nasdaq.com/docs/getting-started
- NewsAPI documentation: https://newsapi.org/docs
