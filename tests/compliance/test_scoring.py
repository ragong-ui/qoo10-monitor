"""Tests for scoring module."""

import pytest
from compliance_briefing.scoring import score_alert, source_status


def _item(source_id, category="regulation", title="", body=""):
    return {
        "source_id": source_id,
        "category": category,
        "title": title,
        "body": body,
        "country": "JP",
    }


def test_nite_scores_critical():
    sev, conf = score_alert(_item("nite", "recall", "製品リコール"))
    assert sev == "critical"
    assert conf == "high"


def test_caa_scores_high():
    sev, conf = score_alert(_item("caa", "regulation"))
    assert sev == "high"
    assert conf == "high"


def test_brave_news_scores_medium():
    sev, conf = score_alert(_item("brave_news", "competitor"))
    assert sev == "medium"
    assert conf == "medium"


def test_recall_keyword_boosts_severity():
    sev, conf = score_alert(_item("brave_news", "competitor", title="製品の緊急回収について"))
    # 緊急回収 is a critical keyword → should boost to at least high
    assert sev in ("critical", "high")


def test_rikouru_critical_keyword():
    sev, conf = score_alert(_item("egov", "regulation", title="リコール通知"))
    assert sev == "critical"


def test_high_keyword_in_body():
    sev, conf = score_alert(_item("gdelt", "regulation", title="ニュース", body="行政処分が下された"))
    assert sev in ("critical", "high")


def test_source_status_primary():
    status = source_status(["nite", "brave_news"])
    assert status == "primary_confirmed"


def test_source_status_multi():
    status = source_status(["brave_news", "gdelt"])
    assert status == "multi_source_confirmed"


def test_source_status_single():
    status = source_status(["gdelt"])
    assert status == "single_source"


def test_source_status_empty():
    status = source_status([])
    assert status == "single_source"


def test_unknown_source_is_low_confidence():
    # Unknown source → confidence low. Severity depends on category boost.
    sev, conf = score_alert(_item("unknown_source_xyz", category="competitor"))
    assert sev == "medium"  # competitor category maps to medium
    assert conf == "low"
