from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests


GDELT_DOC_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"


@dataclass(frozen=True)
class NewsArticle:
    title: str
    url: str
    source: str = ""
    domain: str = ""
    language: str = ""
    published_at: str = ""
    image_url: str = ""


@dataclass(frozen=True)
class NewsQuery:
    label: str
    query: str
    timespan: str = "6h"
    max_records: int = 30


DEFAULT_NEWS_QUERIES = (
    NewsQuery("Markets", '"stock market" OR equities OR bonds OR yields'),
    NewsQuery("Macro", 'inflation OR "central bank" OR recession OR GDP OR payrolls'),
    NewsQuery("Rates", 'Treasury OR "yield curve" OR "interest rates" OR Fed OR ECB'),
    NewsQuery("Energy", 'oil OR gas OR OPEC OR energy'),
)


class GdeltNewsClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def search(self, query: NewsQuery) -> tuple[NewsArticle, ...]:
        response = self.session.get(
            GDELT_DOC_ENDPOINT,
            params={
                "query": query.query,
                "mode": "ArtList",
                "format": "json",
                "timespan": query.timespan,
                "maxrecords": str(query.max_records),
                "sort": "HybridRel",
            },
            timeout=15,
        )
        response.raise_for_status()
        return parse_gdelt_articles(response.json())


def parse_gdelt_articles(payload: dict[str, Any]) -> tuple[NewsArticle, ...]:
    articles = []
    seen_urls = set()
    for record in payload.get("articles", []):
        if not isinstance(record, dict):
            continue
        url = str(record.get("url", "")).strip()
        title = str(record.get("title", "")).strip()
        if not url or not title or url in seen_urls:
            continue
        seen_urls.add(url)
        articles.append(
            NewsArticle(
                title=title,
                url=url,
                source=str(record.get("sourceCollectionIdentifier", "")).strip(),
                domain=str(record.get("domain", "")).strip(),
                language=str(record.get("language", "")).strip(),
                published_at=_format_gdelt_date(str(record.get("seendate", "")).strip()),
                image_url=str(record.get("socialimage", "")).strip(),
            )
        )
    return tuple(articles)


def default_news_queries() -> tuple[NewsQuery, ...]:
    return DEFAULT_NEWS_QUERIES


def news_query_by_label(label: str) -> NewsQuery:
    key = label.strip().lower()
    for query in DEFAULT_NEWS_QUERIES:
        if query.label.lower() == key:
            return query
    return DEFAULT_NEWS_QUERIES[0]


def _format_gdelt_date(value: str) -> str:
    if not value:
        return ""
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return value
