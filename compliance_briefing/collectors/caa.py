"""
Consumer Affairs Agency (消費者庁) collector — recalls, sanctions, consumer alerts.

Strategy (in order of preference):
  1. Parse the official RSS feed via feedparser.
  2. If the RSS is unavailable or empty, fall back to scraping
     https://www.caa.go.jp/notice/ with BeautifulSoup.

At least one of {feedparser, beautifulsoup4} must be installed.
"""

import logging

from ..collector_base import BaseCollector, CollectorError

log = logging.getLogger(__name__)

try:
    import feedparser as _feedparser  # type: ignore[import]
    _FEEDPARSER_AVAILABLE = True
except ImportError:
    _FEEDPARSER_AVAILABLE = False

try:
    from bs4 import BeautifulSoup  # type: ignore[import]
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

_CAA_RSS_URL = "https://www.caa.go.jp/rss/news.rss"
_CAA_NOTICE_URL = "https://www.caa.go.jp/notice/"
_CAA_BASE_URL = "https://www.caa.go.jp"

_FILTER_KEYWORDS: tuple[str, ...] = (
    "回収",
    "リコール",
    "措置命令",
    "課徴金",
    "行政処分",
    "注意喚起",
    "景品表示",
    "電子商取引",
    "越境",
)


def _matches(text: str) -> bool:
    return any(kw in text for kw in _FILTER_KEYWORDS)


def _resolve_url(href: str) -> str:
    """Make an absolute URL from an href found on the CAA website."""
    href = href.strip()
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return _CAA_BASE_URL + href
    return _CAA_BASE_URL + "/" + href


class CAACollector(BaseCollector):
    source_id = "caa"

    def _fetch_live(self) -> list[dict]:
        if not _FEEDPARSER_AVAILABLE and not _BS4_AVAILABLE:
            raise CollectorError(
                "Neither feedparser nor beautifulsoup4 is installed — "
                "cannot run CAACollector. "
                "Run: pip install feedparser beautifulsoup4"
            )

        # Attempt 1: RSS via feedparser
        if _FEEDPARSER_AVAILABLE:
            items = self._try_rss()
            if items is not None:
                return items

        # Attempt 2: HTML scraping via BeautifulSoup
        if _BS4_AVAILABLE:
            return self._scrape_notice_page()

        # feedparser was available but RSS failed, and bs4 is absent
        raise CollectorError(
            "CAA RSS feed is unavailable and beautifulsoup4 is not installed "
            "for the HTML fallback. Run: pip install beautifulsoup4"
        )

    # ------------------------------------------------------------------
    # Strategy 1: RSS
    # ------------------------------------------------------------------

    def _try_rss(self) -> list[dict] | None:
        """
        Attempt to collect items via the CAA RSS feed.
        Returns a list on success (may be empty), or None to signal fallback.
        """
        log.debug("[%s] Trying RSS: %s", self.source_id, _CAA_RSS_URL)
        try:
            resp = self._get(_CAA_RSS_URL, timeout=20)
        except Exception as exc:
            log.warning(
                "[%s] RSS request failed (%s) — falling back to HTML", self.source_id, exc
            )
            return None

        feed = _feedparser.parse(resp.text)

        if feed.bozo and not feed.entries:
            log.warning(
                "[%s] RSS could not be parsed (%s) — falling back to HTML",
                self.source_id,
                feed.bozo_exception,
            )
            return None

        total = len(feed.entries)
        items: list[dict] = []

        for entry in feed.entries:
            title: str = (getattr(entry, "title", None) or "").strip()
            body: str = (
                getattr(entry, "summary", None)
                or getattr(entry, "description", None)
                or ""
            ).strip()

            if not _matches(title) and not _matches(body):
                continue

            url: str = (getattr(entry, "link", None) or "").strip()
            external_id: str = url or (getattr(entry, "id", None) or title)
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
                    extra={"method": "rss", "feed_url": _CAA_RSS_URL},
                )
            )

        log.info(
            "[%s] RSS: %d / %d entries matched keyword filter",
            self.source_id,
            len(items),
            total,
        )
        return items

    # ------------------------------------------------------------------
    # Strategy 2: HTML scraping
    # ------------------------------------------------------------------

    def _scrape_notice_page(self) -> list[dict]:
        """
        Scrape the CAA notices page and return all matching anchor links.
        """
        log.debug("[%s] Scraping HTML: %s", self.source_id, _CAA_NOTICE_URL)
        resp = self._get(_CAA_NOTICE_URL, timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")

        seen_urls: set[str] = set()
        items: list[dict] = []

        for a_tag in soup.find_all("a", href=True):
            href: str = a_tag["href"]
            if not href or href.startswith("#") or href.lower().startswith("javascript"):
                continue

            title: str = a_tag.get_text(separator=" ", strip=True)
            if not title or not _matches(title):
                continue

            url = _resolve_url(href)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Look for a nearby date string in a sibling or parent element
            published_at: str | None = None
            parent = a_tag.parent
            if parent:
                parent_text = parent.get_text(separator=" ", strip=True)
                # Simple heuristic: grab the first "YYYY.MM.DD" or "YYYY年MM月DD日" pattern
                import re as _re
                date_match = _re.search(
                    r"(\d{4}[年./]\d{1,2}[月./]\d{1,2})", parent_text
                )
                if date_match:
                    published_at = date_match.group(1)

            items.append(
                self._raw_item(
                    source_id=self.source_id,
                    external_id=url,
                    url=url,
                    title=title,
                    body="",
                    category="regulation",
                    country="JP",
                    published_at=published_at,
                    extra={"method": "html_bs4", "source_url": _CAA_NOTICE_URL},
                )
            )

        log.info("[%s] HTML scrape: %d matching links", self.source_id, len(items))
        return items
