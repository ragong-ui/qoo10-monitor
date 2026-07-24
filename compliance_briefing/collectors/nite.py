"""
NITE (National Institute of Technology and Evaluation / 製品評価技術基盤機構)
product safety incident collector.

Strategy (in order of preference):
  1. Parse the NITE incident news RSS feed via feedparser.
  2. If the RSS is unavailable or returns no entries, fall back to scraping
     https://www.nite.go.jp/jiko/news/ with BeautifulSoup.

All entries from NITE are safety-relevant — no keyword filter is applied.
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

_NITE_RSS_URL = "https://www.nite.go.jp/jiko/news/rss.xml"
_NITE_NEWS_URL = "https://www.nite.go.jp/jiko/news/"
_NITE_BASE_URL = "https://www.nite.go.jp"


def _resolve_url(href: str) -> str:
    """Convert a relative or protocol-relative href to an absolute URL."""
    href = href.strip()
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return _NITE_BASE_URL + href
    return _NITE_BASE_URL + "/" + href


class NITECollector(BaseCollector):
    source_id = "nite"

    def _fetch_live(self) -> list[dict]:
        if not _FEEDPARSER_AVAILABLE and not _BS4_AVAILABLE:
            raise CollectorError(
                "Neither feedparser nor beautifulsoup4 is installed — "
                "cannot run NITECollector. "
                "Run: pip install feedparser beautifulsoup4"
            )

        # Attempt 1: RSS
        if _FEEDPARSER_AVAILABLE:
            items = self._try_rss()
            if items is not None:
                return items

        # Attempt 2: HTML scraping
        if _BS4_AVAILABLE:
            return self._scrape_news_page()

        raise CollectorError(
            "NITE RSS feed is unavailable and beautifulsoup4 is not installed "
            "for the HTML fallback. Run: pip install beautifulsoup4"
        )

    # ------------------------------------------------------------------
    # Strategy 1: RSS
    # ------------------------------------------------------------------

    def _try_rss(self) -> list[dict] | None:
        """
        Attempt to collect items via the NITE RSS feed.
        Returns a list on success (may be empty), or None to signal fallback.
        """
        log.debug("[%s] Trying RSS: %s", self.source_id, _NITE_RSS_URL)
        try:
            resp = self._get(_NITE_RSS_URL, timeout=20)
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

        items: list[dict] = []

        for entry in feed.entries:
            title: str = (getattr(entry, "title", None) or "").strip()
            url: str = (getattr(entry, "link", None) or "").strip()
            external_id: str = url or (getattr(entry, "id", None) or title)
            body: str = (
                getattr(entry, "summary", None)
                or getattr(entry, "description", None)
                or ""
            ).strip()
            published_at: str | None = getattr(entry, "published", None)

            items.append(
                self._raw_item(
                    source_id=self.source_id,
                    external_id=external_id,
                    url=url,
                    title=title,
                    body=body,
                    category="recall",
                    country="JP",
                    published_at=published_at,
                    extra={"method": "rss", "feed_url": _NITE_RSS_URL},
                )
            )

        log.info("[%s] RSS: %d entries collected", self.source_id, len(items))
        return items

    # ------------------------------------------------------------------
    # Strategy 2: HTML scraping
    # ------------------------------------------------------------------

    def _scrape_news_page(self) -> list[dict]:
        """
        Scrape the NITE news page and return all anchor links as recall items.
        All links on the NITE incident news page are considered safety-relevant.
        """
        log.debug("[%s] Scraping HTML: %s", self.source_id, _NITE_NEWS_URL)
        resp = self._get(_NITE_NEWS_URL, timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")

        seen_urls: set[str] = set()
        items: list[dict] = []

        for a_tag in soup.find_all("a", href=True):
            href: str = a_tag["href"]
            if not href or href.startswith("#") or href.lower().startswith("javascript"):
                continue

            title: str = a_tag.get_text(separator=" ", strip=True)
            if not title:
                continue

            url = _resolve_url(href)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Attempt to extract a nearby date from the surrounding element
            published_at: str | None = None
            parent = a_tag.parent
            if parent:
                import re as _re
                date_match = _re.search(
                    r"(\d{4}[年./]\d{1,2}[月./]\d{1,2}|\d{4}-\d{2}-\d{2})",
                    parent.get_text(separator=" ", strip=True),
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
                    category="recall",
                    country="JP",
                    published_at=published_at,
                    extra={"method": "html_bs4", "source_url": _NITE_NEWS_URL},
                )
            )

        log.info("[%s] HTML scrape: %d links collected", self.source_id, len(items))
        return items
