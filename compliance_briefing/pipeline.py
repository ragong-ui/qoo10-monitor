"""
Main compliance briefing pipeline orchestrator.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import ComplianceConfig
from .db import DB
from .dedup import dedup_items, make_fingerprint
from .scoring import score_alert, source_status
from .llm import generate_summaries
from .slack_notifier import post_compliance_briefing
from .sheets_uploader import upload_run_snapshot
from .collectors import ALL_COLLECTORS

log = logging.getLogger(__name__)


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]


def _build_alert(
    run_id: str,
    canonical: dict,
    all_sources: list[str],
    summary: dict,
) -> dict:
    """Assemble a normalized alert dict ready for DB upsert."""
    severity, confidence = score_alert(canonical)
    src_status = source_status(all_sources)

    # Entity is the source domain or marketplace if available
    url = canonical.get("url", "")
    entity = canonical.get("marketplace") or canonical.get("brand") or canonical.get("source_id", "")

    return {
        "alert_id": uuid.uuid4().hex,
        "fingerprint": canonical.get("fingerprint") or make_fingerprint(
            canonical["source_id"], canonical["external_id"]
        ),
        "run_id": run_id,
        "category": canonical.get("category", "regulation"),
        "country": canonical.get("country", "JP"),
        "marketplace": canonical.get("marketplace"),
        "entity": entity,
        "brand": canonical.get("brand"),
        "product_name": canonical.get("extra", {}).get("product_name"),
        "model_number": canonical.get("extra", {}).get("model_number"),
        "severity": severity,
        "confidence": confidence,
        "source_status": src_status,
        "title_ko": summary.get("title_ko") or canonical.get("title", ""),
        "title_ja": summary.get("title_ja") or canonical.get("title", ""),
        "summary_ko": summary.get("summary_ko") or canonical.get("body", "")[:300],
        "summary_ja": summary.get("summary_ja") or canonical.get("body", "")[:300],
        "source_url": url,
        "source_id": canonical.get("source_id", ""),
        "external_id": canonical.get("external_id", ""),
        "published_at": canonical.get("published_at"),
        "extra": canonical.get("extra", {}),
    }


class CompliancePipeline:
    def __init__(self, cfg: ComplianceConfig | None = None):
        self.cfg = cfg or ComplianceConfig()
        self.db = DB(self.cfg.db_path)

    def run(self) -> dict:
        """
        Execute one compliance briefing run.
        Returns a summary dict.
        """
        if not self.cfg.enabled:
            log.info("[pipeline] COMPLIANCE_BRIEFING_ENABLED=false — exiting")
            return {"status": "disabled"}

        run_id = _new_run_id()
        log.info("[pipeline] Starting run %s (dry_run=%s)", run_id, self.cfg.dry_run)

        self.db.create_run(run_id, self.cfg.dry_run)

        # ── Step 1: Collect ───────────────────────────────────
        all_raw: list[dict] = []
        for CollectorClass in ALL_COLLECTORS:
            collector = CollectorClass(self.cfg)
            items, success = collector.collect()
            self.db.record_source_health(collector.source_id, success)
            all_raw.extend(items)
            log.info("[pipeline] %s → %d items (ok=%s)", collector.source_id, len(items), success)

        log.info("[pipeline] Total raw items: %d", len(all_raw))

        if not all_raw:
            log.info("[pipeline] No items collected — finishing run")
            self.db.finish_run(run_id, 0, 0, 0)
            return {"run_id": run_id, "status": "no_items", "alert_count": 0}

        # ── Step 2: Dedup + cluster ───────────────────────────
        deduplicated = dedup_items(all_raw)
        log.info("[pipeline] After dedup: %d clusters from %d raw", len(deduplicated), len(all_raw))

        # ── Step 3: LLM summarization ─────────────────────────
        canonicals = [item for item, _ in deduplicated]
        summaries = generate_summaries(self.cfg, canonicals)

        # ── Step 4: Upsert to DB ──────────────────────────────
        new_count = 0
        updated_count = 0
        alert_ids: list[str] = []

        for (canonical, all_sources), summary in zip(deduplicated, summaries):
            alert = _build_alert(run_id, canonical, all_sources, summary)
            is_new, changes = self.db.upsert_alert(alert)
            if is_new:
                new_count += 1
            elif changes:
                updated_count += 1
            alert_ids.append(alert["alert_id"])

        total_alerts = len(deduplicated)
        log.info("[pipeline] Upserted: %d new, %d updated, %d ongoing",
                 new_count, updated_count, total_alerts - new_count - updated_count)

        # ── Step 5: Google Sheets snapshot ───────────────────
        try:
            upload_run_snapshot(self.cfg, self.db, run_id)
        except Exception as e:
            log.error("[pipeline] Sheets upload failed: %s", e)

        # ── Step 6: Slack notification ────────────────────────
        try:
            run_alerts = self.db.get_alerts_for_run(run_id)
            slacked = post_compliance_briefing(self.cfg, run_id, run_alerts)
            if slacked:
                new_alert_ids = [a["alert_id"] for a in run_alerts
                                 if a.get("status") in ("new", "updated")]
                self.db.mark_slacked(new_alert_ids)
        except Exception as e:
            log.error("[pipeline] Slack post failed: %s", e)

        # ── Finish ────────────────────────────────────────────
        self.db.finish_run(run_id, total_alerts, new_count, updated_count)

        summary_result = {
            "run_id": run_id,
            "status": "completed",
            "dry_run": self.cfg.dry_run,
            "alert_count": total_alerts,
            "new_count": new_count,
            "updated_count": updated_count,
        }
        log.info("[pipeline] Run %s completed: %s", run_id, summary_result)
        return summary_result
