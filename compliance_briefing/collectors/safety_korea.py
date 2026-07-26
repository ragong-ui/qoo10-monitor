"""
Safety Korea Collector — 국가기술표준원 / 한국소비자원 제품 리콜 데이터

Sources (우선순위 순):
1. data.go.kr 제품안전정보원 API (SAFETY_KOREA_API_KEY 설정 시)
   https://apis.data.go.kr/1130000/MdcsInfoService01/getMdcsInfo01List
2. 식품의약품안전처 OpenAPI (apiKey 방식)
   http://openapi.foodsafetykorea.go.kr/api/{key}/C003/json/1/100
3. 소비자24 공공 리콜 RSS / HTML 파싱 (API키 없을 때 fallback)
   https://www.safetykorea.go.kr/release/recall/list
"""

from __future__ import annotations

import hashlib
import html as html_lib
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urljoin

from ..collector_base import BaseCollector, CollectorError

if TYPE_CHECKING:
    from ..config import ComplianceConfig

log = logging.getLogger(__name__)

# ── 상수 ─────────────────────────────────────────────────────────────────────

# data.go.kr 제품안전정보 공공 API
_MDCS_ENDPOINT = (
    "https://apis.data.go.kr/1130000/MdcsInfoService01/getMdcsInfo01List"
)

# 식품의약품안전처 리콜 (식품 외 품목 포함)
_MFDS_ENDPOINT = "http://openapi.foodsafetykorea.go.kr/api/{key}/C003/json/1/100"

# 소비자24 리콜 목록 (공개 HTML) — 2026.07 도메인 변경: .go.kr → .kr
_SK_RECALL_LIST_URL = "https://www.safetykorea.kr/recall/recallBoard"
_SK_RECALL_BASE_URL = "https://www.safetykorea.kr"

# 일본 관련 키워드 (추가 필터링용 — 현재는 전체 포함)
_JP_KEYWORDS = [
    "日本", "輸出", "越境", "EC", "海外販売",
    "Qoo10", "楽天", "Amazon", "직구", "해외",
]

# 일본 판매 가능성 높은 품목 카테고리 키워드
_JP_CATEGORY_KEYWORDS = [
    "전자", "화장품", "유아", "어린이", "완구", "배터리", "충전",
    "가전", "뷰티", "헬스", "의료기기", "식품",
    "electronics", "cosmetic", "child", "toy", "battery",
]


class SafetyKoreaCollector(BaseCollector):
    """
    한국 제품 리콜 정보 수집기.

    API 키가 설정된 경우 data.go.kr 공공 API를 사용하고,
    없을 경우 소비자24 사이트 HTML을 파싱해 리콜 목록을 가져옵니다.
    """

    source_id: str = "safety_korea_kca"

    def _fetch_live(self) -> list[dict]:
        """우선순위에 따라 데이터 소스 시도."""
        if self.cfg.safety_korea_api_key:
            # 1순위: data.go.kr MDCS API
            try:
                items = self._fetch_mdcs_api()
                if items:
                    log.info("[%s] MDCS API에서 %d건 수집", self.source_id, len(items))
                    return items
            except Exception as e:
                self._record_partial_error(f"MDCS API 실패: {e}")
                log.warning("[%s] MDCS API 실패: %s — MFDS 시도", self.source_id, e)

            # 2순위: 식품의약품안전처 API
            try:
                items = self._fetch_mfds_api()
                if items:
                    log.info("[%s] MFDS API에서 %d건 수집", self.source_id, len(items))
                    return items
            except Exception as e:
                self._record_partial_error(f"MFDS API 실패: {e}")
                log.warning("[%s] MFDS API 실패: %s — HTML 파싱으로 전환", self.source_id, e)

        # 3순위 (fallback): 소비자24 HTML 파싱
        log.info("[%s] 소비자24 HTML 파싱 시작", self.source_id)
        try:
            return self._fetch_html_fallback()
        except CollectorError as exc:
            if self._partial_errors:
                upstream = "; ".join(self._partial_errors)
                raise CollectorError(f"{upstream}; {exc}") from exc
            raise

    # ── MDCS (data.go.kr) API ─────────────────────────────────────────────────

    def _fetch_mdcs_api(self) -> list[dict]:
        """
        data.go.kr 제품안전정보원 리콜 API 호출.
        serviceKey는 URL 인코딩 없이 전달 (공공API 표준).
        """
        params = {
            "serviceKey": self.cfg.safety_korea_api_key,
            "pageNo": "1",
            "numOfRows": "50",
            "dataType": "JSON",
        }
        resp = self._get(_MDCS_ENDPOINT, params=params, timeout=30)
        data = resp.json()

        # 응답 구조 탐색
        body = (
            data.get("response", {}).get("body", {})
            or data.get("body", {})
            or data
        )
        items_raw = (
            body.get("items", {}).get("item", [])
            if isinstance(body.get("items"), dict)
            else body.get("items", [])
        )
        if isinstance(items_raw, dict):
            items_raw = [items_raw]

        result = []
        for item in items_raw:
            raw = self._parse_mdcs_item(item)
            if raw:
                result.append(raw)
        return result

    def _parse_mdcs_item(self, item: dict) -> dict | None:
        """MDCS API 응답 항목을 RawItem으로 변환."""
        try:
            # 공통 필드 (API 응답 키는 실제 API 문서 기준)
            recall_no = (
                item.get("recallNo")
                or item.get("rcllNo")
                or item.get("seq")
                or ""
            )
            product_name = (
                item.get("productName")
                or item.get("prdNm")
                or item.get("itemNm")
                or "알 수 없는 제품"
            )
            reason = (
                item.get("recallReason")
                or item.get("rcllRsn")
                or item.get("hazardContent")
                or ""
            )
            manufacturer = (
                item.get("manufacturer")
                or item.get("mnftrNm")
                or item.get("brandNm")
                or ""
            )
            category = (
                item.get("productCategory")
                or item.get("prdCatNm")
                or item.get("catNm")
                or ""
            )
            recall_date = (
                item.get("recallDate")
                or item.get("rcllDt")
                or item.get("regDt")
                or ""
            )
            detail_url = (
                item.get("detailUrl")
                or item.get("url")
                or _SK_RECALL_BASE_URL
            )

            # source_id는 카테고리에 따라 결정
            source = _resolve_source_id(category)

            ext_id = recall_no or _make_hash(product_name + recall_date)
            title = f"[리콜] {product_name}"
            if reason:
                title += f" — {reason[:60]}"

            body_parts = [f"제품명: {product_name}"]
            if reason:
                body_parts.append(f"리콜 사유: {reason}")
            if category:
                body_parts.append(f"품목: {category}")
            if manufacturer:
                body_parts.append(f"제조/수입사: {manufacturer}")
            if recall_date:
                body_parts.append(f"리콜일: {recall_date}")

            published_at = _parse_date(recall_date)

            return self._raw_item(
                source_id=source,
                external_id=str(ext_id),
                url=detail_url,
                title=title,
                body="\n".join(body_parts),
                category="recall",
                country="KR",
                published_at=published_at,
                marketplace=None,
                brand=manufacturer or None,
                extra={
                    "recall_no": recall_no,
                    "product_category": category,
                    "raw": item,
                },
            )
        except Exception as e:
            log.debug("[%s] MDCS 항목 파싱 실패: %s", self.source_id, e)
            return None

    # ── MFDS (식품의약품안전처) API ───────────────────────────────────────────

    def _fetch_mfds_api(self) -> list[dict]:
        """
        식품의약품안전처 리콜 API (C003).
        식품 외 의약품·의료기기·화장품 리콜 포함.
        """
        url = _MFDS_ENDPOINT.format(key=self.cfg.safety_korea_api_key)
        resp = self._get(url, timeout=30)
        data = resp.json()

        # 응답: {"C003": {"total_count": N, "row": [...]}}
        rows = []
        for key in data:
            inner = data[key]
            if isinstance(inner, dict):
                row_data = inner.get("row", [])
                if isinstance(row_data, list):
                    rows.extend(row_data)

        result = []
        for row in rows:
            raw = self._parse_mfds_item(row)
            if raw:
                result.append(raw)
        return result

    def _parse_mfds_item(self, item: dict) -> dict | None:
        """MFDS API 응답 항목 파싱."""
        try:
            product = item.get("PRDLST_NM") or item.get("PRDT_NM") or "알 수 없는 제품"
            reason = item.get("RECALL_REASON") or item.get("BNFDE") or ""
            company = item.get("ENTP_NM") or item.get("BSSH_NM") or ""
            category = item.get("PRDLST_TP") or item.get("PRDT_TP") or "식품/의약품"
            date_str = item.get("RECALL_DATE") or item.get("PRMS_DT") or ""
            seq = item.get("SEQ") or item.get("BNDE_NO") or ""

            ext_id = str(seq) if seq else _make_hash(product + date_str)
            title = f"[MFDS리콜] {product}"
            if reason:
                title += f" — {reason[:60]}"

            body_parts = [f"제품명: {product}"]
            if reason:
                body_parts.append(f"리콜 사유: {reason}")
            if category:
                body_parts.append(f"품목유형: {category}")
            if company:
                body_parts.append(f"업체명: {company}")
            if date_str:
                body_parts.append(f"회수일: {date_str}")

            return self._raw_item(
                source_id="safety_korea_mfds",
                external_id=ext_id,
                url=_SK_RECALL_BASE_URL,
                title=title,
                body="\n".join(body_parts),
                category="recall",
                country="KR",
                published_at=_parse_date(date_str),
                brand=company or None,
                extra={"product_category": category, "raw": item},
            )
        except Exception as e:
            log.debug("[%s] MFDS 항목 파싱 실패: %s", self.source_id, e)
            return None

    # ── HTML Fallback (소비자24) ──────────────────────────────────────────────

    def _fetch_html_fallback(self) -> list[dict]:
        """
        API 키 없을 때: 소비자24 리콜 목록 페이지 HTML 파싱.
        BeautifulSoup 없이 re 기반으로 파싱.
        """
        try:
            resp = self._get(_SK_RECALL_LIST_URL, timeout=30)
        except Exception as e:
            raise CollectorError(f"소비자24 HTML 요청 실패: {e}") from e

        html = resp.text
        result = []

        # 리콜 항목 패턴: 테이블 행 또는 리스트 항목 추출
        # 소비자24 리콜 목록은 <table> 기반 구조
        # <td> 내 제품명, 리콜번호, 날짜 등을 추출
        rows = re.findall(
            r'<tr[^>]*>(.*?)</tr>',
            html,
            re.DOTALL | re.IGNORECASE,
        )

        for row_html in rows:
            item = self._parse_html_row(row_html)
            if item:
                result.append(item)

        # HTML 파싱 실패 시 페이지 전체에서 리콜 관련 텍스트 블록 추출
        if not result:
            result = self._extract_recall_blocks_from_html(html)

        log.info("[%s] HTML 파싱 결과: %d건", self.source_id, len(result))
        return result

    def _parse_html_row(self, row_html: str) -> dict | None:
        """<tr> HTML에서 리콜 정보 추출."""
        try:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL | re.IGNORECASE)
            if len(cells) < 3:
                return None

            # 주석과 태그를 제거하고 HTML 엔티티를 복원한다.
            clean = [re.sub(r'<!--.*?-->', '', c, flags=re.DOTALL) for c in cells]
            clean = [html_lib.unescape(re.sub(r'<[^>]+>', '', c)).strip() for c in clean]
            clean = [re.sub(r'\s+', ' ', c).strip() for c in clean]

            # 빈 행 스킵
            if not any(clean):
                return None

            # 목록의 href는 항상 "#none"이다. 실제 상세 페이지 키는
            # onclick="goDetail(...)" 또는 hidden recallUid에 들어 있다.
            uid_match = re.search(
                r'name=["\']recallUid["\'][^>]*value=["\']([^"\']+)["\']',
                row_html,
                re.IGNORECASE,
            )
            if not uid_match:
                uid_match = re.search(
                    r'goDetail\(["\']([^"\']+)["\']\)',
                    row_html,
                    re.IGNORECASE,
                )
            recall_uid = uid_match.group(1).strip() if uid_match else ""

            if recall_uid:
                query = urlencode({"recallUid": recall_uid})
                detail_url = f"{_SK_RECALL_BASE_URL}/recall/ajax/recallBoard?{query}"
            else:
                link_match = re.search(
                    r'href=["\']([^"\']+)["\']',
                    row_html,
                    re.IGNORECASE,
                )
                href = link_match.group(1).strip() if link_match else ""
                if not href or href == "#none":
                    return None
                detail_url = (
                    href if href.startswith("http")
                    else urljoin(_SK_RECALL_BASE_URL, href)
                )

            # 날짜 컬럼 탐지 (YYYY-MM-DD 또는 YYYY.MM.DD 형식)
            date_str = ""
            product_name = clean[2] if len(clean) > 2 else ""
            recall_no = clean[3] if len(clean) > 3 else ""
            company = clean[4] if len(clean) > 4 else ""

            for cell in clean:
                if re.match(r'\d{4}[-./]\d{2}[-./]\d{2}', cell):
                    date_str = cell
                elif not recall_no and (
                    re.match(r'\d{4}-\d{4,}', cell)
                    or re.match(r'[A-Z]{2,}-\d+', cell)
                ):
                    recall_no = cell
                elif len(cell) > 5 and not product_name:
                    product_name = product_name or cell

            # 제품명 확인
            if not product_name:
                product_name = clean[1] if len(clean) > 1 else clean[0]
            if not product_name or len(product_name) < 2:
                return None

            ext_id = recall_uid or recall_no or _make_hash(product_name + date_str)
            title = f"[소비자24 리콜] {product_name}"
            body_parts = [f"제품명: {product_name}"]
            if recall_no:
                body_parts.append(f"리콜 번호: {recall_no}")
            if date_str:
                body_parts.append(f"리콜일: {date_str}")
            if company:
                body_parts.append(f"업체명: {company}")
            body_parts.append(f"출처: {detail_url}")

            return self._raw_item(
                source_id="safety_korea_kca",
                external_id=str(ext_id),
                url=detail_url,
                title=title,
                body="\n".join(body_parts),
                category="recall",
                country="KR",
                published_at=_parse_date(date_str),
                brand=company or None,
                extra={
                    "recall_uid": recall_uid,
                    "recall_no": recall_no,
                    "product_name": product_name,
                },
            )
        except Exception as e:
            log.debug("[%s] HTML 행 파싱 오류: %s", self.source_id, e)
            return None

    def _extract_recall_blocks_from_html(self, html: str) -> list[dict]:
        """
        테이블 파싱 실패 시 리콜 관련 텍스트 블록을 정규식으로 추출.
        소비자24 리콜 공고 제목 패턴: 제품명 + 리콜 키워드
        """
        result = []
        # 리콜 관련 링크+텍스트 패턴 추출
        pattern = re.compile(
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        seen = set()
        for href, anchor_text in pattern.findall(html):
            text = re.sub(r'<[^>]+>', '', anchor_text).strip()
            text = re.sub(r'\s+', ' ', text).strip()

            # 리콜 관련 키워드 필터
            if not any(kw in text for kw in ["리콜", "회수", "결함", "위해", "수거"]):
                continue
            if len(text) < 5:
                continue
            if text in seen:
                continue
            seen.add(text)

            detail_url = href if href.startswith("http") else urljoin(_SK_RECALL_BASE_URL, href)
            ext_id = _make_hash(text)

            result.append(self._raw_item(
                source_id="safety_korea_kca",
                external_id=ext_id,
                url=detail_url,
                title=f"[소비자24 리콜] {text}",
                body=f"제품명/공고: {text}\n출처: {detail_url}",
                category="recall",
                country="KR",
                published_at=None,
                brand=None,
            ))

        return result


# ── 유틸리티 ─────────────────────────────────────────────────────────────────

def _make_hash(text: str) -> str:
    """짧은 결정적 ID 생성 (외부 ID가 없을 때)."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _parse_date(date_str: str) -> str | None:
    """
    다양한 날짜 형식을 ISO 8601 UTC 문자열로 변환.
    파싱 실패 시 None 반환.
    """
    if not date_str:
        return None

    date_str = date_str.strip()
    # 숫자+구분자 형식 정규화
    normalized = re.sub(r'[./]', '-', date_str)
    normalized = re.sub(r'\s+', 'T', normalized, count=1)

    formats = [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y%m%d",
        "%Y년%m월%d일",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(normalized[:len(fmt)], fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def _resolve_source_id(category: str) -> str:
    """
    제품 카테고리에 따라 적절한 source_id 반환.

    - 식품/의약품/화장품 → safety_korea_mfds (식품의약품안전처)
    - 전기/전자/기계 → safety_korea_kats (국가기술표준원)
    - 기타 → safety_korea_kca (한국소비자원)
    """
    cat_lower = (category or "").lower()

    mfds_keywords = ["식품", "의약", "화장", "food", "drug", "cosmetic", "의료"]
    kats_keywords = ["전기", "전자", "기계", "가스", "안전기준", "kc", "electric", "battery"]

    if any(kw in cat_lower for kw in mfds_keywords):
        return "safety_korea_mfds"
    if any(kw in cat_lower for kw in kats_keywords):
        return "safety_korea_kats"
    return "safety_korea_kca"
