from types import SimpleNamespace

from compliance_briefing.sheets_uploader import upload_run_snapshot


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _DB:
    def __init__(self):
        self.marked = []

    def get_alerts_for_run(self, run_id):
        return [{
            "run_id": run_id,
            "first_seen": "2026-07-26T01:02:03+00:00",
            "category": "recall",
            "country": "JP",
            "severity": "high",
            "confidence": "high",
            "title_ko": "리콜",
            "title_ja": "リコール",
            "summary_ko": "가" * 600,
            "summary_ja": "あ" * 600,
            "source_url": "https://example.com/recall",
            "brand": "Qoo10",
            "marketplace": "JP",
            "status": "new",
        }]

    def mark_dashboard_ready(self, run_id):
        self.marked.append(run_id)


def _config():
    return SimpleNamespace(
        sheets_export_enabled=True,
        compliance_apps_script_url="https://example.com/apps-script",
        compliance_apps_script_token="test-token",
        sheets_worksheet="ComplianceBriefing",
    )


def test_upload_matches_apps_script_contract_and_marks_ready(monkeypatch):
    calls = []

    def post(url, json, timeout):
        calls.append((url, json, timeout))
        if json["action"] == "batch_append":
            return _Response({"status": "ok", "rows": 1})
        return _Response({"status": "ok", "updated": 1})

    monkeypatch.setattr("compliance_briefing.sheets_uploader.requests.post", post)
    db = _DB()

    assert upload_run_snapshot(_config(), db, "run-123") is True
    assert [call[1]["action"] for call in calls] == [
        "batch_append",
        "mark_dashboard_ready",
    ]
    row = calls[0][1]["rows"][0]
    assert row["detected_at"] == "2026-07-26T01:02:03+00:00"
    assert row["title_ja"] == "リコール"
    assert len(row["summary_ko"]) == 500
    assert len(row["summary_ja"]) == 500
    assert calls[0][1]["api_token"] == "test-token"
    assert calls[1][1]["api_token"] == "test-token"
    assert db.marked == ["run-123"]


def test_upload_does_not_mark_local_ready_when_remote_mark_fails(monkeypatch):
    def post(url, json, timeout):
        if json["action"] == "batch_append":
            return _Response({"status": "ok", "rows": 1})
        return _Response({"status": "error", "message": "failed"})

    monkeypatch.setattr("compliance_briefing.sheets_uploader.requests.post", post)
    db = _DB()

    assert upload_run_snapshot(_config(), db, "run-456") is False
    assert db.marked == []


def test_upload_accepts_atomic_dashboard_ready_response(monkeypatch):
    calls = []

    def post(url, json, timeout):
        calls.append(json)
        return _Response({"status": "ok", "rows": 1, "dashboard_ready": True})

    monkeypatch.setattr("compliance_briefing.sheets_uploader.requests.post", post)
    db = _DB()

    assert upload_run_snapshot(_config(), db, "run-789") is True
    assert [call["action"] for call in calls] == ["batch_append"]
    assert db.marked == ["run-789"]


def test_upload_requires_api_token(monkeypatch):
    cfg = _config()
    cfg.compliance_apps_script_token = ""
    db = _DB()

    def unexpected_post(*args, **kwargs):
        raise AssertionError("request must not be sent without an API token")

    monkeypatch.setattr(
        "compliance_briefing.sheets_uploader.requests.post",
        unexpected_post,
    )

    assert upload_run_snapshot(cfg, db, "run-no-token") is False
    assert db.marked == []
