from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from time import monotonic
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


class GdeltRateLimitError(RuntimeError):
    pass


class GdeltResponseError(RuntimeError):
    pass


class GdeltNewsClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        cache_ttl_seconds: float = 90.0,
        rate_limit_backoff_seconds: float = 90.0,
    ) -> None:
        self.session = session or requests.Session()
        self.cache_ttl_seconds = cache_ttl_seconds
        self.rate_limit_backoff_seconds = rate_limit_backoff_seconds
        self._cache: dict[tuple[str, str, int], tuple[float, tuple[NewsArticle, ...]]] = {}
        self._rate_limited_until = 0.0

    def search(self, query: NewsQuery) -> tuple[NewsArticle, ...]:
        cache_key = (query.query, query.timespan, query.max_records)
        cached = self._cache.get(cache_key)
        now = monotonic()
        if cached and now - cached[0] < self.cache_ttl_seconds:
            return cached[1]
        if now < self._rate_limited_until:
            raise GdeltRateLimitError(
                "GDELT is rate limiting news requests. Wait a minute, then refresh again."
            )
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
        if response.status_code == 429:
            self._rate_limited_until = now + self.rate_limit_backoff_seconds
            raise GdeltRateLimitError(
                "GDELT is rate limiting news requests. Wait a minute, then refresh again."
            )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise GdeltResponseError(
                "GDELT returned an empty or non-JSON response. Wait briefly and refresh again."
            ) from exc
        if not isinstance(payload, dict):
            raise GdeltResponseError("GDELT returned an unexpected response format.")
        articles = parse_gdelt_articles(payload)
        self._cache[cache_key] = (now, articles)
        return articles


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
