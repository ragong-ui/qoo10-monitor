"""Integration test for the compliance pipeline (dry-run mode with fixtures)."""

import os
import tempfile
from pathlib import Path

import pytest

from compliance_briefing.config import ComplianceConfig
from compliance_briefing.db import DB
from compliance_briefing.pipeline import CompliancePipeline


@pytest.fixture
def tmp_cfg(tmp_path):
    """ComplianceConfig wired to temp DB and fixtures dir."""
    fixtures_src = Path(__file__).parent / "fixtures"
    cfg = ComplianceConfig()
    cfg.dry_run = True
    cfg.enabled = True
    cfg.sheets_export_enabled = False
    cfg.slack_publish_enabled = False
    cfg.llm_provider = "disabled"
    # Override paths
    cfg.__dict__["_db_path"] = tmp_path / "test_compliance.db"
    cfg.__dict__["_fixtures_dir"] = fixtures_src

    # Monkey-patch properties
    type(cfg).db_path = property(lambda self: self.__dict__.get("_db_path", tmp_path / "test_compliance.db"))
    type(cfg).fixtures_dir = property(lambda self: self.__dict__.get("_fixtures_dir", fixtures_src))
    return cfg


def test_pipeline_dry_run_completes(tmp_cfg):
    pipeline = CompliancePipeline(tmp_cfg)
    result = pipeline.run()

    assert result["status"] in ("completed", "no_items")
    assert "run_id" in result
    assert result.get("dry_run") is True


def test_pipeline_disabled(tmp_cfg):
    tmp_cfg.enabled = False
    pipeline = CompliancePipeline(tmp_cfg)
    result = pipeline.run()
    assert result["status"] == "disabled"


def test_pipeline_produces_alerts(tmp_cfg):
    pipeline = CompliancePipeline(tmp_cfg)
    result = pipeline.run()

    if result["status"] == "completed":
        db = DB(tmp_cfg.db_path)
        alerts = db.get_alerts_for_run(result["run_id"])
        assert isinstance(alerts, list)
        for alert in alerts:
            assert alert["severity"] in ("critical", "high", "medium", "low")
            assert alert["confidence"] in ("high", "medium", "low")
            assert alert["status"] in ("new", "updated", "ongoing", "closed", "corrected")


def test_pipeline_dedup_same_run(tmp_cfg):
    """Running twice should not duplicate alerts."""
    pipeline = CompliancePipeline(tmp_cfg)
    result1 = pipeline.run()
    result2 = pipeline.run()

    if result1["status"] == "completed" and result2["status"] == "completed":
        # Second run should have 0 new (all already in DB as ongoing)
        assert result2.get("new_count", 0) == 0


def test_secret_masking_in_config():
    """Sensitive values must not appear in masked_log_line output."""
    cfg = ComplianceConfig()
    # Set fake secrets
    cfg.slack_bot_token = "xoxb-FAKESECRETTOKEN12345"
    cfg.brave_api_key = "BRAVESECRETKEY_ABCDEFGH"
    cfg.anthropic_api_key = "sk-ant-FAKEKEYABCDEFGH"

    masked = cfg.masked_log_line()

    assert "FAKESECRETTOKEN12345" not in masked
    assert "BRAVESECRETKEY_ABCDEFGH" not in masked
    assert "sk-ant-FAKEKEYABCDEFGH" not in masked
    # Status indicators should still be present
    assert "set" in masked or "unset" in masked
