"""Persistence regression tests for compliance alerts."""

import json
import sqlite3

from compliance_briefing.db import DB


def _alert(run_id: str, **overrides) -> dict:
    alert = {
        "alert_id": f"generated-{run_id}",
        "fingerprint": "stable-fingerprint",
        "run_id": run_id,
        "category": "regulation",
        "country": "JP",
        "marketplace": "Qoo10",
        "entity": "Qoo10",
        "brand": None,
        "product_name": None,
        "model_number": None,
        "severity": "medium",
        "confidence": "low",
        "source_status": "single_source",
        "title_ko": "초기 제목",
        "title_ja": "初期タイトル",
        "summary_ko": "초기 요약",
        "summary_ja": "初期要約",
        "source_url": "https://example.com/alerts/1",
        "source_id": "brave_news",
        "external_id": "article-1",
        "published_at": "2026-07-25T00:00:00Z",
        "extra": {"revision": 1},
    }
    alert.update(overrides)
    return alert


def test_upsert_applies_changes_then_settles_to_ongoing(tmp_path):
    db = DB(tmp_path / "compliance.db")

    is_new, changes = db.upsert_alert(_alert("run-1"))
    assert is_new is True
    assert changes == []

    changed_alert = _alert(
        "run-2",
        confidence="medium",
        summary_ko="변경된 요약",
        source_url="https://example.com/alerts/1?revision=2",
        extra={"revision": 2},
    )
    is_new, changes = db.upsert_alert(changed_alert)

    assert is_new is False
    assert {field for field, _, _ in changes} == {"confidence", "summary_ko"}

    stored = db.get_alert_by_fingerprint("stable-fingerprint")
    assert stored is not None
    assert stored["alert_id"] == "generated-run-1"
    assert stored["run_id"] == "run-2"
    assert stored["status"] == "updated"
    assert stored["confidence"] == "medium"
    assert stored["summary_ko"] == "변경된 요약"
    assert stored["source_url"] == "https://example.com/alerts/1?revision=2"
    assert json.loads(stored["extra_json"]) == {"revision": 2}

    is_new, changes = db.upsert_alert(_alert(
        "run-3",
        confidence="medium",
        summary_ko="변경된 요약",
        source_url="https://example.com/alerts/1?revision=2",
        extra={"revision": 2},
    ))

    assert is_new is False
    assert changes == []

    stored = db.get_alert_by_fingerprint("stable-fingerprint")
    assert stored is not None
    assert stored["run_id"] == "run-3"
    assert stored["status"] == "ongoing"

    with db.conn() as con:
        history = con.execute(
            "SELECT field, old_value, new_value FROM alert_history "
            "WHERE alert_id=? ORDER BY id",
            ("generated-run-1",),
        ).fetchall()

    assert [row["field"] for row in history] == ["confidence", "summary_ko"]


def test_source_health_tracks_ok_partial_and_failed(tmp_path):
    db = DB(tmp_path / "compliance.db")

    db.record_source_health("brave_news", "failed", "request failed")
    with db.conn() as con:
        failed = con.execute(
            "SELECT * FROM source_health WHERE source_id=?",
            ("brave_news",),
        ).fetchone()

    assert failed["last_status"] == "failed"
    assert failed["consecutive_failures"] == 1
    assert failed["last_success"] is None
    assert failed["error_msg"] == "request failed"

    db.record_source_health("brave_news", "partial", "1 of 4 queries failed")
    with db.conn() as con:
        partial = con.execute(
            "SELECT * FROM source_health WHERE source_id=?",
            ("brave_news",),
        ).fetchone()

    assert partial["last_status"] == "partial"
    assert partial["consecutive_failures"] == 0
    assert partial["last_success"] is None
    assert partial["error_msg"] == "1 of 4 queries failed"

    db.record_source_health("brave_news", "ok")
    with db.conn() as con:
        healthy = con.execute(
            "SELECT * FROM source_health WHERE source_id=?",
            ("brave_news",),
        ).fetchone()

    assert healthy["last_status"] == "ok"
    assert healthy["consecutive_failures"] == 0
    assert healthy["last_success"] is not None
    assert healthy["error_msg"] is None


def test_source_health_migrates_existing_database(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as con:
        con.execute(
            """CREATE TABLE source_health (
               source_id TEXT PRIMARY KEY,
               last_checked TEXT,
               last_success TEXT,
               consecutive_failures INTEGER NOT NULL DEFAULT 0,
               error_msg TEXT
            )"""
        )

    db = DB(db_path)
    with db.conn() as con:
        columns = {
            row["name"] for row in con.execute("PRAGMA table_info(source_health)")
        }

    assert "last_status" in columns
