"""
Consumer Affairs Agency (消費者庁) collector — recalls, sanctions, consumer alerts.

Strategy:
  1. Scrape year-specific archive page: /notice/archive/{year}/
     Collects all /notice/entry/XXXXXX/ links (official notices, always relevant).
  2. Scrape recall information site: https://www.recall.caa.go.jp/
     Collects detail.php links (product recalls, highest priority).

Both scrapes use static HTML — no feedparser or JS rendering required.
"""

import logging
import re
from datetime import datetime

from ..collector_base import BaseCollector, CollectorError

log = logging.getLogger(__name__)

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

_CAA_BASE_URL = "https://www.caa.go.jp"
_CAA_RECALL_BASE = "https://www.recall.caa.go.jp"

_FILTER_KEYWORDS: tuple[str, ...] = (
    "回収", "リコール", "措置命令", "課徴金", "行政処分", "注意喚起",
    "景品表示", "電子商取引", "越境", "消費者", "重大事故", "製品事故",
    "製品安全", "公表", "改正", "規制", "通達",
)


def _matches(text: str) -> bool:
    return any(kw in text for kw in _FILTER_KEYWORDS)


def _current_year() -> int:
    return datetime.now().year


def _archive_url(year: int) -> str:
    return f"{_CAA_BASE_URL}/notice/archive/{year}/"


class CAACollector(BaseCollector):
    source_id = "caa"

    def _fetch_live(self) -> list[dict]:
        if not _BS4_AVAILABLE:
            raise CollectorError(
                "beautifulsoup4 is not installed. Run: pip install beautifulsoup4"
            )

        items: list[dict] = []
        seen_urls: set[str] = set()

        # ── Source 1: CAA archive (official notices) ──────────────────
        items.extend(self._scrape_archive(seen_urls))

        # ── Source 2: recall.caa.go.jp front page ────────────────────
        items.extend(self._scrape_recall_site(seen_urls))

        if len(self._partial_errors) >= 2 and not items:
            raise CollectorError("All CAA requests failed")

        log.info("[%s] Total: %d items from %d unique URLs", self.source_id, len(items), len(seen_urls))
        return items

    # ------------------------------------------------------------------
    # Source 1: CAA notice archive (year-specific)
    # ------------------------------------------------------------------

    def _scrape_archive(self, seen_urls: set) -> list[dict]:
        year = _current_year()
        url = _archive_url(year)
        log.debug("[%s] Scraping archive: %s", self.source_id, url)

        try:
            resp = self._get(url, timeout=30)
        except Exception as exc:
            self._record_partial_error(f"Archive fetch failed: {exc}")
            log.warning("[%s] Archive fetch failed (%s)", self.source_id, exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        items: list[dict] = []

        for a_tag in soup.find_all("a", href=True):
            href: str = a_tag["href"].strip()
            title: str = a_tag.get_text(separator=" ", strip=True)

            if not title or len(title) < 5:
                continue

            # Include all /notice/entry/XXXXXX/ links (all are official notices)
            # Include /notice/statement/ etc. only if keyword matches
            is_entry = bool(re.search(r"/notice/entry/\d+/", href))
            is_other_notice = href.startswith("/notice/") and not is_entry

            if not is_entry and not (is_other_notice and _matches(title)):
                continue

            abs_url = _CAA_BASE_URL + href if href.startswith("/") else href
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            # Date extraction from parent element
            published_at = _extract_date(a_tag)

            category = "recall" if any(kw in title for kw in ("回収", "リコール", "重大事故", "製品事故")) else "regulation"

            items.append(
                self._raw_item(
                    source_id=self.source_id,
                    external_id=abs_url,
                    url=abs_url,
                    title=title,
                    body="",
                    category=category,
                    country="JP",
                    published_at=published_at,
                    extra={"method": "archive_html", "source_url": url},
                )
            )

        log.info("[%s] Archive scrape: %d items (year=%d)", self.source_id, len(items), year)
        return items

    # ------------------------------------------------------------------
    # Source 2: recall.caa.go.jp (product recall database)
    # ------------------------------------------------------------------

    def _scrape_recall_site(self, seen_urls: set) -> list[dict]:
        log.debug("[%s] Scraping recall site: %s", self.source_id, _CAA_RECALL_BASE)
        try:
            resp = self._get(_CAA_RECALL_BASE + "/", timeout=30)
        except Exception as exc:
            self._record_partial_error(f"Recall site fetch failed: {exc}")
            log.warning("[%s] Recall site fetch failed (%s) — skipping", self.source_id, exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        items: list[dict] = []

        for a_tag in soup.find_all("a", href=True):
            href: str = a_tag["href"].strip()
            title: str = a_tag.get_text(separator=" ", strip=True)

            if "detail.php" not in href or not title or len(title) < 5:
                continue

            abs_url = (_CAA_RECALL_BASE + "/" + href.lstrip("/")) if not href.startswith("http") else href
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            # Extract recall type from title suffix (e.g. "- 回収", "- 修理")
            recall_type = ""
            if " - " in title:
                recall_type = title.split(" - ")[-1].strip()

            items.append(
                self._raw_item(
                    source_id=self.source_id,
                    external_id=abs_url,
                    url=abs_url,
                    title=title,
                    body=recall_type,
                    category="recall",
                    country="JP",
                    published_at=None,
                    extra={"method": "recall_site", "recall_type": recall_type},
                )
            )

        log.info("[%s] Recall site: %d items", self.source_id, len(items))
        return items


# ── Utilities ─────────────────────────────────────────────────────────────────

def _extract_date(a_tag) -> str | None:
    """Try to extract a date from the parent/sibling elements of a link."""
    parent = a_tag.parent
    if not parent:
        return None
    text = parent.get_text(separator=" ", strip=True)
    m = re.search(r"(\d{4}[年./]\d{1,2}[月./]\d{1,2}|\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else None
