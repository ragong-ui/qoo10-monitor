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
