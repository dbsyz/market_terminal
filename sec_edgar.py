from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests


SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
DEFAULT_SEC_USER_AGENT = "Market Terminal research app contact@example.com"


@dataclass(frozen=True)
class SecCompany:
    cik: str
    ticker: str
    title: str


@dataclass(frozen=True)
class SecFiling:
    cik: str
    accession_number: str
    form: str
    filing_date: str
    report_date: str = ""
    primary_document: str = ""
    description: str = ""

    @property
    def filing_url(self) -> str:
        compact_accession = self.accession_number.replace("-", "")
        return (
            "https://www.sec.gov/Archives/edgar/data/"
            f"{int(self.cik)}/{compact_accession}/{self.primary_document}"
        )


@dataclass(frozen=True)
class SecCompanyFact:
    taxonomy: str
    tag: str
    label: str
    description: str
    unit: str
    value: float | int | str
    end: str
    filed: str
    form: str
    fiscal_year: int | None = None
    fiscal_period: str = ""


@dataclass(frozen=True)
class SecFundamentalSnapshot:
    cik: str
    entity_name: str
    facts: tuple[SecCompanyFact, ...]


class SecEdgarClient:
    def __init__(
        self,
        user_agent: str | None = None,
        session: requests.Session | None = None,
        min_request_interval: float = 0.11,
    ) -> None:
        self.user_agent = (
            user_agent
            or os.getenv("SEC_USER_AGENT", "").strip()
            or DEFAULT_SEC_USER_AGENT
        )
        self.session = session or requests.Session()
        self.min_request_interval = min_request_interval
        self._last_request_at = 0.0

    def lookup_ticker(self, ticker: str) -> SecCompany | None:
        ticker_key = ticker.strip().upper()
        for company in parse_company_tickers(self._get_json(SEC_COMPANY_TICKERS_URL)):
            if company.ticker.upper() == ticker_key:
                return company
        return None

    def recent_filings(
        self,
        cik: str,
        forms: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
        limit: int = 20,
    ) -> tuple[SecFiling, ...]:
        payload = self._get_json(SEC_SUBMISSIONS_URL.format(cik=normalize_cik(cik)))
        return parse_recent_filings(payload, forms=forms, limit=limit)

    def company_facts(self, cik: str) -> SecFundamentalSnapshot:
        payload = self._get_json(SEC_COMPANY_FACTS_URL.format(cik=normalize_cik(cik)))
        return parse_company_facts(payload)

    def fundamental_snapshot(
        self,
        cik: str,
        tags: tuple[str, ...] = (
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "NetIncomeLoss",
            "Assets",
            "Liabilities",
            "StockholdersEquity",
            "EarningsPerShareDiluted",
        ),
    ) -> SecFundamentalSnapshot:
        snapshot = self.company_facts(cik)
        selected = []
        seen = set()
        for fact in snapshot.facts:
            key = fact.tag
            if key in tags and key not in seen:
                selected.append(fact)
                seen.add(key)
        return SecFundamentalSnapshot(snapshot.cik, snapshot.entity_name, tuple(selected))

    def _get_json(self, url: str) -> dict[str, Any]:
        self._respect_rate_limit()
        response = self.session.get(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json",
            },
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self._last_request_at = time.monotonic()


def normalize_cik(cik: str | int) -> str:
    digits = "".join(character for character in str(cik) if character.isdigit())
    if not digits:
        raise ValueError("CIK must contain digits")
    return digits.zfill(10)


def parse_company_tickers(payload: dict[str, Any]) -> tuple[SecCompany, ...]:
    companies = []
    for record in payload.values():
        if not isinstance(record, dict):
            continue
        cik = record.get("cik_str")
        ticker = str(record.get("ticker", "")).strip().upper()
        title = str(record.get("title", "")).strip()
        if cik and ticker:
            companies.append(SecCompany(normalize_cik(cik), ticker, title))
    return tuple(companies)


def parse_recent_filings(
    payload: dict[str, Any],
    forms: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
    limit: int = 20,
) -> tuple[SecFiling, ...]:
    recent = payload.get("filings", {}).get("recent", {})
    cik = normalize_cik(payload.get("cik", ""))
    wanted = {form.upper() for form in forms}
    filings = []
    for index, form in enumerate(recent.get("form", [])):
        form_value = str(form).upper()
        if wanted and form_value not in wanted:
            continue
        accession = _recent_value(recent, "accessionNumber", index)
        if not accession:
            continue
        filings.append(
            SecFiling(
                cik=cik,
                accession_number=accession,
                form=form_value,
                filing_date=_recent_value(recent, "filingDate", index),
                report_date=_recent_value(recent, "reportDate", index),
                primary_document=_recent_value(recent, "primaryDocument", index),
                description=_recent_value(recent, "primaryDocDescription", index),
            )
        )
        if len(filings) >= limit:
            break
    return tuple(filings)


def parse_company_facts(payload: dict[str, Any]) -> SecFundamentalSnapshot:
    facts = []
    cik = normalize_cik(payload.get("cik", ""))
    entity_name = str(payload.get("entityName", "")).strip()
    for taxonomy, taxonomy_facts in payload.get("facts", {}).items():
        if not isinstance(taxonomy_facts, dict):
            continue
        for tag, fact_payload in taxonomy_facts.items():
            facts.extend(_parse_fact_units(str(taxonomy), str(tag), fact_payload))
    facts.sort(key=lambda fact: (fact.filed, fact.end), reverse=True)
    return SecFundamentalSnapshot(cik, entity_name, tuple(facts))


def _parse_fact_units(
    taxonomy: str,
    tag: str,
    fact_payload: dict[str, Any],
) -> tuple[SecCompanyFact, ...]:
    if not isinstance(fact_payload, dict):
        return ()
    label = str(fact_payload.get("label", tag)).strip()
    description = str(fact_payload.get("description", "")).strip()
    parsed = []
    units = fact_payload.get("units", {})
    if not isinstance(units, dict):
        return ()
    for unit, records in units.items():
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict) or "val" not in record:
                continue
            parsed.append(
                SecCompanyFact(
                    taxonomy=taxonomy,
                    tag=tag,
                    label=label,
                    description=description,
                    unit=str(unit),
                    value=record["val"],
                    end=str(record.get("end", "")),
                    filed=str(record.get("filed", "")),
                    form=str(record.get("form", "")),
                    fiscal_year=_optional_int(record.get("fy")),
                    fiscal_period=str(record.get("fp", "")),
                )
            )
    return tuple(parsed)


def _recent_value(recent: dict[str, list[Any]], key: str, index: int) -> str:
    values = recent.get(key, [])
    if index >= len(values):
        return ""
    return str(values[index] or "").strip()


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
