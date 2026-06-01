from __future__ import annotations

import unittest

from market_terminal.news_feed import (
    GdeltNewsClient,
    NewsQuery,
    default_news_queries,
    news_query_by_label,
    parse_gdelt_articles,
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

    def get(self, url, params, timeout):
        self.request = (url, params, timeout)
        return StubResponse(self.payload)


class NewsFeedTests(unittest.TestCase):
    def test_parses_gdelt_articles_and_deduplicates_urls(self) -> None:
        articles = parse_gdelt_articles(
            {
                "articles": [
                    {
                        "title": "Markets rally",
                        "url": "https://example.com/a",
                        "domain": "example.com",
                        "language": "English",
                        "seendate": "20260601123000",
                    },
                    {"title": "Duplicate", "url": "https://example.com/a"},
                    {"title": "", "url": "https://example.com/empty"},
                ]
            }
        )

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].domain, "example.com")
        self.assertEqual(articles[0].published_at, "2026-06-01 12:30")

    def test_client_requests_article_list_json(self) -> None:
        session = StubSession({"articles": [{"title": "A", "url": "https://example.com/a"}]})
        client = GdeltNewsClient(session=session)

        articles = client.search(NewsQuery("Test", "markets", timespan="1h", max_records=5))

        self.assertEqual(len(articles), 1)
        self.assertEqual(session.request[1]["mode"], "ArtList")
        self.assertEqual(session.request[1]["format"], "json")
        self.assertEqual(session.request[1]["timespan"], "1h")
        self.assertEqual(session.request[1]["maxrecords"], "5")

    def test_default_news_queries_are_selectable_by_label(self) -> None:
        self.assertTrue(default_news_queries())
        self.assertEqual(news_query_by_label("Macro").label, "Macro")
        self.assertEqual(news_query_by_label("Unknown").label, "Markets")


if __name__ == "__main__":
    unittest.main()
