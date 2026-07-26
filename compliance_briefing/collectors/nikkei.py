"""
Nikkei Shimbun (日本経済新聞) collector — compliance & regulation news.

Strategy (no API key or login required):
  1. Search queries: /search?keyword=<term> — keyword-targeted, returns ~10 articles each
  2. Category pages: /news/category/economy/ and /news/category/politics/ — broader coverage

All article metadata (title, snippet, date) is available in static HTML without a subscription.
Full article content is behind a paywall and is NOT fetched.
"""

import logging
import re
import time
from datetime import date

from ..collector_base import BaseCollector

log = logging.getLogger(__name__)

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

_NIKKEI_BASE = "https://www.nikkei.com"
_NIKKEI_SEARCH = "https://www.nikkei.com/search"

# Compliance-focused search queries
_SEARCH_QUERIES: list[str] = [
    "消費者庁 措置命令",
    "景品表示法 課徴金",
    "公正取引委員会",
    "電子商取引法 改正",
    "越境EC 規制",
    "Qoo10 規制",
    "製品リコール 消費者庁",
    "特定商取引法",
]

# Broad category pages (filtered by keywords post-fetch)
_CATEGORY_URLS: list[str] = [
    f"{_NIKKEI_BASE}/news/category/economy/",
    f"{_NIKKEI_BASE}/news/category/politics/",
]

# Keyword filter for category page results
_FILTER_KEYWORDS: tuple[str, ...] = (
    "消費者庁", "公正取引", "景品表示", "措置命令", "課徴金",
    "電子商取引", "越境", "リコール", "回収", "行政処分",
    "規制", "Qoo10", "楽天", "Amazon", "特定商取引",
    "個人情報", "製品安全", "製品事故",
)


def _matches(text: str) -> bool:
    return any(kw in text for kw in _FILTER_KEYWORDS)


def _abs_url(href: str) -> str:
    if href.startswith("http"):
        return href
    return _NIKKEI_BASE + href


def _parse_article_date(text: str) -> str | None:
    """Extract and normalize an article date as YYYY-MM-DD."""
    match = re.search(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日|"
        r"(\d{4})/(\d{1,2})/(\d{1,2})",
        text,
    )
    if not match:
        return None

    parts = match.groups()
    year, month, day = (
        parts[:3] if parts[0] is not None else parts[3:]
    )
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except (TypeError, ValueError):
        return None


def _is_recent(
    published_at: str | None,
    lookback_days: int,
    today: date | None = None,
) -> bool:
    """Return whether a normalized article date is within the live lookback."""
    if not published_at:
        return False
    try:
        published = date.fromisoformat(published_at)
    except ValueError:
        return False

    current = today or date.today()
    age_days = (current - published).days
    return 0 <= age_days <= lookback_days


class NikkeiCollector(BaseCollector):
    source_id = "nikkei"

    def _fetch_live(self) -> list[dict]:
        if not _BS4_AVAILABLE:
            from ..collector_base import CollectorError
            raise CollectorError(
                "beautifulsoup4 is not installed. Run: pip install beautifulsoup4"
            )

        seen_urls: set[str] = set()
        items: list[dict] = []

        # ── Step 1: Keyword search queries ───────────────────────────
        for query in _SEARCH_QUERIES:
            batch = self._search(query, seen_urls)
            items.extend(batch)
            time.sleep(0.5)

        # ── Step 2: Category pages (with keyword filter) ─────────────
        for cat_url in _CATEGORY_URLS:
            batch = self._scrape_category(cat_url, seen_urls)
            items.extend(batch)
            time.sleep(0.5)

        request_count = len(_SEARCH_QUERIES) + len(_CATEGORY_URLS)
        if request_count and len(self._partial_errors) >= request_count:
            from ..collector_base import CollectorError
            raise CollectorError("All Nikkei requests failed")

        log.info("[%s] Total: %d items (%d unique URLs)", self.source_id, len(items), len(seen_urls))
        return items

    # ------------------------------------------------------------------
    # Search query scraping
    # ------------------------------------------------------------------

    def _search(self, query: str, seen_urls: set) -> list[dict]:
        log.debug("[%s] Search: %r", self.source_id, query)
        try:
            resp = self._get(_NIKKEI_SEARCH, params={"keyword": query}, timeout=20)
        except Exception as exc:
            self._record_partial_error(f"Search {query!r} failed: {exc}")
            log.warning("[%s] Search %r failed: %s", self.source_id, query, exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        items: list[dict] = []
        seen_in_page: set[str] = set()

        for a_tag in soup.find_all("a", href=True):
            href: str = a_tag["href"]
            if "/article/" not in href:
                continue

            url = _abs_url(href)
            if url in seen_urls or url in seen_in_page:
                continue
            seen_in_page.add(url)

            title = a_tag.get_text(strip=True)
            # Skip navigation/date-only text (very short or pure date strings)
            if not title or len(title) < 10 or re.match(r"^\d{4}年", title):
                continue

            # Extract date from nearby sibling text
            parent_text = ""
            parent = a_tag.parent
            if parent:
                parent_text = parent.get_text(separator=" ", strip=True)
            published_at = _parse_article_date(parent_text)
            if not _is_recent(published_at, self.cfg.nikkei_lookback_days):
                log.debug(
                    "[%s] Skipping stale/undated search result: %s (%s)",
                    self.source_id,
                    url,
                    published_at or "undated",
                )
                continue

            seen_urls.add(url)
            items.append(
                self._raw_item(
                    source_id=self.source_id,
                    external_id=url,
                    url=url,
                    title=title,
                    body="",
                    category=_infer_category(title),
                    country="JP",
                    published_at=published_at,
                    extra={"method": "search", "query": query},
                )
            )

        log.debug("[%s] Search %r → %d items", self.source_id, query, len(items))
        return items

    # ------------------------------------------------------------------
    # Category page scraping (keyword-filtered)
    # ------------------------------------------------------------------

    def _scrape_category(self, cat_url: str, seen_urls: set) -> list[dict]:
        log.debug("[%s] Category: %s", self.source_id, cat_url)
        try:
            resp = self._get(cat_url, timeout=20)
        except Exception as exc:
            self._record_partial_error(f"Category {cat_url} failed: {exc}")
            log.warning("[%s] Category %s failed: %s", self.source_id, cat_url, exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        items: list[dict] = []
        seen_in_page: set[str] = set()

        for a_tag in soup.find_all("a", href=True):
            href: str = a_tag["href"]
            if "/article/" not in href:
                continue

            url = _abs_url(href)
            if url in seen_urls or url in seen_in_page:
                continue
            seen_in_page.add(url)

            title = a_tag.get_text(strip=True)
            if not title or len(title) < 10 or re.match(r"^\d{4}年", title):
                continue

            # Apply keyword filter for category pages
            if not _matches(title):
                continue

            parent_text = ""
            parent = a_tag.parent
            if parent:
                parent_text = parent.get_text(separator=" ", strip=True)
            published_at = _parse_article_date(parent_text)
            if not _is_recent(published_at, self.cfg.nikkei_lookback_days):
                log.debug(
                    "[%s] Skipping stale/undated category result: %s (%s)",
                    self.source_id,
                    url,
                    published_at or "undated",
                )
                continue

            seen_urls.add(url)
            items.append(
                self._raw_item(
                    source_id=self.source_id,
                    external_id=url,
                    url=url,
                    title=title,
                    body="",
                    category=_infer_category(title),
                    country="JP",
                    published_at=published_at,
                    extra={"method": "category", "source_url": cat_url},
                )
            )

        log.debug("[%s] Category %s → %d items", self.source_id, cat_url, len(items))
        return items


# ── Utilities ─────────────────────────────────────────────────────────────────

def _infer_category(title: str) -> str:
    if any(kw in title for kw in ("リコール", "回収", "製品事故", "製品安全")):
        return "recall"
    if any(kw in title for kw in ("Qoo10", "楽天", "Amazon", "競合")):
        return "competitor"
    return "regulation"
