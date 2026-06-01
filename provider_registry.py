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
        features=("search", "history"),
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
    if spec.credential_env and not _has_env_value(spec.credential_env):
        return ProviderHealth(spec, STATUS_MISSING_KEY, f"Set {spec.credential_env} to enable.")
    return ProviderHealth(spec, STATUS_READY, "Configured.")


def _has_env_value(name: str) -> bool:
    return bool(name and os.getenv(name, "").strip())


def _missing_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    return tuple(path for path in paths if not path.exists())
