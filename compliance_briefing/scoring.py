"""
Rule-based severity and confidence scoring for compliance alerts.
"""

from __future__ import annotations

# ── Severity keyword patterns ─────────────────────────────────────────────────

_CRITICAL_TITLE_PATTERNS = [
    "強制回収", "緊急回収", "リコール", "recall", "回収命令",
    "販売停止命令", "輸入禁止", "製品事故", "製品安全", "重篤",
    "死亡", "重傷", "火災", "爆発", "感電",
    "긴급 리콜", "판매 중지 명령", "제품 회수", "중대 위해",
]

_HIGH_TITLE_PATTERNS = [
    "行政処分", "課徴金", "措置命令", "景品表示法", "特定商取引",
    "プロバイダ責任", "改正", "規制強化", "法改正", "告発",
    "個人情報漏えい", "不正アクセス", "フィッシング",
    "관세법", "전자상거래법", "개인정보", "과징금", "행정처분",
]

_MEDIUM_TITLE_PATTERNS = [
    "ガイドライン", "通知", "Q&A", "FAQ", "解説",
    "注意喚起", "啓発", "周知", "パブリックコメント",
    "가이드라인", "고시", "행정예고",
]

# ── Source-based severity baseline ────────────────────────────────────────────

_SOURCE_SEVERITY_MAP: dict[str, str] = {
    # Critical — product safety with enforcement
    "nite": "critical",
    "caa": "high",
    "safety_korea_mfds": "critical",
    # High — regulatory bodies
    "egov": "high",
    "meti": "high",
    "jftc": "high",
    "ppc": "high",
    "mhlw": "high",
    "safety_korea_kats": "high",
    "safety_korea_kca": "medium",
    # Medium — news / monitoring
    "brave_news": "medium",
    "gdelt": "medium",
    "ncac": "medium",
}

_CATEGORY_SEVERITY_BOOST: dict[str, str] = {
    "recall": "critical",
    "safety": "high",
    "regulation": "high",
    "competitor": "medium",
}

_SEV_ORDER = ["critical", "high", "medium", "low"]


def _max_severity(*sevs: str) -> str:
    best = len(_SEV_ORDER) - 1
    for s in sevs:
        try:
            best = min(best, _SEV_ORDER.index(s))
        except ValueError:
            pass
    return _SEV_ORDER[best]


# ── Confidence scoring ────────────────────────────────────────────────────────

_HIGH_CONFIDENCE_SOURCES = {"nite", "caa", "egov", "meti", "jftc", "ppc", "mhlw",
                             "safety_korea_mfds", "safety_korea_kats"}
_MEDIUM_CONFIDENCE_SOURCES = {"safety_korea_kca", "ncac", "brave_news"}


def score_alert(raw_item: dict) -> tuple[str, str]:
    """
    Return (severity, confidence) for a raw_item.
    """
    source_id = raw_item.get("source_id", "")
    category = raw_item.get("category", "")
    title = (raw_item.get("title") or "").lower()
    body = (raw_item.get("body") or "").lower()
    text = title + " " + body

    # Severity from source
    base_sev = _SOURCE_SEVERITY_MAP.get(source_id, "medium")

    # Severity from category
    cat_sev = _CATEGORY_SEVERITY_BOOST.get(category, "medium")

    # Severity from title/body keywords
    kw_sev = "medium"
    for kw in _CRITICAL_TITLE_PATTERNS:
        if kw.lower() in text:
            kw_sev = "critical"
            break
    if kw_sev != "critical":
        for kw in _HIGH_TITLE_PATTERNS:
            if kw.lower() in text:
                kw_sev = "high"
                break

    severity = _max_severity(base_sev, cat_sev, kw_sev)

    # Confidence
    if source_id in _HIGH_CONFIDENCE_SOURCES:
        confidence = "high"
    elif source_id in _MEDIUM_CONFIDENCE_SOURCES:
        confidence = "medium"
    else:
        confidence = "low"

    return severity, confidence


def source_status(source_ids: list[str]) -> str:
    """Determine source_status from the set of sources that found this alert."""
    if not source_ids:
        return "single_source"
    confirmed_sources = _HIGH_CONFIDENCE_SOURCES & set(source_ids)
    if confirmed_sources:
        return "primary_confirmed"
    if len(set(source_ids)) >= 2:
        return "multi_source_confirmed"
    return "single_source"
