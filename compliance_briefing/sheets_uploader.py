"""
Google Sheets snapshot upload via the compliance Apps Script Web App.
Atomic: collect all rows → validate → upload → mark dashboard_ready.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from .config import ComplianceConfig
    from .db import DB

log = logging.getLogger(__name__)

SHEET_HEADERS = [
    "検出日時", "Run ID", "カテゴリ", "国", "重要度", "信頼度",
    "タイトル(KO)", "タイトル(JA)", "概要(KO)", "概要(JA)",
    "ソースURL", "ブランド", "マーケットプレイス", "ステータス", "備考",
]

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _alert_to_row(alert: dict) -> list:
    return [
        alert.get("first_seen", ""),
        alert.get("run_id", ""),
        alert.get("category", ""),
        alert.get("country", ""),
        alert.get("severity", ""),
        alert.get("confidence", ""),
        alert.get("title_ko", ""),
        alert.get("title_ja", ""),
        alert.get("summary_ko", "")[:500],
        alert.get("summary_ja", "")[:500],
        alert.get("source_url", ""),
        alert.get("brand", "") or "",
        alert.get("marketplace", "") or "",
        alert.get("status", "new"),
        "",  # 備考 — blank initially
    ]


def _validate_rows(rows: list[list]) -> bool:
    """Basic sanity check before upload."""
    if not rows:
        return True
    for row in rows:
        if len(row) != len(SHEET_HEADERS):
            log.error("[sheets] Row has %d columns, expected %d", len(row), len(SHEET_HEADERS))
            return False
    return True


def upload_run_snapshot(
    cfg: "ComplianceConfig",
    db: "DB",
    run_id: str,
) -> bool:
    """
    Atomic upload: fetch all alerts for run_id → validate → POST to Apps Script → mark ready.
    Returns True on success.
    """
    if not cfg.sheets_export_enabled:
        log.info("[sheets] GOOGLE_SHEETS_EXPORT_ENABLED=false — skipping upload")
        return True

    if not cfg.compliance_apps_script_url:
        log.warning("[sheets] COMPLIANCE_APPS_SCRIPT_URL not set — cannot upload")
        return False

    alerts = db.get_alerts_for_run(run_id)
    if not alerts:
        log.info("[sheets] No alerts for run %s — nothing to upload", run_id)
        return True

    # Sort by severity then first_seen
    alerts.sort(key=lambda a: (_SEV_ORDER.get(a.get("severity", "low"), 3), a.get("first_seen", "")))

    rows = [_alert_to_row(a) for a in alerts]

    if not _validate_rows(rows):
        return False

    payload = {
        "action": "batch_append",
        "sheet": cfg.sheets_worksheet,
        "headers": SHEET_HEADERS,
        "rows": rows,
        "run_id": run_id,
    }

    try:
        resp = requests.post(cfg.compliance_apps_script_url, json=payload, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") == "ok":
            log.info("[sheets] Uploaded %d rows for run %s", result.get("rows", len(rows)), run_id)
            db.mark_dashboard_ready(run_id)
            return True
        else:
            log.error("[sheets] Apps Script returned error: %s", result)
            return False
    except Exception as e:
        log.error("[sheets] Upload failed: %s", e)
        return False
