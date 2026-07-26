"""
Post-collection relevance filter — runs between collect and dedup.

Rules (applied in order):
  1. Domain blocklist — Qoo10 own domains + known noise sites (all sources)
  2. Minimum title length (all sources)
  3. Compliance relevance for news/search sources (brave_news, nikkei, gdelt):
     - a strong enforcement / recall signal, OR
     - both a regulatory action and a compliance subject

Official/government sources (nite, caa, egov, …) bypass rule 3.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# ── Domain blocklist ──────────────────────────────────────────────────────────

_BLOCKED_DOMAINS: frozenset[str] = frozenset({
    # Qoo10 owned — promotional / self-referential content
    "qoo10.jp",
    "seller.qoo10.jp",
    "article-university.qoo10.jp",
    "media.qoo10.jp",
    "about.qoo10.jp",
    "help.qoo10.jp",
    "qoo10.com",
    "giosis.com",
    # Known aggregators / content farms (add as discovered)
    "prtimes.jp",           # PR TIMES press releases — marketing, not regulation
    "dreamnews.jp",         # press release distributor
    "atpress.ne.jp",        # press release distributor
})

# ── Relevance signals ────────────────────────────────────────────────────────

# These are sufficiently specific to qualify without another keyword.
_STRONG_SIGNALS: tuple[str, ...] = (
    # Japanese enforcement / safety
    "措置命令", "課徴金", "行政処分", "業務停止", "回収命令",
    "リコール", "重大製品事故", "製品事故", "不当表示",
    "優良誤認", "有利誤認", "告発", "使用中止", "漏えい", "漏洩",
    # Korean enforcement / safety
    "리콜", "과징금", "행정처분", "시정명령", "영업정지",
    "회수명령", "제품사고", "고발", "사용중지", "개인정보 유출",
)

# Weaker action words qualify only when paired with a compliance subject.
_ACTION_SIGNALS: tuple[str, ...] = (
    # Japanese
    "規制", "改正", "施行", "違反", "禁止", "認証", "安全基準",
    "法案", "指針", "調査", "制裁", "取締", "摘発",
    # Korean
    "규제", "개정", "시행", "위반", "금지", "인증", "안전기준",
    "법안", "지침", "조사", "제재", "단속", "적발",
)

_SUBJECT_SIGNALS: tuple[str, ...] = (
    # Authorities / laws
    "消費者庁", "公正取引委員会", "NITE", "消費生活センター",
    "景品表示法", "特定商取引法", "プロバイダ責任", "個人情報保護",
    "소비자원", "공정거래위원회", "개인정보", "당국",
    # Commerce / product safety
    "電子商取引", "越境EC", "越境 EC", "製品安全", "製品基準",
    "제품안전", "안전인증", "소비자안전", "전자상거래", "해외직구",
    # Marketplaces — never sufficient on their own
    "Qoo10", "楽天", "Rakuten", "Amazon", "큐텐", "쿠팡",
)

# Sources that bypass the keyword relevance check (official/government)
_TRUSTED_SOURCES: frozenset[str] = frozenset({
    "nite", "caa", "egov", "meti", "jftc", "ppc", "mhlw",
    "safety_korea_mfds", "safety_korea_kats", "safety_korea_kca",
})

# Sources subject to keyword relevance check (news / search results)
_NEWS_SOURCES: frozenset[str] = frozenset({
    "brave_news", "nikkei", "gdelt",
})

_MIN_TITLE_LEN = 8  # characters — rejects nav-only text nodes


# ── Internal helpers ──────────────────────────────────────────────────────────

def _is_valid_source_url(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.fragment.lower() != "none"
    )


def _has_mojibake(text: str) -> bool:
    text = text or ""
    return (
        "\ufffd" in text
        or any("\x80" <= ch <= "\x9f" for ch in text)
        or any(marker in text for marker in ("Ã", "Â", "â€", "ðŸ"))
    )


def _netloc(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host.removeprefix("www.")
    except Exception:
        return ""


def _is_blocked(url: str) -> bool:
    host = _netloc(url)
    if not host:
        return False
    return any(host == bd or host.endswith("." + bd) for bd in _BLOCKED_DOMAINS)


def _contains_any(text: str, signals: tuple[str, ...]) -> bool:
    return any(signal.lower() in text for signal in signals)


def _is_relevant(item: dict) -> bool:
    text = (
        (item.get("title") or "") + " " + (item.get("body") or "")
    ).lower()
    if _contains_any(text, _STRONG_SIGNALS):
        return True
    return (
        _contains_any(text, _ACTION_SIGNALS)
        and _contains_any(text, _SUBJECT_SIGNALS)
    )


# ── Public API ────────────────────────────────────────────────────────────────

def filter_items(items: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """
    Filter raw items. Returns (kept_items, rejection_counts_by_reason).

    Rejection reasons: "invalid_url", "invalid_text", "blocked_domain",
    "short_title", "no_keyword"
    """
    kept: list[dict] = []
    counts: dict[str, int] = {
        "invalid_url": 0,
        "invalid_text": 0,
        "blocked_domain": 0,
        "short_title": 0,
        "no_keyword": 0,
    }

    for item in items:
        source_id = item.get("source_id", "")
        url = item.get("url", "")
        title = (item.get("title") or "").strip()

        # Rule 1 — valid, navigable source URL (all sources)
        if not _is_valid_source_url(url):
            log.debug("[filter] invalid_url: %s (src=%s)", url, source_id)
            counts["invalid_url"] += 1
            continue

        # Rule 2 — reject corrupted source text before it reaches the DB
        if _has_mojibake(title) or _has_mojibake(item.get("body") or ""):
            log.debug("[filter] invalid_text: %r (src=%s)", title, source_id)
            counts["invalid_text"] += 1
            continue

        # Rule 3 — domain blocklist (all sources)
        if _is_blocked(url):
            log.debug("[filter] blocked_domain: %s (src=%s)", url, source_id)
            counts["blocked_domain"] += 1
            continue

        # Rule 4 — minimum title length (all sources)
        if len(title) < _MIN_TITLE_LEN:
            log.debug("[filter] short_title: %r (src=%s)", title, source_id)
            counts["short_title"] += 1
            continue

        # Rule 5 — structured relevance (news sources only)
        if source_id in _NEWS_SOURCES and not _is_relevant(item):
            log.debug("[filter] no_keyword: %r (src=%s)", title, source_id)
            counts["no_keyword"] += 1
            continue

        kept.append(item)

    total_rejected = sum(counts.values())
    if total_rejected:
        log.info(
            "[filter] Rejected %d / %d items — %s",
            total_rejected,
            len(items),
            ", ".join(f"{r}={n}" for r, n in counts.items() if n),
        )

    return kept, counts


def is_blocked_url(url: str) -> bool:
    """Quick URL check — used by collectors for inline pre-filtering."""
    return _is_blocked(url)
