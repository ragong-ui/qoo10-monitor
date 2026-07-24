"""Tests for Slack notifier (offline — no real API calls)."""

import pytest
from unittest.mock import patch, MagicMock

from compliance_briefing.config import ComplianceConfig
from compliance_briefing.formatters import format_slack_message, format_alert_block
from compliance_briefing.slack_notifier import post_compliance_briefing


def _make_alert(severity="high", status="new", category="regulation", country="JP"):
    return {
        "alert_id": "test-alert-id",
        "run_id": "20260724T083000_abc123",
        "severity": severity,
        "status": status,
        "category": category,
        "country": country,
        "title_ko": "테스트 알림",
        "title_ja": "テストアラート",
        "summary_ko": "테스트 요약 내용입니다.",
        "summary_ja": "テストの概要です。",
        "source_url": "https://example.com/source",
        "brand": None,
        "marketplace": "Qoo10",
    }


def test_format_slack_message_no_alerts():
    blocks = format_slack_message("run123", [])
    assert any("No new compliance alerts" in str(b) for b in blocks)


def test_format_slack_message_with_critical():
    alerts = [_make_alert("critical", "new")]
    blocks = format_slack_message("run123", alerts)
    text = str(blocks)
    assert "Critical" in text or "critical" in text.lower() or "🚨" in text


def test_format_slack_message_dry_run_tag():
    blocks = format_slack_message("run123", [], dry_run=True)
    assert any("DRY RUN" in str(b) for b in blocks)


def test_format_slack_message_block_structure():
    alerts = [_make_alert("high", "new")]
    blocks = format_slack_message("run123", alerts)
    assert isinstance(blocks, list)
    for block in blocks:
        assert isinstance(block, dict)
        assert "type" in block


def test_format_alert_block_ko():
    alert = _make_alert()
    block = format_alert_block(alert, lang="ko")
    assert block["type"] == "section"
    text = block["text"]["text"]
    assert "테스트 알림" in text


def test_format_alert_block_ja():
    alert = _make_alert()
    block = format_alert_block(alert, lang="ja")
    text = block["text"]["text"]
    assert "テストアラート" in text


def test_slack_post_disabled_by_default():
    cfg = ComplianceConfig()
    cfg.slack_publish_enabled = False
    result = post_compliance_briefing(cfg, "run123", [_make_alert()])
    assert result is True  # Success (skipped)


def test_slack_post_no_token():
    cfg = ComplianceConfig()
    cfg.slack_publish_enabled = True
    cfg.slack_bot_token = ""
    result = post_compliance_briefing(cfg, "run123", [_make_alert()])
    assert result is False


def test_slack_post_calls_api():
    cfg = ComplianceConfig()
    cfg.slack_publish_enabled = True
    cfg.slack_bot_token = "xoxb-fake-token"
    cfg.slack_compliance_channel = "C0BKB580VBM"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"ok": True}

    with patch("compliance_briefing.slack_notifier.requests.post", return_value=mock_resp):
        result = post_compliance_briefing(cfg, "run123", [_make_alert()])
    assert result is True


def test_slack_api_error_returns_false():
    cfg = ComplianceConfig()
    cfg.slack_publish_enabled = True
    cfg.slack_bot_token = "xoxb-fake-token"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"ok": False, "error": "invalid_auth"}

    with patch("compliance_briefing.slack_notifier.requests.post", return_value=mock_resp):
        result = post_compliance_briefing(cfg, "run123", [_make_alert()])
    assert result is False
