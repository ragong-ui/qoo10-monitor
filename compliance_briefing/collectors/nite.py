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

_NITE_PRESS_BASE = "https://www.nite.go.jp/jiko/chuikanki/press"
_NITE_BASE_URL = "https://www.nite.go.jp"


def _current_fy_url() -> str:
    """Return NITE press release index URL for the current Japanese fiscal year."""
    from datetime import datetime
    now = datetime.now()
    fy = now.year if now.month >= 4 else now.year - 1
    return f"{_NITE_PRESS_BASE}/{fy}fy/index.html"


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
        if not _BS4_AVAILABLE:
            raise CollectorError(
                "beautifulsoup4 is not installed — "
                "cannot run NITECollector. "
                "Run: pip install beautifulsoup4"
            )

        return self._scrape_news_page()

    # ------------------------------------------------------------------
    # HTML scraping (current fiscal year press releases)
    # ------------------------------------------------------------------

    def _scrape_news_page(self) -> list[dict]:
        """
        Scrape NITE press release index for the current fiscal year.
        URL pattern: /jiko/chuikanki/press/{year}fy/index.html
        """
        news_url = _current_fy_url()
        log.debug("[%s] Scraping HTML: %s", self.source_id, news_url)
        resp = self._get(news_url, timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")

        seen_urls: set[str] = set()
        items: list[dict] = []

        for a_tag in soup.find_all("a", href=True):
            href: str = a_tag["href"]
            if not href or href.startswith("#") or href.lower().startswith("javascript"):
                continue

            # Only collect actual press release pages (prs*.html), not year index pages
            import re as _re
            if not _re.search(r"/jiko/chuikanki/press/\d{4}fy/prs\d+\.html", href):
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
                    extra={"method": "html_bs4", "source_url": news_url},
                )
            )

        log.info("[%s] HTML scrape: %d links collected", self.source_id, len(items))
        return items
