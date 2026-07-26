"""Integration test for the compliance pipeline (dry-run mode with fixtures)."""

import os
import tempfile
from pathlib import Path

import pytest

from compliance_briefing.collector_base import CollectionResult
from compliance_briefing.config import ComplianceConfig
from compliance_briefing.db import DB
from compliance_briefing.pipeline import CompliancePipeline


@pytest.fixture
def tmp_cfg(tmp_path, monkeypatch):
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
    monkeypatch.setattr(
        type(cfg),
        "db_path",
        property(lambda self: self.__dict__.get("_db_path", tmp_path / "test_compliance.db")),
    )
    monkeypatch.setattr(
        type(cfg),
        "fixtures_dir",
        property(lambda self: self.__dict__.get("_fixtures_dir", fixtures_src)),
    )
    return cfg


def test_pipeline_dry_run_completes(tmp_cfg):
    pipeline = CompliancePipeline(tmp_cfg)
    result = pipeline.run()

    assert result["status"] in ("completed", "no_items")
    assert "run_id" in result
    assert result.get("dry_run") is True
    assert set(result["source_statuses"].values()) == {"ok"}


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


def test_pipeline_marks_run_failed_when_dashboard_upload_fails(tmp_cfg, monkeypatch):
    tmp_cfg.sheets_export_enabled = True
    monkeypatch.setattr(
        "compliance_briefing.pipeline.upload_run_snapshot",
        lambda *_args, **_kwargs: False,
    )

    pipeline = CompliancePipeline(tmp_cfg)
    result = pipeline.run()

    if result["status"] != "no_items":
        assert result["status"] == "failed"
        assert result["dashboard_ready"] is False
        with pipeline.db.conn() as con:
            run = con.execute(
                "SELECT status, error_msg, alert_count FROM runs WHERE run_id=?",
                (result["run_id"],),
            ).fetchone()
        assert run["status"] == "failed"
        assert run["error_msg"] == "Google Sheets dashboard update failed"
        assert run["alert_count"] == result["alert_count"]


def test_pipeline_dedup_same_run(tmp_cfg):
    """Running twice should not duplicate alerts."""
    pipeline = CompliancePipeline(tmp_cfg)
    result1 = pipeline.run()
    result2 = pipeline.run()

    if result1["status"] == "completed" and result2["status"] == "completed":
        # Second run should have 0 new (all already in DB as ongoing)
        assert result2.get("new_count", 0) == 0


def test_pipeline_failure_marks_run_failed(tmp_cfg, monkeypatch):
    """Unexpected failures must not leave a run stuck in running state."""
    secret = "BRAVESECRETKEY_ABCDEFGH"
    tmp_cfg.brave_api_key = secret

    def fail_summarization(*_args, **_kwargs):
        raise RuntimeError(f"forced failure with {secret}")

    monkeypatch.setattr(
        "compliance_briefing.pipeline.generate_summaries",
        fail_summarization,
    )

    pipeline = CompliancePipeline(tmp_cfg)
    with pytest.raises(RuntimeError, match="forced failure"):
        pipeline.run()

    with pipeline.db.conn() as con:
        run = con.execute(
            "SELECT status, finished_at, error_msg FROM runs"
        ).fetchone()

    assert run is not None
    assert run["status"] == "failed"
    assert run["finished_at"] is not None
    assert "RuntimeError: forced failure" in run["error_msg"]
    assert secret not in run["error_msg"]


def test_config_separates_dry_run_and_live_databases(tmp_path):
    cfg = ComplianceConfig()
    cfg.base_dir = tmp_path

    cfg.dry_run = True
    assert cfg.db_path == tmp_path / "compliance_briefing.dryrun.db"

    cfg.dry_run = False
    assert cfg.db_path == tmp_path / "compliance_briefing.db"


def test_gdelt_is_disabled_by_default_and_can_be_enabled(monkeypatch):
    monkeypatch.delenv("COMPLIANCE_SOURCE_GDELT_ENABLED", raising=False)
    cfg = ComplianceConfig()

    assert cfg.collector_enabled("gdelt") is False
    assert cfg.collector_enabled("egov") is True

    monkeypatch.setenv("COMPLIANCE_SOURCE_GDELT_ENABLED", "true")
    assert cfg.collector_enabled("gdelt") is True


def test_pipeline_skips_disabled_collectors(tmp_cfg, monkeypatch):
    class DisabledCollector:
        source_id = "gdelt"

        def __init__(self, _cfg):
            raise AssertionError("disabled collector must not be instantiated")

    class EnabledCollector:
        source_id = "test_official"

        def __init__(self, _cfg):
            pass

        def collect(self):
            return CollectionResult(
                items=[{
                    "source_id": self.source_id,
                    "external_id": "1",
                    "url": "https://example.com/notice/1",
                    "title": "공식 규제 공지 테스트",
                    "body": "테스트",
                    "category": "regulation",
                    "country": "JP",
                    "published_at": None,
                    "marketplace": None,
                    "brand": None,
                    "extra": {},
                }],
                status="ok",
            )

    tmp_cfg.collector_enabled_overrides = {
        "gdelt": False,
        "test_official": True,
    }
    monkeypatch.setattr(
        "compliance_briefing.pipeline.ALL_COLLECTORS",
        [DisabledCollector, EnabledCollector],
    )

    result = CompliancePipeline(tmp_cfg).run()

    assert result["status"] == "completed"
    assert result["disabled_sources"] == ["gdelt"]
    assert result["source_statuses"] == {"test_official": "ok"}


def test_pipeline_fails_when_all_collectors_are_disabled(tmp_cfg, monkeypatch):
    class DisabledCollector:
        source_id = "gdelt"

    tmp_cfg.collector_enabled_overrides = {"gdelt": False}
    monkeypatch.setattr(
        "compliance_briefing.pipeline.ALL_COLLECTORS",
        [DisabledCollector],
    )

    pipeline = CompliancePipeline(tmp_cfg)
    with pytest.raises(RuntimeError, match="No compliance collectors"):
        pipeline.run()

    with pipeline.db.conn() as con:
        run = con.execute("SELECT status, error_msg FROM runs").fetchone()

    assert run["status"] == "failed"
    assert "No compliance collectors are enabled" in run["error_msg"]


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
