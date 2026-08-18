"""Qoo10 SNS 탐지 결과의 Case·근거·AI 2차 분석 공통 로직."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
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
    "claude_cli": "claude-sonnet-4-6",
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o-mini",
}

AI_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {
            "type": "string",
            "enum": [
                "PURCHASE_COUNTERFEIT",
                "GENERAL_WARNING",
                "AD_OR_AFFILIATE",
                "UNRELATED",
                "INSUFFICIENT_CONTENT",
            ],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "maxLength": 300},
        "evidence": {"type": "string", "maxLength": 300},
    },
    "required": ["label", "confidence", "reason", "evidence"],
    "additionalProperties": False,
}

AI_BATCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "row_id": {"type": "string"},
                    **AI_OUTPUT_SCHEMA["properties"],
                },
                "required": ["row_id", *AI_OUTPUT_SCHEMA["required"]],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
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
        self.cli_command = os.getenv("SNS_AI_CLAUDE_CLI", "claude").strip() or "claude"
        self.cli_bare = os.getenv("SNS_AI_CLAUDE_BARE", "true").strip().lower() in {
            "1", "true", "yes",
        }
        self.cli_api_key_helper = os.getenv(
            "SNS_AI_CLAUDE_API_KEY_HELPER", ""
        ).strip()
        self.cli_workdir = os.getenv("SNS_AI_CLAUDE_WORKDIR", "").strip() or os.getcwd()
        self.cli_path = shutil.which(self.cli_command)
        self._client: Any = None

    @property
    def configured(self) -> bool:
        return self.provider in {"claude_cli", "anthropic", "openai"} and bool(self.model)

    @property
    def api_key(self) -> str:
        if self.provider == "anthropic":
            return os.getenv("ANTHROPIC_API_KEY", "").strip()
        if self.provider == "openai":
            return os.getenv("OPENAI_API_KEY", "").strip()
        return ""

    @property
    def enabled(self) -> bool:
        if self.provider == "claude_cli":
            return self.configured and bool(self.cli_path)
        return self.configured and bool(self.api_key)

    def pending(self, evidence: str) -> AiResult:
        if not self.configured:
            reason = "AI 2차 분석 미설정"
        elif self.provider == "claude_cli" and not self.cli_path:
            reason = "Claude Code CLI 미설치"
        elif self.provider != "claude_cli" and not self.api_key:
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
        max_attempts = 1 if self.provider == "claude_cli" else self.max_attempts
        for attempt in range(1, max_attempts + 1):
            try:
                return self._call(prompt)
            except Exception as exc:
                last_error = exc
                if attempt < max_attempts:
                    delay = self.retry_base_seconds * (2 ** (attempt - 1))
                    time.sleep(delay)
        if last_error is not None:
            raise last_error
        raise RuntimeError("AI 분석 호출 실패")

    def _call(self, prompt: str) -> str:
        if self.provider == "claude_cli":
            return self._call_claude_cli(prompt)

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

    def _call_claude_cli(
        self,
        prompt: str,
        schema: dict[str, Any] | None = None,
    ) -> str:
        if not self.cli_path:
            raise RuntimeError("Claude Code CLI를 찾을 수 없습니다.")

        command = [self.cli_path]
        if self.cli_bare:
            command.append("--bare")
            if self.cli_api_key_helper:
                command.extend([
                    "--settings",
                    json.dumps(
                        {"apiKeyHelper": self.cli_api_key_helper},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ])
        command.extend([
            "-p",
            "--model", self.model,
            "--output-format", "json",
            "--json-schema", json.dumps(schema or AI_OUTPUT_SCHEMA, separators=(",", ":")),
            "--tools", "",
            "--permission-mode", "dontAsk",
            "--no-session-persistence",
            "--max-turns", "1",
        ])

        env = os.environ.copy()
        env["CLAUDE_CODE_SKIP_PROMPT_HISTORY"] = "1"
        node_options = env.get("NODE_OPTIONS", "").strip()
        if "--use-system-ca" not in node_options.split():
            env["NODE_OPTIONS"] = (node_options + " --use-system-ca").strip()

        run_kwargs: dict[str, Any] = {
            "args": command,
            "input": prompt,
            "text": True,
            "encoding": "utf-8",
            "capture_output": True,
            "timeout": self.timeout,
            "cwd": self.cli_workdir,
            "env": env,
            "check": False,
        }
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        completed = subprocess.run(**run_kwargs)
        if completed.returncode != 0:
            raise RuntimeError(
                f"Claude Code CLI 호출 실패 (exit={completed.returncode})"
            )

        envelope = json.loads(completed.stdout or "{}")
        if envelope.get("is_error"):
            raise RuntimeError("Claude Code CLI가 오류 응답을 반환했습니다.")
        structured = envelope.get("structured_output")
        if isinstance(structured, dict):
            return json.dumps(structured, ensure_ascii=False)
        result = envelope.get("result")
        if isinstance(result, str) and result.strip():
            return result
        raise ValueError("Claude Code CLI 응답에 분석 결과가 없습니다.")

    def analyze_batch(self, items: list[dict[str, Any]]) -> dict[str, AiResult]:
        """여러 공개 게시물을 한 번의 Sonnet 호출로 판정하고 row_id별 결과를 반환한다."""
        if not items:
            return {}
        if self.provider != "claude_cli":
            return {
                str(item["row_id"]): self.analyze(
                    text=str(item.get("text", "")),
                    source=str(item.get("source", "")),
                    keyword=str(item.get("keyword", "")),
                    evidence=str(item.get("evidence", "")),
                    source_url=str(item.get("source_url", "")),
                    qoo10_link=str(item.get("qoo10_link", "")),
                )
                for item in items
            }

        results: dict[str, AiResult] = {}
        ready: list[dict[str, Any]] = []
        for item in items:
            row_id = str(item["row_id"])
            evidence = str(item.get("evidence", ""))
            if len(normalize_text(item.get("text", ""))) < 20:
                results[row_id] = AiResult(
                    label="INSUFFICIENT_CONTENT",
                    confidence="0",
                    reason="판정에 필요한 게시물 문맥이 부족합니다.",
                    evidence=evidence,
                    model=self.model,
                )
            else:
                ready.append(item)

        if not ready:
            return results

        expected = {str(item["row_id"]): item for item in ready}
        try:
            raw = self._call_claude_cli(
                self._batch_prompt(ready),
                AI_BATCH_OUTPUT_SCHEMA,
            )
            parsed = _extract_json_object(raw)
            entries = parsed.get("results", [])
            if not isinstance(entries, list):
                raise ValueError("AI 배치 응답 results가 배열이 아닙니다.")

            seen: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                row_id = str(entry.get("row_id", ""))
                if row_id not in expected or row_id in seen:
                    continue
                label = str(entry.get("label", "")).strip().upper()
                if label not in AI_LABELS - {"PENDING", "ERROR"}:
                    continue
                confidence = max(0.0, min(1.0, float(entry.get("confidence", 0))))
                evidence = normalize_text(entry.get("evidence", ""))[:300]
                results[row_id] = AiResult(
                    label=label,
                    confidence=f"{confidence:.2f}",
                    reason=normalize_text(entry.get("reason", ""))[:300],
                    evidence=evidence or str(expected[row_id].get("evidence", "")),
                    model=self.model,
                )
                seen.add(row_id)

            for row_id, item in expected.items():
                if row_id not in results:
                    results[row_id] = AiResult(
                        label="ERROR",
                        confidence="",
                        reason="AI 배치 응답에서 해당 행 결과가 누락되었습니다.",
                        evidence=str(item.get("evidence", "")),
                        model=self.model,
                    )
        except Exception as exc:
            for row_id, item in expected.items():
                results[row_id] = AiResult(
                    label="ERROR",
                    confidence="",
                    reason=f"AI 배치 분석 실패: {type(exc).__name__}",
                    evidence=str(item.get("evidence", "")),
                    model=self.model,
                )
        return results

    @staticmethod
    def _batch_prompt(items: list[dict[str, Any]]) -> str:
        records = [
            {
                "row_id": str(item["row_id"]),
                "source": str(item.get("source", "")),
                "search_keyword": str(item.get("keyword", "")),
                "source_url": str(item.get("source_url", "")),
                "qoo10_product_url": str(item.get("qoo10_link", "")),
                "content": normalize_text(item.get("text", ""))[:3000],
            }
            for item in items
        ]
        return (
            "당신은 Qoo10 위조품 SNS 모니터링의 2차 판정자입니다. "
            "각 record를 서로 독립적으로 판정하고 입력 row_id를 그대로 한 번씩 반환하세요.\n"
            "PURCHASE_COUNTERFEIT는 작성자가 Qoo10에서 구매·수령한 상품이 가품이라고 "
            "동일 게시물 문맥에서 구체적으로 주장한 경우에만 사용합니다.\n"
            "GENERAL_WARNING은 구별법·일반 경고·질문·정보, AD_OR_AFFILIATE는 광고·PR·제휴·판매 홍보, "
            "UNRELATED는 Qoo10 위조품 문제와 무관, INSUFFICIENT_CONTENT는 원문 부족·접근 불가입니다.\n"
            "검색 키워드, 상품 링크, 중국산·중국배송 언급만으로 PURCHASE_COUNTERFEIT로 판정하지 마세요. "
            "추측하지 말고 제공된 텍스트만 사용하세요.\n"
            "records:\n" + json.dumps(records, ensure_ascii=False)
        )

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
