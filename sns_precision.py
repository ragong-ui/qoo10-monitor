"""Google/X 공통 정밀도 필터.

사람의 오탐지여부를 최우선으로 사용한다. AI 판정은 분석 참고값이며 이
모듈의 하드 제외 조건으로 단독 사용하지 않는다.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit


# 2026-08-27 누적 Google 404건(O=301, X=103)에서 사람 X가 한 건도
# 없었던 반복 도메인만 포함한다. SNS/커뮤니티처럼 향후 실검지가 가능한
# 범용 도메인은 과거 O 비율이 높더라도 제외하지 않는다.
GOOGLE_EXCLUDE_DOMAINS = (
    "ecnomikata.com",             # O=13, X=0
    "bibicopy.net",               # O=6, X=0
    "47news.jp",                  # O=4, X=0
    "healthbusiness-online.com",  # O=4, X=0
    "indeed.com",                 # O=4, X=0
    "aucfan.com",                 # O=4, X=0
    "taointerests.com",           # O=4, X=0
    "shaktimat.co.jp",            # O=4, X=0
    "app-tatsujin.com",           # O=3, X=0
    "search.yahoo.co.jp",         # O=3, X=0 (검색결과 집계 페이지)
    "makoto-mall.jp",             # O=3, X=0
    "b.hatena.ne.jp",             # O=3, X=0 (집계 페이지)
    "bigankipatrol.com",          # 기존 검증 규칙, O=2, X=0
)

# lipscosme.com(O=16, X=2), kigencheck.jp(O=2, X=1)는 과거 규칙에서
# 제외 도메인이었지만 사람 X가 확인되어 하드 제외 대상에서 제거했다.

COMMON_EXCLUDE_KEYWORDS = (
    "見分け方",
    "見分け方法",
    "弊社が判断した場合",
    "キャッチコピー",
    "詐欺メイク",
    "すっぴん詐欺",
    # 누적 피드백에서 명시적 상품 제공 게시물은 O=1, X=0이며 광고 범주다.
    "商品提供",
)

_AD_DISCLOSURE_RE = re.compile(
    r"(?i)(?<![\w])#(?:pr|広告|ad|sponsored|タイアップ|案件)(?![\w])"
)


def normalized_host(url: str) -> str:
    return (urlsplit(str(url or "")).hostname or "").lower().removeprefix("www.")


def host_matches(host: str, domain: str) -> bool:
    host = str(host or "").lower().removeprefix("www.")
    domain = str(domain or "").lower().removeprefix("www.")
    return host == domain or host.endswith("." + domain)


def is_excluded_domain(url: str, *, source: str) -> bool:
    if source.lower() != "google":
        return False
    host = normalized_host(url)
    return any(host_matches(host, domain) for domain in GOOGLE_EXCLUDE_DOMAINS)


def contains_hard_exclusion(text: str) -> bool:
    value = str(text or "")
    folded = value.casefold()
    if any(keyword.casefold() in folded for keyword in COMMON_EXCLUDE_KEYWORDS):
        return True
    return bool(_AD_DISCLOSURE_RE.search(value))


def exclusion_reason(*, url: str, text: str, source: str) -> str:
    if is_excluded_domain(url, source=source):
        return f"feedback-domain:{normalized_host(url)}"
    if contains_hard_exclusion(text):
        return "explicit-guide-or-ad"
    return ""
