"""Qoo10 SNS 탐지 결과의 Case·근거·AI 2차 분석 공통 로직."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote


AI_LABELS = {
    "PURCHASE_COUNTERFEIT",
    "GENERAL_WARNING",
    "AD_OR_AFFILIATE",
    "UNRELATED",
    "INSUFFICIENT_CONTENT",
    "PENDING",
    "ERROR",
}
DEFAULT_AI_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
}



_PRODUCT_PATTERNS = [
    re.compile(r"[?&]goodscode=(\d+)", re.IGNORECASE),
    re.compile(r"/g/(\d+)(?:[/?#]|$)", re.IGNORECASE),
    re.compile(r"/item/(?:[^/?#]+/)*(\d+)(?:[/?#]|$)", re.IGNORECASE),
    re.compile(r"/(Q\d{7,})(?:[/?#]|$)", re.IGNORECASE),
]

_SPACE_RE = re.compile(r"\s+")


def normalize_text(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()


def extract_product_number(value: str) -> str:
    """Qoo10 상품 URL/텍스트에서 goodscode 또는 Q코드를 추출한다."""
    text = unquote(str(value or ""))
    if text.lower() in {"", "null", "none", "なし"}:
        return ""
    for pattern in _PRODUCT_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).upper()
    return ""


def build_case_id(qoo10_link: str, source_url: str) -> tuple[str, str]:
    """상품번호가 있으면 상품 Case, 없으면 URL Case를 만든다."""
    product_number = extract_product_number(qoo10_link)
    if product_number:
        return f"PRODUCT:{product_number}", product_number

    normalized_url = str(source_url or "").strip().split("#", 1)[0]
    digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:16]
    return f"URL:{digest}", ""


def extract_detection_evidence(
    text: str,
    fraud_words: list[str],
    *,
    window: int = 120,
) -> str:
    """탐지 키워드 주변 문맥을 사람이 빠르게 읽을 수 있도록 잘라낸다."""
    normalized = normalize_text(text)
    if not normalized:
        return ""

    positions: list[tuple[int, str]] = []
    lower_text = normalized.lower()
    for word in fraud_words:
        index = lower_text.find(word.lower())
        if index >= 0:
            positions.append((index, word))

    if not positions:
        return normalized[: min(len(normalized), window * 2)]

    positions.sort(key=lambda item: item[0])
    index, _word = positions[0]

    qoo10_index = lower_text.rfind("qoo10", max(0, index - window), index + window)
    center = index if qoo10_index < 0 else (index + qoo10_index) // 2
    start = max(0, center - window)
    end = min(len(normalized), center + window)
    excerpt = normalized[start:end].strip()
    if start > 0:
        excerpt = "… " + excerpt
    if end < len(normalized):
        excerpt += " …"
    return excerpt


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI 응답에서 JSON 객체를 찾을 수 없습니다.")
    return json.loads(cleaned[start : end + 1])


@dataclass
class AiResult:
    label: str
    confidence: str
    reason: str
    evidence: str
    model: str


class SnsAiClassifier:
    """환경변수로 켜는 선택형 AI 분류기. 미설정 시 PENDING으로 안전하게 보존한다."""

    def __init__(self) -> None:
        self.provider = os.getenv("SNS_AI_PROVIDER", "disabled").strip().lower()
        self.model = (
            os.getenv("SNS_AI_MODEL", "").strip()
            or DEFAULT_AI_MODELS.get(self.provider, "")
        )
        self.max_rows = max(0, int(os.getenv("SNS_AI_MAX_ROWS", "100")))
        self.max_attempts = max(1, int(os.getenv("SNS_AI_MAX_ATTEMPTS", "3")))
        self.timeout = max(10, int(os.getenv("SNS_AI_TIMEOUT_SECONDS", "45")))
        self.retry_base_seconds = max(
            0.0,
            float(os.getenv("SNS_AI_RETRY_BASE_SECONDS", "2")),
        )
        self._client: Any = None

    @property
    def configured(self) -> bool:
        return self.provider in {"anthropic", "openai"} and bool(self.model)

    @property
    def api_key(self) -> str:
        if self.provider == "anthropic":
            return os.getenv("ANTHROPIC_API_KEY", "").strip()
        if self.provider == "openai":
            return os.getenv("OPENAI_API_KEY", "").strip()
        return ""

    @property
    def enabled(self) -> bool:
        return self.configured and bool(self.api_key)

    def pending(self, evidence: str) -> AiResult:
        if not self.configured:
            reason = "AI 2차 분석 미설정"
        elif not self.api_key:
            reason = f"{self.provider} API 키 미설정"
        else:
            reason = "AI 분석 대기"
        return AiResult(
            label="PENDING",
            confidence="",
            reason=reason,
            evidence=evidence,
            model=self.model if self.configured else "",
        )

    def analyze(
        self, *, text: str, source: str, keyword: str, evidence: str,
        source_url: str = "", qoo10_link: str = "",
    ) -> AiResult:
        if not self.enabled:
            return self.pending(evidence)
        if len(normalize_text(text)) < 20:
            return AiResult(
                label="INSUFFICIENT_CONTENT",
                confidence="0",
                reason="판정에 필요한 게시물 문맥이 부족합니다.",
                evidence=evidence,
                model=self.model,
            )

        prompt = self._prompt(
            text=text,
            source=source,
            keyword=keyword,
            source_url=source_url,
            qoo10_link=qoo10_link,
        )
        try:
            raw = self._call_with_retry(prompt)
            parsed = _extract_json_object(raw)
            label = str(parsed.get("label", "")).strip().upper()
            if label not in AI_LABELS - {"PENDING", "ERROR"}:
                raise ValueError(f"허용되지 않은 AI label: {label}")
            confidence = float(parsed.get("confidence", 0))
            confidence = max(0.0, min(1.0, confidence))
            return AiResult(
                label=label,
                confidence=f"{confidence:.2f}",
                reason=normalize_text(parsed.get("reason", ""))[:300],
                evidence=normalize_text(parsed.get("evidence", ""))[:300] or evidence,
                model=self.model,
            )
        except Exception as exc:
            return AiResult(
                label="ERROR",
                confidence="",
                reason=f"AI 분석 실패: {type(exc).__name__}",
                evidence=evidence,
                model=self.model,
            )

    def _call_with_retry(self, prompt: str) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._call(prompt)
            except Exception as exc:
                last_error = exc
                if attempt < self.max_attempts:
                    delay = self.retry_base_seconds * (2 ** (attempt - 1))
                    time.sleep(delay)
        if last_error is not None:
            raise last_error
        raise RuntimeError("AI 분석 호출 실패")

    def _call(self, prompt: str) -> str:
        if self.provider == "anthropic":
            if self._client is None:
                from anthropic import Anthropic

                self._client = Anthropic(api_key=self.api_key)
            response = self._client.messages.create(
                model=self.model,
                max_tokens=500,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
                timeout=self.timeout,
            )
            return "".join(
                block.text for block in response.content if hasattr(block, "text")
            )

        if self.provider == "openai":
            if self._client is None:
                from openai import OpenAI

                self._client = OpenAI(api_key=self.api_key)
            response = self._client.chat.completions.create(
                model=self.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": "Return only the requested JSON object.",
                    },
                    {"role": "user", "content": prompt},
                ],
                timeout=self.timeout,
            )
            return response.choices[0].message.content or ""

        raise RuntimeError(f"지원하지 않는 SNS_AI_PROVIDER: {self.provider}")

    @staticmethod
    def _prompt(
        *, text: str, source: str, keyword: str, source_url: str, qoo10_link: str,
    ) -> str:
        return f"""
당신은 Qoo10 위조품 SNS 모니터링의 2차 판정자입니다.
게시물이 실제로 Qoo10에서 구매·수령한 상품이 위조품·가품이었다는 부정적 경험인지 판정하세요.
이 판정은 관리자 검토를 돕는 참고 정보이며 최종 조치 판단이 아닙니다.
작성자 자신의 경험과 타인의 글 인용·검색 스니펫·일반 질문을 구분하세요.
일본어, 한국어, 영어 문맥을 그대로 해석하세요.

분류:
- PURCHASE_COUNTERFEIT: 작성자가 Qoo10에서 구매·수령했고 해당 상품이 가품이라고 명시한 실제 경험 또는 구체적 피해 주장
- GENERAL_WARNING: 구별법, 일반 경고, 정보, 질문만 있고 구매 피해 경험은 아님
- AD_OR_AFFILIATE: 광고, PR, 제휴, 판매 홍보
- UNRELATED: Qoo10 위조품 문제와 문맥상 무관
- INSUFFICIENT_CONTENT: 원문이 짧거나 접근 불가로 판단 불충분

주의:
- 검색 키워드가 포함되었다는 이유만으로 PURCHASE_COUNTERFEIT로 분류하지 마세요.
- 상품 링크만 있고 가품 피해 주장이 없으면 PURCHASE_COUNTERFEIT가 아닙니다.
- 추측하지 말고 제공된 텍스트만 사용하세요.
- Qoo10 구매 사실과 가품 주장이 둘 다 동일한 게시물 문맥에 있어야 PURCHASE_COUNTERFEIT입니다.
- 단순 질문, 구별법, 중국산·중국배송 언급, 교환/양도 조건, 상품 링크 첨부만으로는 PURCHASE_COUNTERFEIT가 아닙니다.
- 검색결과 스니펫이 서로 다른 문장을 합친 것으로 보이면 INSUFFICIENT_CONTENT 또는 UNRELATED로 분류하세요.
- 광고·협찬·PR·affiliate·판매 홍보는 AD_OR_AFFILIATE입니다.
- 판단 근거 문구가 없거나 원문 접근이 불가능하면 INSUFFICIENT_CONTENT입니다.

source: {source}
search_keyword: {keyword}
source_url: {source_url}
qoo10_product_url: {qoo10_link}
content:
{normalize_text(text)[:3000]}

다음 JSON만 반환:
{{
  "label": "위 5개 중 하나",
  "confidence": 0.0,
  "reason": "짧은 한국어 설명",
  "evidence": "판정 근거가 된 원문 구절"
}}
""".strip()


def enrich_rows(
    rows: list[dict[str, Any]],
    fraud_words: list[str],
    *,
    source: str,
) -> list[dict[str, Any]]:
    """탐지 결과에 상품 Case, 근거 문장, 선택형 AI 판정을 추가한다."""
    classifier = SnsAiClassifier()

    for index, row in enumerate(rows):
        case_id, product_number = build_case_id(
            str(row.get("qoo10_link", "")),
            str(row.get("url", "")),
        )
        evidence = extract_detection_evidence(
            str(row.get("summary", "")),
            fraud_words,
        )
        row["product_number"] = product_number
        row["case_id"] = case_id
        row["detection_evidence"] = evidence

        keyword = str(row.get("keyword") or row.get("query") or "")
        if index < classifier.max_rows:
            result = classifier.analyze(
                text=str(row.get("summary", "")),
                source=source,
                keyword=keyword,
                evidence=evidence,
                source_url=str(row.get("url", "")),
                qoo10_link=str(row.get("qoo10_link", "")),
            )
        else:
            result = classifier.pending(evidence)

        row["ai_label"] = result.label
        row["ai_confidence"] = result.confidence
        row["ai_reason"] = result.reason
        row["ai_evidence"] = result.evidence
        row["ai_model"] = result.model

    return rows
