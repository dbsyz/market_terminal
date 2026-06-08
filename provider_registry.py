from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .portfolio_index import portfolio_index_files


STATUS_READY = "ready"
STATUS_LIMITED = "limited"
STATUS_MISSING_KEY = "missing_key"
STATUS_MISSING_FILES = "missing_files"
STATUS_PLANNED = "planned"


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    name: str
    features: tuple[str, ...]
    asset_classes: tuple[str, ...]
    docs_url: str
    implemented: bool
    credential_env: str = ""
    notes: str = ""


@dataclass(frozen=True)
class ProviderHealth:
    spec: ProviderSpec
    status: str
    detail: str

    @property
    def configured(self) -> bool:
        return self.status in {STATUS_READY, STATUS_LIMITED}


IMPLEMENTED_PROVIDER_SPECS = (
    ProviderSpec(
        provider_id="yahoo",
        name="Yahoo Finance via yfinance",
        features=("search", "history", "metadata", "quotes"),
        asset_classes=("equities", "etfs", "funds", "indices", "fx", "crypto"),
        docs_url="https://ranaroussi.github.io/yfinance/",
        implemented=True,
        notes="Primary best-effort market data provider; data can be delayed or incomplete.",
    ),
    ProviderSpec(
        provider_id="binance",
        name="Binance Spot public API",
        features=("crypto_history", "crypto_quotes", "live_crypto_quotes"),
        asset_classes=("crypto",),
        docs_url="https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md",
        implemented=True,
        notes="Preferred source for Binance-listed spot crypto pairs; no key for public market data, but access can be geographically restricted.",
    ),
    ProviderSpec(
        provider_id="kraken",
        name="Kraken public market data",
        features=("live_crypto_quotes",),
        asset_classes=("crypto",),
        docs_url="https://docs.kraken.com/api/docs/rest-api/get-ticker-information/",
        implemented=True,
        notes="No-key crypto live snapshot fallback for Kraken-listed pairs; exchange-specific rather than consolidated crypto market data.",
    ),
    ProviderSpec(
        provider_id="coinbase",
        name="Coinbase Exchange public market data",
        features=("live_crypto_quotes",),
        asset_classes=("crypto",),
        docs_url="https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductticker",
        implemented=True,
        notes="No-key crypto live snapshot fallback for major Coinbase products; exchange-specific rather than consolidated crypto market data.",
    ),
    ProviderSpec(
        provider_id="openfigi",
        name="OpenFIGI",
        features=("identifier_mapping", "symbology"),
        asset_classes=("equities", "funds", "indices", "derivatives", "fixed_income"),
        docs_url="https://www.openfigi.com/api/documentation",
        implemented=True,
        credential_env="OPENFIGI_API_KEY",
        notes="Works without a key at lower rate limits; no price data.",
    ),
    ProviderSpec(
        provider_id="twelve_data",
        name="Twelve Data",
        features=("search", "history", "live_quotes"),
        asset_classes=("equities", "etfs", "indices", "fx", "crypto"),
        docs_url="https://twelvedata.com/docs",
        implemented=True,
        credential_env="TWELVE_DATA_API_KEY",
        notes="Optional fallback provider; free tier is quota-limited.",
    ),
    ProviderSpec(
        provider_id="stooq",
        name="Stooq",
        features=("historical_eod",),
        asset_classes=("equities", "etfs", "indices"),
        docs_url="https://stooq.com/db/h/",
        implemented=True,
        credential_env="STOOQ_API_KEY",
        notes="Optional end-of-day historical fallback only.",
    ),
    ProviderSpec(
        provider_id="fort_pnl",
        name="FORT_PNL local portfolio files",
        features=("portfolio_index", "portfolio_monitor"),
        asset_classes=("portfolio",),
        docs_url="",
        implemented=True,
        notes="Local private portfolio files; never commit source data.",
    ),
    ProviderSpec(
        provider_id="sec_edgar",
        name="SEC EDGAR APIs",
        features=("filings", "fundamentals", "company_facts"),
        asset_classes=("us_equities",),
        docs_url="https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
        implemented=True,
        credential_env="SEC_USER_AGENT",
        notes="Public SEC filings and XBRL facts; requires declared User-Agent and fair-access behavior.",
    ),
    ProviderSpec(
        provider_id="fred",
        name="FRED",
        features=("macro", "rates", "economic_indicators"),
        asset_classes=("macro", "rates"),
        docs_url="https://fred.stlouisfed.org/docs/api/fred/",
        implemented=True,
        credential_env="FRED_API_KEY",
        notes="Curated US macro/rates client foundation; public CSV fallback works without a key.",
    ),
    ProviderSpec(
        provider_id="gdelt",
        name="GDELT DOC API",
        features=("news_search", "topic_monitoring"),
        asset_classes=("news", "macro"),
        docs_url="https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/",
        implemented=True,
        notes="Free global news discovery source; requires ranking/noise filtering for finance workflows.",
    ),
    ProviderSpec(
        provider_id="nfin",
        name="nfin Nasdaq API",
        features=("earnings_calendar", "ipo_calendar", "dividends", "splits", "live_us_quotes"),
        asset_classes=("us_equities",),
        docs_url="https://nfin.dev/",
        implemented=True,
        credential_env="NFIN_API_KEY",
        notes="No-key Nasdaq calendar and US quote enrichment source; anonymous access is usable but IP-rate-limited.",
    ),
    ProviderSpec(
        provider_id="alpaca_iex",
        name="Alpaca Basic IEX market data",
        features=("live_us_quotes",),
        asset_classes=("us_equities", "etfs"),
        docs_url="https://docs.alpaca.markets/docs/real-time-stock-pricing-data",
        implemented=True,
        credential_env="ALPACA_API_KEY_ID",
        notes="Optional free-account IEX quote/trade source. Not consolidated SIP; label as IEX-only.",
    ),
    ProviderSpec(
        provider_id="finnhub",
        name="Finnhub",
        features=("live_quotes",),
        asset_classes=("equities", "etfs", "fx", "crypto"),
        docs_url="https://finnhub.io/docs/api/quote",
        implemented=True,
        credential_env="FINNHUB_API_KEY",
        notes="Optional free-key quote source; free-tier limits and symbol coverage need monitoring.",
    ),
)


PLANNED_PROVIDER_SPECS = (
    ProviderSpec(
        provider_id="ecb",
        name="ECB Data Portal",
        features=("macro", "rates", "fx_reference"),
        asset_classes=("macro", "rates", "fx"),
        docs_url="https://data.ecb.europa.eu/help/api/overview",
        implemented=False,
        notes="Planned euro-area SDMX macro/rates integration.",
    ),
    ProviderSpec(
        provider_id="nasdaq_calendar",
        name="NASDAQ public calendars",
        features=("earnings_calendar", "ipo_calendar", "dividends", "splits"),
        asset_classes=("us_equities",),
        docs_url="https://github.com/s-kerin/finance_calendars",
        implemented=False,
        notes="Candidate event-calendar source via public NASDAQ endpoints/wrappers; reverse-engineered access must stay best-effort and clearly attributed.",
    ),
    ProviderSpec(
        provider_id="fmp",
        name="Financial Modeling Prep",
        features=("earnings_calendar", "ipo_calendar", "dividends", "splits", "press_releases"),
        asset_classes=("equities", "news"),
        docs_url="https://site.financialmodelingprep.com/developer/docs",
        implemented=False,
        credential_env="FMP_API_KEY",
        notes="Broad calendar API surface; free tier and redistribution limits need validation before production use.",
    ),
    ProviderSpec(
        provider_id="eodhd",
        name="EODHD",
        features=("earnings_calendar", "ipo_calendar", "dividends", "splits", "eod_history"),
        asset_classes=("equities",),
        docs_url="https://eodhd.com/financial-apis/",
        implemented=False,
        credential_env="EODHD_API_KEY",
        notes="Calendar endpoints and free starter exist, but the free plan is very low quota and personal-use oriented.",
    ),
    ProviderSpec(
        provider_id="stockdata",
        name="StockData.org",
        features=("stock_quotes", "news", "dividends", "splits"),
        asset_classes=("us_equities", "news"),
        docs_url="https://www.stockdata.org/documentation",
        implemented=False,
        credential_env="STOCKDATA_API_KEY",
        notes="Candidate for quotes/news; dividends and splits are documented as Standard-plan endpoints, so not a first-choice free event source.",
    ),
    ProviderSpec(
        provider_id="marketstack",
        name="Marketstack",
        features=("eod_history", "dividends", "splits"),
        asset_classes=("equities",),
        docs_url="https://docs.apilayer.com/marketstack/docs/api-endpoints-v1/",
        implemented=False,
        credential_env="MARKETSTACK_API_KEY",
        notes="Free EOD-oriented API candidate; event-calendar value appears limited to dividend/split fields and quotas need validation.",
    ),
    ProviderSpec(
        provider_id="exchange_calendars",
        name="exchange_calendars / pandas_market_calendars",
        features=("market_holidays", "early_closes", "trading_sessions"),
        asset_classes=("exchange_schedules",),
        docs_url="https://pypi.org/project/exchange-calendars/",
        implemented=False,
        notes="Candidate for market holidays and session schedules, not company-specific earnings/dividend events.",
    ),
)


def provider_specs(include_planned: bool = True) -> tuple[ProviderSpec, ...]:
    if include_planned:
        return IMPLEMENTED_PROVIDER_SPECS + PLANNED_PROVIDER_SPECS
    return IMPLEMENTED_PROVIDER_SPECS


def provider_health_report(include_planned: bool = True) -> tuple[ProviderHealth, ...]:
    return tuple(_provider_health(spec) for spec in provider_specs(include_planned))


def provider_health_summary(include_planned: bool = True) -> str:
    health = provider_health_report(include_planned)
    return "\n".join(
        f"{item.spec.name}: {item.status} - {item.detail}"
        for item in health
    )


def _provider_health(spec: ProviderSpec) -> ProviderHealth:
    if not spec.implemented:
        return ProviderHealth(spec, STATUS_PLANNED, "Listed in DATA_ROADMAP.md; not implemented yet.")
    if spec.provider_id == "yahoo":
        return ProviderHealth(spec, STATUS_READY, "No project API key required.")
    if spec.provider_id == "binance":
        if os.getenv("BINANCE_DISABLE", "0") == "1":
            return ProviderHealth(spec, STATUS_LIMITED, "Disabled by BINANCE_DISABLE=1.")
        return ProviderHealth(spec, STATUS_READY, "No project API key required for public spot market data.")
    if spec.provider_id == "kraken":
        if os.getenv("KRAKEN_DISABLE", "0") == "1":
            return ProviderHealth(spec, STATUS_LIMITED, "Disabled by KRAKEN_DISABLE=1.")
        return ProviderHealth(spec, STATUS_READY, "No project API key required for public spot market data.")
    if spec.provider_id == "coinbase":
        if os.getenv("COINBASE_DISABLE", "0") == "1":
            return ProviderHealth(spec, STATUS_LIMITED, "Disabled by COINBASE_DISABLE=1.")
        return ProviderHealth(spec, STATUS_READY, "No project API key required for public exchange market data.")
    if spec.provider_id == "openfigi":
        if _has_env_value(spec.credential_env):
            return ProviderHealth(spec, STATUS_READY, f"{spec.credential_env} is configured.")
        return ProviderHealth(
            spec,
            STATUS_LIMITED,
            f"No {spec.credential_env}; unauthenticated API access has lower limits.",
        )
    if spec.provider_id == "fort_pnl":
        files = portfolio_index_files()
        missing = _missing_paths((files.constituents, files.summary, files.levels))
        if missing:
            detail = "Missing local files: " + ", ".join(path.name for path in missing)
            return ProviderHealth(spec, STATUS_MISSING_FILES, detail)
        return ProviderHealth(spec, STATUS_READY, "Required local portfolio files are present.")
    if spec.provider_id == "sec_edgar":
        if _has_env_value(spec.credential_env):
            return ProviderHealth(spec, STATUS_READY, f"{spec.credential_env} is configured.")
        return ProviderHealth(
            spec,
            STATUS_LIMITED,
            "Default SEC User-Agent is active; set SEC_USER_AGENT for production use.",
        )
    if spec.provider_id == "fred":
        if _has_env_value(spec.credential_env):
            return ProviderHealth(spec, STATUS_READY, f"{spec.credential_env} is configured.")
        return ProviderHealth(
            spec,
            STATUS_LIMITED,
            "Using public CSV fallback; set FRED_API_KEY for the official JSON API.",
        )
    if spec.provider_id == "nfin":
        if os.getenv("NFIN_DISABLE", "0") == "1":
            return ProviderHealth(spec, STATUS_LIMITED, "Disabled by NFIN_DISABLE=1.")
        if _has_env_value(spec.credential_env):
            return ProviderHealth(spec, STATUS_READY, f"{spec.credential_env} is configured.")
        return ProviderHealth(
            spec,
            STATUS_LIMITED,
            "Anonymous no-key access is active; set NFIN_API_KEY for higher limits.",
        )
    if spec.provider_id == "alpaca_iex":
        if _has_env_value("ALPACA_API_KEY_ID") and _has_env_value("ALPACA_API_SECRET_KEY"):
            return ProviderHealth(spec, STATUS_READY, "Alpaca Basic IEX credentials are configured.")
        return ProviderHealth(
            spec,
            STATUS_MISSING_KEY,
            "Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY to enable.",
        )
    if spec.credential_env and not _has_env_value(spec.credential_env):
        return ProviderHealth(spec, STATUS_MISSING_KEY, f"Set {spec.credential_env} to enable.")
    return ProviderHealth(spec, STATUS_READY, "Configured.")


def _has_env_value(name: str) -> bool:
    return bool(name and os.getenv(name, "").strip())


def _missing_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    return tuple(path for path in paths if not path.exists())
