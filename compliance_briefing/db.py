"""
SQLite persistence for compliance briefing runs and alerts.
"""

import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    dry_run     INTEGER NOT NULL DEFAULT 0,
    alert_count INTEGER,
    new_count   INTEGER,
    updated_count INTEGER,
    error_msg   TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id        TEXT PRIMARY KEY,
    fingerprint     TEXT NOT NULL UNIQUE,
    run_id          TEXT NOT NULL,
    category        TEXT NOT NULL,
    country         TEXT NOT NULL,
    marketplace     TEXT,
    entity          TEXT NOT NULL,
    brand           TEXT,
    product_name    TEXT,
    model_number    TEXT,
    severity        TEXT NOT NULL,
    confidence      TEXT NOT NULL,
    source_status   TEXT NOT NULL DEFAULT 'single_source',
    title_ko        TEXT NOT NULL,
    title_ja        TEXT NOT NULL,
    summary_ko      TEXT NOT NULL,
    summary_ja      TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    published_at    TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'new',
    sheets_row      INTEGER,
    slacked         INTEGER NOT NULL DEFAULT 0,
    dashboard_ready INTEGER NOT NULL DEFAULT 0,
    extra_json      TEXT
);

CREATE TABLE IF NOT EXISTS alert_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id    TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    changed_at  TEXT NOT NULL,
    field       TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT
);

CREATE TABLE IF NOT EXISTS source_health (
    source_id            TEXT PRIMARY KEY,
    last_checked         TEXT,
    last_success         TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    error_msg            TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_fingerprint ON alerts(fingerprint);
CREATE INDEX IF NOT EXISTS idx_alerts_status      ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_alerts_run_id      ON alerts(run_id);
CREATE INDEX IF NOT EXISTS idx_history_alert_id   ON alert_history(alert_id);
"""


class DB:
    def __init__(self, path: Path):
        self.path = path
        self._migrate()

    @contextmanager
    def conn(self) -> Generator[sqlite3.Connection, None, None]:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _migrate(self):
        with self.conn() as con:
            con.executescript(SCHEMA)

    # ── Runs ──────────────────────────────────────────────────

    def create_run(self, run_id: str, dry_run: bool) -> None:
        with self.conn() as con:
            con.execute(
                "INSERT INTO runs (run_id, started_at, status, dry_run) VALUES (?,?,?,?)",
                (run_id, _now(), "running", int(dry_run)),
            )

    def finish_run(self, run_id: str, alert_count: int, new_count: int,
                   updated_count: int, error_msg: str | None = None) -> None:
        status = "failed" if error_msg else "completed"
        with self.conn() as con:
            con.execute(
                """UPDATE runs SET finished_at=?, status=?, alert_count=?,
                   new_count=?, updated_count=?, error_msg=? WHERE run_id=?""",
                (_now(), status, alert_count, new_count, updated_count, error_msg, run_id),
            )

    # ── Alerts ───────────────────────────────────────────────

    def get_alert_by_fingerprint(self, fingerprint: str) -> dict | None:
        with self.conn() as con:
            row = con.execute(
                "SELECT * FROM alerts WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
        return dict(row) if row else None

    def upsert_alert(self, alert: dict) -> tuple[bool, list[tuple[str, str, str]]]:
        """Insert or update an alert. Returns (is_new, list_of_changes)."""
        existing = self.get_alert_by_fingerprint(alert["fingerprint"])
        now = _now()

        if not existing:
            with self.conn() as con:
                con.execute(
                    """INSERT INTO alerts
                       (alert_id, fingerprint, run_id, category, country, marketplace,
                        entity, brand, product_name, model_number, severity, confidence,
                        source_status, title_ko, title_ja, summary_ko, summary_ja,
                        source_url, source_id, external_id, published_at,
                        first_seen, last_seen, status, extra_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        alert["alert_id"], alert["fingerprint"], alert["run_id"],
                        alert["category"], alert["country"], alert.get("marketplace"),
                        alert["entity"], alert.get("brand"), alert.get("product_name"),
                        alert.get("model_number"), alert["severity"], alert["confidence"],
                        alert.get("source_status", "single_source"),
                        alert["title_ko"], alert["title_ja"],
                        alert["summary_ko"], alert["summary_ja"],
                        alert["source_url"], alert["source_id"], alert["external_id"],
                        alert.get("published_at"), now, now, "new",
                        json.dumps(alert.get("extra", {}), ensure_ascii=False),
                    ),
                )
            return True, []

        # Existing — detect changes
        track_fields = ("severity", "confidence", "summary_ko", "summary_ja",
                        "source_status", "title_ko", "title_ja")
        changes: list[tuple[str, str, str]] = []
        for f in track_fields:
            old_val = str(existing.get(f) or "")
            new_val = str(alert.get(f) or "")
            if old_val != new_val:
                changes.append((f, old_val, new_val))

        new_status = "updated" if changes else "ongoing"
        with self.conn() as con:
            con.execute(
                "UPDATE alerts SET last_seen=?, run_id=?, status=? WHERE fingerprint=?",
                (now, alert["run_id"], new_status, alert["fingerprint"]),
            )
            if changes:
                con.executemany(
                    """INSERT INTO alert_history (alert_id, run_id, changed_at, field, old_value, new_value)
                       VALUES (?,?,?,?,?,?)""",
                    [
                        (existing["alert_id"], alert["run_id"], now, f, ov, nv)
                        for f, ov, nv in changes
                    ],
                )
        return False, changes

    def mark_slacked(self, alert_ids: list[str]) -> None:
        if not alert_ids:
            return
        placeholders = ",".join("?" * len(alert_ids))
        with self.conn() as con:
            con.execute(
                f"UPDATE alerts SET slacked=1 WHERE alert_id IN ({placeholders})", alert_ids
            )

    def mark_dashboard_ready(self, run_id: str) -> None:
        with self.conn() as con:
            con.execute(
                "UPDATE alerts SET dashboard_ready=1 WHERE run_id=?", (run_id,)
            )

    def get_alerts_for_run(self, run_id: str) -> list[dict]:
        with self.conn() as con:
            rows = con.execute(
                "SELECT * FROM alerts WHERE run_id=? ORDER BY severity, category",
                (run_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_new_alerts_since(self, hours: int = 48) -> list[dict]:
        with self.conn() as con:
            rows = con.execute(
                "SELECT * FROM alerts WHERE status IN ('new','updated') "
                "AND datetime(first_seen) >= datetime('now', ? || ' hours') "
                "ORDER BY severity, first_seen DESC",
                (f"-{hours}",),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Source health ─────────────────────────────────────────

    def record_source_health(self, source_id: str, success: bool, error_msg: str | None = None) -> None:
        now = _now()
        with self.conn() as con:
            existing = con.execute(
                "SELECT consecutive_failures FROM source_health WHERE source_id=?", (source_id,)
            ).fetchone()

            if existing is None:
                failures = 0 if success else 1
                con.execute(
                    """INSERT INTO source_health
                       (source_id, last_checked, last_success, consecutive_failures, error_msg)
                       VALUES (?,?,?,?,?)""",
                    (source_id, now, now if success else None, failures, error_msg),
                )
            else:
                failures = 0 if success else (existing["consecutive_failures"] + 1)
                con.execute(
                    """UPDATE source_health SET last_checked=?, consecutive_failures=?, error_msg=?
                       {} WHERE source_id=?""".format(
                        ", last_success=?" if success else ""
                    ),
                    ([now, failures, error_msg, now, source_id] if success
                     else [now, failures, error_msg, source_id]),
                )
