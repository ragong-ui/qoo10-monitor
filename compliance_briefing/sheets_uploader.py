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


def _alert_to_payload(alert: dict) -> dict:
    """Map a DB alert to the object contract expected by Code.gs."""
    return {
        "detected_at": alert.get("first_seen", ""),
        "run_id": alert.get("run_id", ""),
        "category": alert.get("category", ""),
        "country": alert.get("country", ""),
        "severity": alert.get("severity", ""),
        "confidence": alert.get("confidence", ""),
        "title_ko": alert.get("title_ko", ""),
        "title_ja": alert.get("title_ja", ""),
        "summary_ko": alert.get("summary_ko", "")[:500],
        "summary_ja": alert.get("summary_ja", "")[:500],
        "source_url": alert.get("source_url", ""),
        "brand": alert.get("brand", "") or "",
        "marketplace": alert.get("marketplace", "") or "",
        "status": alert.get("status", "new"),
        "notes": "",
    }


def _validate_rows(rows: list[dict]) -> bool:
    """Basic sanity check before upload."""
    expected = {
        "detected_at", "run_id", "category", "country", "severity", "confidence",
        "title_ko", "title_ja", "summary_ko", "summary_ja", "source_url",
        "brand", "marketplace", "status", "notes",
    }
    for row in rows:
        if set(row) != expected:
            log.error("[sheets] Row payload does not match Apps Script contract")
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

    if not cfg.compliance_apps_script_token:
        log.warning("[sheets] COMPLIANCE_APPS_SCRIPT_TOKEN not set — cannot upload securely")
        return False

    alerts = db.get_alerts_for_run(run_id)
    if not alerts:
        log.info("[sheets] No alerts for run %s — nothing to upload", run_id)
        return True

    # Sort by severity then first_seen
    alerts.sort(key=lambda a: (_SEV_ORDER.get(a.get("severity", "low"), 3), a.get("first_seen", "")))

    rows = [_alert_to_payload(a) for a in alerts]

    if not _validate_rows(rows):
        return False

    payload = {
        "action": "batch_append",
        "sheet": cfg.sheets_worksheet,
        "headers": SHEET_HEADERS,
        "rows": rows,
        "run_id": run_id,
        "api_token": cfg.compliance_apps_script_token,
    }

    try:
        append_resp = requests.post(
            cfg.compliance_apps_script_url,
            json=payload,
            timeout=60,
        )
        append_resp.raise_for_status()
        result = append_resp.json()
        if result.get("status") != "ok":
            log.error("[sheets] Apps Script returned error: %s", result)
            return False
        if int(result.get("rows", -1)) != len(rows):
            log.error(
                "[sheets] Apps Script appended %s rows, expected %d",
                result.get("rows"),
                len(rows),
            )
            return False

        # New deployments mark all rows ready atomically in batch_append.
        # Keep the explicit action as a compatibility path for older deployments.
        if result.get("dashboard_ready") is True:
            log.info("[sheets] Uploaded %d rows for run %s", len(rows), run_id)
            db.mark_dashboard_ready(run_id)
            return True

        ready_resp = requests.post(
            cfg.compliance_apps_script_url,
            json={
                "action": "mark_dashboard_ready",
                "run_id": run_id,
                "api_token": cfg.compliance_apps_script_token,
            },
            timeout=60,
        )
        ready_resp.raise_for_status()
        ready_result = ready_resp.json()
        if ready_result.get("status") != "ok":
            log.error("[sheets] Dashboard-ready update failed: %s", ready_result)
            return False
        if int(ready_result.get("updated", 0)) < len(rows):
            log.error(
                "[sheets] Dashboard-ready updated %s rows, expected at least %d",
                ready_result.get("updated"),
                len(rows),
            )
            return False

        log.info("[sheets] Uploaded %d rows for run %s", len(rows), run_id)
        db.mark_dashboard_ready(run_id)
        return True
    except Exception as e:
        log.error("[sheets] Upload failed: %s", e)
        return False
