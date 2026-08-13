from __future__ import annotations

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

    assert classifier.model == "claude-haiku-4-5-20251001"
    assert classifier.enabled is False
    assert result.label == "PENDING"
    assert result.reason == "anthropic API 키 미설정"
    assert result.model == "claude-haiku-4-5-20251001"


def test_ai_classifier_retries_transient_failures(monkeypatch):
    monkeypatch.setenv("SNS_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("SNS_AI_MODEL", "test-haiku")
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
    assert result.model == "test-haiku"
    assert "source_url: https://x.com/user/status/1" in calls[-1]
    assert "qoo10_product_url: https://www.qoo10.jp/g/627681006" in calls[-1]


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
