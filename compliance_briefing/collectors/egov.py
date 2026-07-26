"""
e-Gov Japan RSS feed collector — legislative updates and regulatory notices.

Endpoint: https://www.e-gov.go.jp/rss/news.rss
Library:  feedparser (required)

Only entries whose title contains at least one of the compliance keywords
are returned; all others are silently dropped.
"""

import logging

from ..collector_base import BaseCollector, CollectorError

log = logging.getLogger(__name__)

try:
    import feedparser as _feedparser  # type: ignore[import]
    _FEEDPARSER_AVAILABLE = True
except ImportError:
    _FEEDPARSER_AVAILABLE = False

_EGOV_RSS_URL = "https://www.e-gov.go.jp/news/news.xml"

# Title must contain at least one of these to be included
_FILTER_KEYWORDS: tuple[str, ...] = (
    "Qoo10",
    "越境",
    "電子商取引",
    "通信販売",
    "景品表示",
    "特定商取引",
    "個人情報",
    "消費者",
    "製品安全",
    "回収",
    "輸入",
)


def _title_matches(title: str) -> bool:
    return any(kw in title for kw in _FILTER_KEYWORDS)


class EGovCollector(BaseCollector):
    source_id = "egov"

    def _fetch_live(self) -> list[dict]:
        if not _FEEDPARSER_AVAILABLE:
            raise CollectorError(
                "feedparser is not installed — cannot run EGovCollector. "
                "Run: pip install feedparser"
            )

        log.debug("[%s] Fetching RSS: %s", self.source_id, _EGOV_RSS_URL)
        resp = self._get(_EGOV_RSS_URL, timeout=30)

        # feedparser can parse a string of XML directly
        feed = _feedparser.parse(resp.text)

        if feed.bozo and not feed.entries:
            raise CollectorError(
                f"feedparser could not parse e-Gov RSS feed "
                f"({_EGOV_RSS_URL}): {feed.bozo_exception}"
            )

        total = len(feed.entries)
        items: list[dict] = []

        for entry in feed.entries:
            title: str = (getattr(entry, "title", None) or "").strip()

            if not _title_matches(title):
                continue

            # link takes priority over id (which may be a GUID, not a URL)
            url: str = (getattr(entry, "link", None) or "").strip()
            external_id: str = url or (getattr(entry, "id", None) or title)

            body: str = (
                getattr(entry, "summary", None)
                or getattr(entry, "description", None)
                or ""
            ).strip()

            # feedparser exposes the raw date string as entry.published
            published_at: str | None = getattr(entry, "published", None)

            items.append(
                self._raw_item(
                    source_id=self.source_id,
                    external_id=external_id,
                    url=url,
                    title=title,
                    body=body,
                    category="regulation",
                    country="JP",
                    published_at=published_at,
                    extra={"feed_url": _EGOV_RSS_URL},
                )
            )

        log.info(
            "[%s] %d / %d entries matched keyword filter",
            self.source_id,
            len(items),
            total,
        )
        return items
