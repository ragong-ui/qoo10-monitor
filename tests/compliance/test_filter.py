"""Tests for post-collection relevance filtering."""

from compliance_briefing.filter import filter_items


def _item(title: str, source_id: str = "brave_news", **overrides) -> dict:
    item = {
        "source_id": source_id,
        "external_id": title,
        "url": "https://news.example.com/article",
        "title": title,
        "body": "",
    }
    item.update(overrides)
    return item


def test_news_requires_strong_signal_or_action_subject_pair():
    items = [
        _item("消費者庁、通販会社に措置命令"),
        _item("Qoo10への規制強化を政府が検討"),
        _item("정부, Qoo10 개인정보 규제 강화"),
        _item("Qoo10 운영 불법제품 단속 강화, 소비자 당국 협력"),
        _item("電気掃除機をリコール、発火のおそれ"),
        _item("Qoo10、夏のメガセールを開催"),
        _item("消費者庁、来年度の定員を4割増員"),
        _item("BASE、越境ECの海外配送を代行"),
        _item("규제 산업의 향후 시장 전망 보고서"),
    ]

    kept, counts = filter_items(items)

    assert [item["title"] for item in kept] == [
        "消費者庁、通販会社に措置命令",
        "Qoo10への規制強化を政府が検討",
        "정부, Qoo10 개인정보 규제 강화",
        "Qoo10 운영 불법제품 단속 강화, 소비자 당국 협력",
        "電気掃除機をリコール、発火のおそれ",
    ]
    assert counts["no_keyword"] == 4


def test_trusted_source_bypasses_news_relevance_rule():
    item = _item(
        "公式発表のお知らせです",
        source_id="caa",
        url="https://www.caa.go.jp/notice/entry/123456/",
    )

    kept, counts = filter_items([item])

    assert kept == [item]
    assert counts["no_keyword"] == 0


def test_domain_blocklist_applies_to_subdomains():
    item = _item(
        "Qoo10への規制強化を政府が検討",
        url="https://campaign.media.qoo10.jp/article/1",
    )

    kept, counts = filter_items([item])

    assert kept == []
    assert counts["blocked_domain"] == 1


def test_invalid_or_placeholder_source_url_is_rejected():
    items = [
        _item("消費者庁、通販会社に措置命令", url="https://www.safetykorea.kr#none"),
        _item("消費者庁、通販会社に措置命令", url="javascript:alert(1)"),
    ]

    kept, counts = filter_items(items)

    assert kept == []
    assert counts["invalid_url"] == 2


def test_mojibake_is_rejected_before_database_storage():
    item = _item("æ¶ˆè²»è€…åºãŒæŽªç½®å‘½ä»¤")

    kept, counts = filter_items([item])

    assert kept == []
    assert counts["invalid_text"] == 1
