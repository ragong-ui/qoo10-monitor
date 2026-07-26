"""
Brave Search News API collector — JP/KR compliance & regulation news.

Searches multiple query groups against the Brave News API (freshness=pd, country=jp)
and returns deduplicated raw_item dicts.
"""

import logging
import time

from ..collector_base import BaseCollector, CollectorError
from ..filter import is_blocked_url

log = logging.getLogger(__name__)

_BRAVE_NEWS_URL = "https://api.search.brave.com/res/v1/news/search"

# Each group: (group_label, category, freshness, list_of_queries)
# freshness: "pd" = past day, "pw" = past week, "pm" = past month
_QUERY_GROUPS: list[tuple[str, str, str, list[str]]] = [
    (
        "regulation",
        "regulation",
        "pd",  # regulatory actions are time-sensitive
        [
            "Qoo10 規制",
            "越境EC 規制 日本",
            "電子商取引法 改正",
            "景品表示法 措置命令",
            "消費者庁 措置命令",
            "消費者庁 行政処分",
        ],
    ),
    (
        "safety_jp",
        "safety",
        "pd",  # product safety recalls need immediate detection
        [
            "製品リコール 日本",
            "消費者庁 回収命令",
            "消費者庁 重大製品事故",
            "NITE 製品事故",
        ],
    ),
    (
        "safety_kr",
        "safety",
        "pw",  # KR safety news in JP context is slower-moving
        [
            "안전 리콜 한국제품 일본",
            "한국 소비자원 일본",
        ],
    ),
    (
        "competitor",
        "competitor",
        "pw",  # competitor news is less time-critical
        [
            "Qoo10 行政処分",
            "Rakuten 規制",
            "Amazon JP 規制",
        ],
    ),
]

# Keywords used to detect which marketplace an article relates to
_MARKETPLACE_KEYWORDS = ("Qoo10", "Rakuten", "Amazon")


def _detect_marketplace(text: str) -> str | None:
    for kw in _MARKETPLACE_KEYWORDS:
        if kw.lower() in text.lower():
            return kw
    return None


class BraveSearchCollector(BaseCollector):
    source_id = "brave_news"

    def _fetch_live(self) -> list[dict]:
        if not self.cfg.brave_api_key:
            raise CollectorError(
                "BRAVE_SEARCH_API_KEY is not set — cannot run BraveSearchCollector. "
                "Set it in your .env file or environment."
            )

        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.cfg.brave_api_key,
        }

        seen_urls: set[str] = set()
        items: list[dict] = []
        request_count = 0
        failed_requests = 0

        for group_label, category, freshness, queries in _QUERY_GROUPS:
            for query in queries:
                request_count += 1
                log.debug("[%s] Querying: %r  group=%s freshness=%s",
                          self.source_id, query, group_label, freshness)

                params = {
                    "q": query,
                    "count": self.cfg.brave_results_per_query,
                    "country": "jp",
                    "lang": "ja",
                    "freshness": freshness,
                }

                try:
                    resp = self._get(
                        _BRAVE_NEWS_URL,
                        params=params,
                        headers=headers,
                        timeout=20,
                    )
                    data = resp.json()
                except Exception as exc:
                    failed_requests += 1
                    self._record_partial_error(f"Query {query!r} failed: {exc}")
                    log.warning(
                        "[%s] Query %r failed (%s) — skipping",
                        self.source_id,
                        query,
                        exc,
                    )
                    time.sleep(0.5)
                    continue

                results: list[dict] = data.get("results", [])
                log.debug(
                    "[%s] Query %r → %d results", self.source_id, query, len(results)
                )

                for article in results:
                    url = (article.get("url") or "").strip()
                    if not url or url in seen_urls:
                        continue
                    if is_blocked_url(url):
                        log.debug("[%s] blocked: %s", self.source_id, url)
                        continue
                    seen_urls.add(url)

                    title = (article.get("title") or "").strip()
                    body = (article.get("description") or "").strip()

                    # Brave returns page_age as an ISO-8601 timestamp when available,
                    # and age as a human-readable relative string ("2 hours ago").
                    # Prefer the structured page_age field.
                    page_age = article.get("page_age")
                    age = article.get("age")
                    published_at: str | None = (
                        str(page_age) if page_age else (str(age) if age else None)
                    )

                    marketplace = _detect_marketplace(f"{query} {title}")

                    items.append(
                        self._raw_item(
                            source_id=self.source_id,
                            external_id=url,
                            url=url,
                            title=title,
                            body=body,
                            category=category,
                            country="JP",
                            published_at=published_at,
                            marketplace=marketplace,
                            extra={
                                "query": query,
                                "query_group": group_label,
                            },
                        )
                    )

                # Respect Brave rate limits — 1 req/s on free tier
                time.sleep(0.5)

        if request_count and failed_requests == request_count:
            raise CollectorError("All Brave Search queries failed")

        log.info(
            "[%s] Finished: %d unique items across %d query groups",
            self.source_id,
            len(items),
            len(_QUERY_GROUPS),
        )
        return items
