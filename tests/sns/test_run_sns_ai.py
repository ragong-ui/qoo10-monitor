from __future__ import annotations

from sns_enrichment import AiResult
from run_sns_ai import SHEET_SPECS, analyze_row, pending_rows


def test_pending_rows_selects_pending_and_optional_errors():
    rows = [
        {"_row_index": "2", "AI 판정 / AI判定": "PENDING"},
        {"_row_index": "3", "AI 판정 / AI判定": ""},
        {"_row_index": "4", "AI 판정 / AI判定": "ERROR"},
        {"_row_index": "5", "AI 판정 / AI判定": "PURCHASE_COUNTERFEIT"},
        {"_row_index": "bad", "AI 판정 / AI判定": "PENDING"},
    ]

    assert [row["_row_index"] for row in pending_rows(rows)] == ["2", "3"]
    assert [
        row["_row_index"] for row in pending_rows(rows, include_errors=True)
    ] == ["2", "3", "4"]


def test_analyze_row_builds_safe_ai_update_payload():
    class FakeClassifier:
        def analyze(self, **kwargs):
            assert kwargs["source"] == "google"
            assert kwargs["source_url"] == "https://example.com/post/1"
            assert kwargs["qoo10_link"] == "https://www.qoo10.jp/g/627681006"
            return AiResult(
                label="PURCHASE_COUNTERFEIT",
                confidence="0.91",
                reason="구매한 상품이 가품이라는 구체적 경험",
                evidence="Qoo10で購入した商品は偽物でした",
                model="test-haiku",
            )

    row = {
        "_row_index": "12",
        "검색 키워드": "Qoo10 偽物",
        "URL": "https://example.com/post/1",
        "개요 / 概要": "Qoo10で購入した商品は偽物でした",
        "Qoo10 상품 / 商品P": "https://www.qoo10.jp/g/627681006",
        "탐지 근거 / 検知根拠": "Qoo10で購入した商品は偽物でした",
    }

    update = analyze_row(FakeClassifier(), row, SHEET_SPECS["google"])

    assert update["sheet"] == "Google モニタリング"
    assert update["row_index"] == 12
    assert update["source_url"] == "https://example.com/post/1"
    assert update["ai_label"] == "PURCHASE_COUNTERFEIT"
    assert update["ai_confidence"] == "0.91"
    assert update["ai_model"] == "test-haiku"


def test_code_gs_exposes_ai_batch_update_route():
    code = open("Code.gs", encoding="utf-8").read()
    assert 'payload.action === "ai_batch_update"' in code
    assert "function handleAiBatchUpdate(payload)" in code
    assert "source URL mismatch" in code
