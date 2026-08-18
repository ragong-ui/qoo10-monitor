from __future__ import annotations

import json
from types import SimpleNamespace

import sns_enrichment
from sns_enrichment import (
    SnsAiClassifier,
    build_case_id,
    enrich_rows,
    extract_detection_evidence,
    extract_product_number,
)


def test_extract_product_number_from_qoo10_urls():
    assert extract_product_number("https://www.qoo10.jp/g/627681006") == "627681006"
    assert (
        extract_product_number(
            "https://special.qoo10.jp/gmkt.inc/Goods/Goods.aspx?goodscode=1097671463"
        )
        == "1097671463"
    )
    assert (
        extract_product_number("https://m.qoo10.jp/su/1466767450/Q206693446")
        == "Q206693446"
    )


def test_build_case_id_groups_the_same_product():
    first = build_case_id(
        "https://www.qoo10.jp/g/627681006",
        "https://www.instagram.com/p/first/",
    )
    second = build_case_id(
        "https://www.qoo10.jp/g/627681006",
        "https://x.com/user/status/2",
    )
    assert first == ("PRODUCT:627681006", "627681006")
    assert second == first


def test_build_case_id_falls_back_to_stable_source_url_hash():
    first = build_case_id("", "https://example.com/post/1#section")
    second = build_case_id("", "https://example.com/post/1")
    other = build_case_id("", "https://example.com/post/2")
    assert first == second
    assert first[0].startswith("URL:")
    assert first != other


def test_detection_evidence_keeps_fraud_context():
    text = "앞 문장 " * 40 + "Qoo10で購入した商品は偽物でした。" + " 뒤 문장" * 40
    evidence = extract_detection_evidence(text, ["偽物"])
    assert "Qoo10" in evidence
    assert "偽物" in evidence
    assert len(evidence) < len(text)


def test_ai_classifier_is_safe_pending_when_disabled(monkeypatch):
    monkeypatch.setenv("SNS_AI_PROVIDER", "disabled")
    monkeypatch.delenv("SNS_AI_MODEL", raising=False)
    classifier = SnsAiClassifier()
    result = classifier.analyze(
        text="Qoo10で購入した商品は偽物でした",
        source="google",
        keyword="Qoo10 偽物",
        evidence="Qoo10で購入した商品は偽物でした",
    )
    assert result.label == "PENDING"
    assert result.model == ""


def test_enrich_rows_adds_case_evidence_and_pending_ai(monkeypatch):
    monkeypatch.setenv("SNS_AI_PROVIDER", "disabled")
    rows = [{
        "date": "2026-07-26",
        "keyword": "Qoo10 偽物",
        "url": "https://x.com/user/status/1",
        "summary": "Qoo10で購入した商品は偽物でした",
        "qoo10_link": "https://www.qoo10.jp/g/627681006",
        "likelihood": "HIGH",
    }]
    enriched = enrich_rows(rows, ["偽物"], source="google")
    assert enriched[0]["product_number"] == "627681006"
    assert enriched[0]["case_id"] == "PRODUCT:627681006"
    assert "偽物" in enriched[0]["detection_evidence"]
    assert enriched[0]["ai_label"] == "PENDING"


def test_anthropic_without_api_key_stays_pending(monkeypatch):
    monkeypatch.setenv("SNS_AI_PROVIDER", "anthropic")
    monkeypatch.delenv("SNS_AI_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    classifier = SnsAiClassifier()
    result = classifier.analyze(
        text="Qoo10で購入した商品は偽物でした。返金を依頼しました。",
        source="google",
        keyword="Qoo10 偽物",
        evidence="Qoo10で購入した商品は偽物でした。",
    )

    assert classifier.model == "claude-sonnet-4-6"
    assert classifier.enabled is False
    assert result.label == "PENDING"
    assert result.reason == "anthropic API 키 미설정"
    assert result.model == "claude-sonnet-4-6"


def test_ai_classifier_retries_transient_failures(monkeypatch):
    monkeypatch.setenv("SNS_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("SNS_AI_MODEL", "test-sonnet")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("SNS_AI_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("SNS_AI_RETRY_BASE_SECONDS", "0")
    classifier = SnsAiClassifier()
    calls = []

    def fake_call(prompt):
        calls.append(prompt)
        if len(calls) < 3:
            raise TimeoutError("temporary")
        return (
            '{"label":"PURCHASE_COUNTERFEIT","confidence":0.93,'
            '"reason":"Qoo10 구매 가품 경험","evidence":"偽物でした"}'
        )

    monkeypatch.setattr(classifier, "_call", fake_call)
    result = classifier.analyze(
        text="Qoo10で購入して受け取った商品は偽物でした。返金を依頼しました。",
        source="x",
        keyword="Qoo10 偽物",
        evidence="Qoo10で購入して受け取った商品は偽物でした。",
        source_url="https://x.com/user/status/1",
        qoo10_link="https://www.qoo10.jp/g/627681006",
    )

    assert len(calls) == 3
    assert result.label == "PURCHASE_COUNTERFEIT"
    assert result.confidence == "0.93"
    assert result.model == "test-sonnet"
    assert "source_url: https://x.com/user/status/1" in calls[-1]
    assert "qoo10_product_url: https://www.qoo10.jp/g/627681006" in calls[-1]


def test_claude_cli_uses_sonnet_structured_output_without_api_key(monkeypatch):
    monkeypatch.setenv("SNS_AI_PROVIDER", "claude_cli")
    monkeypatch.delenv("SNS_AI_MODEL", raising=False)
    monkeypatch.setenv("SNS_AI_CLAUDE_BARE", "true")
    monkeypatch.setenv(
        "SNS_AI_CLAUDE_API_KEY_HELPER",
        "npx @ebay/claude-code-token@latest get_token",
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        sns_enrichment.shutil,
        "which",
        lambda _command: r"C:\Users\ragong\.local\bin\claude.exe",
    )
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "is_error": False,
                "structured_output": {
                    "label": "PURCHASE_COUNTERFEIT",
                    "confidence": 0.96,
                    "reason": "Qoo10 구매 후 가품이라고 명시했습니다.",
                    "evidence": "Qoo10で購入した商品は偽物でした",
                },
            }),
            stderr="",
        )

    monkeypatch.setattr(sns_enrichment.subprocess, "run", fake_run)
    classifier = SnsAiClassifier()
    result = classifier.analyze(
        text="Qoo10で購入して受け取った商品は偽物でした。返金を依頼しました。",
        source="google",
        keyword="Qoo10 偽物",
        evidence="Qoo10で購入した商品は偽物でした",
    )

    assert classifier.enabled is True
    assert classifier.model == "claude-sonnet-4-6"
    assert result.label == "PURCHASE_COUNTERFEIT"
    assert result.confidence == "0.96"
    assert "--bare" in captured["args"]
    assert captured["args"][captured["args"].index("--model") + 1] == "claude-sonnet-4-6"
    settings = json.loads(captured["args"][captured["args"].index("--settings") + 1])
    assert settings["apiKeyHelper"].startswith("npx @ebay/claude-code-token")
    assert captured["env"]["NODE_OPTIONS"].endswith("--use-system-ca")
    assert "ANTHROPIC_API_KEY" not in captured["env"]


def test_claude_cli_without_executable_stays_pending(monkeypatch):
    monkeypatch.setenv("SNS_AI_PROVIDER", "claude_cli")
    monkeypatch.delenv("SNS_AI_MODEL", raising=False)
    monkeypatch.setattr(sns_enrichment.shutil, "which", lambda _command: None)

    classifier = SnsAiClassifier()
    result = classifier.analyze(
        text="Qoo10で購入した商品は偽物でした。返金を依頼しました。",
        source="google",
        keyword="Qoo10 偽物",
        evidence="偽物でした",
    )

    assert classifier.enabled is False
    assert result.label == "PENDING"
    assert result.reason == "Claude Code CLI 미설치"

def test_claude_cli_batch_maps_each_row_and_marks_missing(monkeypatch):
    monkeypatch.setenv("SNS_AI_PROVIDER", "claude_cli")
    monkeypatch.delenv("SNS_AI_MODEL", raising=False)
    monkeypatch.setattr(sns_enrichment.shutil, "which", lambda _command: "claude.exe")
    classifier = SnsAiClassifier()
    captured = {}

    def fake_batch_call(prompt, schema=None):
        captured["prompt"] = prompt
        captured["schema"] = schema
        return json.dumps({
            "results": [{
                "row_id": "google:2",
                "label": "PURCHASE_COUNTERFEIT",
                "confidence": 0.94,
                "reason": "Qoo10 구매 가품 경험",
                "evidence": "Qoo10で購入した商品は偽物でした",
            }]
        })

    monkeypatch.setattr(classifier, "_call_claude_cli", fake_batch_call)
    items = [
        {
            "row_id": "google:2",
            "text": "Qoo10で購入して受け取った商品は偽物でした。返金を依頼しました。",
            "source": "google",
            "keyword": "Qoo10 偽物",
            "evidence": "偽物でした",
            "source_url": "https://example.com/2",
            "qoo10_link": "",
        },
        {
            "row_id": "x:3",
            "text": "Qoo10の商品について偽物かどうか一般的な見分け方を質問しています。",
            "source": "x",
            "keyword": "Qoo10 偽物",
            "evidence": "見分け方",
            "source_url": "https://example.com/3",
            "qoo10_link": "",
        },
    ]

    results = classifier.analyze_batch(items)

    assert results["google:2"].label == "PURCHASE_COUNTERFEIT"
    assert results["google:2"].model == "claude-sonnet-4-6"
    assert results["x:3"].label == "ERROR"
    assert "누락" in results["x:3"].reason
    assert captured["schema"] is sns_enrichment.AI_BATCH_OUTPUT_SCHEMA
    assert '"row_id": "google:2"' in captured["prompt"]

def test_ai_prompt_requires_same_context_purchase_and_counterfeit():
    prompt = SnsAiClassifier._prompt(
        text="Qoo10 偽物",
        source="google",
        keyword="Qoo10 偽物",
        source_url="https://example.com/post",
        qoo10_link="",
    )

    assert "둘 다 동일한 게시물 문맥" in prompt
    assert "최종 조치 판단이 아닙니다" in prompt
    assert "중국산·중국배송 언급" in prompt
