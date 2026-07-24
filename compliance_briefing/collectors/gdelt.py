"""
GDELT Project API v2 collector — multilingual JP/KR compliance news.

Endpoint: https://api.gdeltproject.org/api/v2/doc/doc
No API key required.

Runs three query passes (two Japanese, one Korean) against GDELT's
artlist mode, covers the last 48 hours, and returns deduplicated items
with category inferred from title keywords.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from ..collector_base import BaseCollector, CollectorError

log = logging.getLogger(__name__)

_GDELT_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# Each tuple: (query_string, sourcelang, fallback_category)
_GDELT_QUERIES: list[tuple[str, str, str]] = [
    (
        # Qoo10 counterfeit / regulation / recall in Japanese media
        '"Qoo10" "偽物" OR "Qoo10" "規制" OR "Qoo10" "回収" OR "Qoo10" "行政処分"',
        "Japanese",
        "regulation",
    ),
    (
        # Korea product incidents reported in Japanese media
        '"韓国" "回収" "日本" OR "Korea" "recall" "Japan" OR "韓国製品" "事故"',
        "Japanese",
        "safety",
    ),
    (
        # Korean-language coverage: 큐텐(Qoo10) regulation / recall
        '"큐텐" "규제" OR "한국" "리콜" "일본" OR "소비자원" "리콜"',
        "Korean",
        "safety",
    ),
]

# Keyword sets used for category classification
_SAFETY_KEYWORDS: frozenset[str] = frozenset({
    "recall", "リコール", "回収", "리콜", "事故", "accident", "injury", "危険",
    "defect", "欠陥", "火災", "fire", "explosion", "爆発",
})
_REGULATION_KEYWORDS: frozenset[str] = frozenset({
    "規制", "regulation", "法律", "法改正", "改正", "措置", "命令",
    "행정", "규제", "처분", "법률", "행정처분",
})
_COMPETITOR_KEYWORDS: frozenset[str] = frozenset({
    "Rakuten", "Amazon", "楽天", "competitor", "競合",
})


def _classify_title(title: str, fallback: str) -> str:
    """Infer category from article title keywords."""
    lower = title.lower()
    if any(kw.lower() in lower for kw in _SAFETY_KEYWORDS):
        return "safety"
    if any(kw.lower() in lower for kw in _REGULATION_KEYWORDS):
        return "regulation"
    if any(kw.lower() in lower for kw in _COMPETITOR_KEYWORDS):
        return "competitor"
    return fallback


def _gdelt_timestamp(hours_ago: int) -> str:
    """Return a GDELT-format UTC timestamp string (YYYYMMDDHHmmss) N hours in the past."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.strftime("%Y%m%d%H%M%S")


def _parse_seendate(seendate: str) -> str | None:
    """
    Convert a GDELT seendate string to an ISO-8601 UTC string.

    GDELT formats observed:
      "20240101T120000Z"
      "20240101T120000.000Z"
    Returns the original string unchanged if parsing fails.
    """
    if not seendate:
        return None
    try:
        # Strip sub-second precision and trailing Z before parsing
        clean = re.sub(r"\.\d+Z?$", "", seendate.rstrip("Z"))
        dt = datetime.strptime(clean, "%Y%m%dT%H%M%S")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return seendate


class GDELTCollector(BaseCollector):
    source_id = "gdelt"

    def _fetch_live(self) -> list[dict]:
        start_dt = _gdelt_timestamp(hours_ago=48)
        seen_urls: set[str] = set()
        items: list[dict] = []

        for query, sourcelang, fallback_category in _GDELT_QUERIES:
            log.debug(
                "[%s] Query: %r  lang=%s", self.source_id, query, sourcelang
            )

            params: dict = {
                "query": query,
                "mode": "artlist",
                "maxrecords": self.cfg.gdelt_max_records,
                "format": "json",
                "sourcelang": sourcelang,
                "startdatetime": start_dt,
                "timespan": "48h",
            }

            try:
                resp = self._get(_GDELT_API_URL, params=params, timeout=30)
                data = resp.json()
            except Exception as exc:
                log.warning(
                    "[%s] Query %r failed (%s) — skipping",
                    self.source_id,
                    query,
                    exc,
                )
                continue

            articles: list[dict] = data.get("articles") or []
            log.debug(
                "[%s] Query %r → %d articles (lang=%s)",
                self.source_id,
                query,
                len(articles),
                sourcelang,
            )

            for article in articles:
                url: str = (article.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                title: str = (article.get("title") or "").strip()
                seendate: str = article.get("seendate", "")
                language: str = article.get("language") or sourcelang
                domain: str = article.get("domain") or ""

                category = _classify_title(title, fallback_category)
                published_at = _parse_seendate(seendate)

                items.append(
                    self._raw_item(
                        source_id=self.source_id,
                        external_id=url,
                        url=url,
                        title=title,
                        body="",  # GDELT artlist mode does not return article body text
                        category=category,
                        country="JP",
                        published_at=published_at,
                        extra={
                            "query": query,
                            "sourcelang": sourcelang,
                            "language": language,
                            "domain": domain,
                            "seendate_raw": seendate,
                        },
                    )
                )

        log.info(
            "[%s] Finished: %d unique articles across %d query passes",
            self.source_id,
            len(items),
            len(_GDELT_QUERIES),
        )
        return items
