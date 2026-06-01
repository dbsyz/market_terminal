from __future__ import annotations

import unittest

from market_terminal.sec_edgar import (
    DEFAULT_SEC_USER_AGENT,
    SecEdgarClient,
    normalize_cik,
    parse_company_facts,
    parse_company_tickers,
    parse_recent_filings,
)


class StubResponse:
    def __init__(self, payload) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self.payload


class StubSession:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.request = None

    def get(self, url, headers, timeout):
        self.request = (url, headers, timeout)
        return StubResponse(self.payload)


class SecEdgarTests(unittest.TestCase):
    def test_normalizes_cik_to_ten_digits(self) -> None:
        self.assertEqual(normalize_cik("320193"), "0000320193")

    def test_parses_company_ticker_file(self) -> None:
        companies = parse_company_tickers(
            {
                "0": {"cik_str": 320193, "ticker": "aapl", "title": "Apple Inc."},
                "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
            }
        )

        self.assertEqual(companies[0].cik, "0000320193")
        self.assertEqual(companies[0].ticker, "AAPL")
        self.assertEqual(companies[1].title, "Microsoft Corp")

    def test_parses_recent_filings_and_filters_forms(self) -> None:
        filings = parse_recent_filings(
            {
                "cik": "320193",
                "filings": {
                    "recent": {
                        "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002"],
                        "form": ["10-K", "4"],
                        "filingDate": ["2026-01-30", "2026-02-01"],
                        "reportDate": ["2025-12-31", ""],
                        "primaryDocument": ["aapl-20251231.htm", "xslF345X05/doc.xml"],
                        "primaryDocDescription": ["10-K", "FORM 4"],
                    }
                },
            },
            forms=("10-K",),
        )

        self.assertEqual(len(filings), 1)
        self.assertEqual(filings[0].form, "10-K")
        self.assertEqual(
            filings[0].filing_url,
            "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/aapl-20251231.htm",
        )

    def test_parses_company_facts_latest_first(self) -> None:
        snapshot = parse_company_facts(
            {
                "cik": 320193,
                "entityName": "Apple Inc.",
                "facts": {
                    "us-gaap": {
                        "Revenues": {
                            "label": "Revenues",
                            "description": "Revenue from contracts",
                            "units": {
                                "USD": [
                                    {
                                        "val": 100,
                                        "end": "2025-12-31",
                                        "filed": "2026-01-30",
                                        "form": "10-K",
                                        "fy": 2025,
                                        "fp": "FY",
                                    },
                                    {
                                        "val": 90,
                                        "end": "2024-12-31",
                                        "filed": "2025-01-30",
                                        "form": "10-K",
                                    },
                                ]
                            },
                        }
                    }
                },
            }
        )

        self.assertEqual(snapshot.cik, "0000320193")
        self.assertEqual(snapshot.entity_name, "Apple Inc.")
        self.assertEqual(snapshot.facts[0].value, 100)
        self.assertEqual(snapshot.facts[0].fiscal_year, 2025)

    def test_client_sends_declared_user_agent(self) -> None:
        session = StubSession({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}})
        client = SecEdgarClient(session=session, min_request_interval=0)

        company = client.lookup_ticker("AAPL")

        self.assertEqual(company.cik, "0000320193")
        self.assertEqual(session.request[1]["User-Agent"], DEFAULT_SEC_USER_AGENT)
        self.assertEqual(session.request[2], 15)


if __name__ == "__main__":
    unittest.main()
