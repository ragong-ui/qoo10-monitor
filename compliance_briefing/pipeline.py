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
from .filter import filter_items
from .scoring import score_alert, source_status
from .llm import generate_summaries
from .slack_notifier import post_compliance_briefing
from .sheets_uploader import upload_run_snapshot
from .collectors import ALL_COLLECTORS

log = logging.getLogger(__name__)


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]


def _safe_error_message(cfg: ComplianceConfig, exc: Exception) -> str:
    """Build a bounded error summary with configured secrets redacted."""
    message = f"{type(exc).__name__}: {exc}"
    secrets = (
        cfg.brave_api_key,
        cfg.safety_korea_api_key,
        cfg.anthropic_api_key,
        cfg.openai_api_key,
        cfg.slack_bot_token,
        cfg.compliance_apps_script_url,
    )
    for secret in secrets:
        if secret:
            message = message.replace(secret, "***")
    return message[:2000]


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

    def summary_value(key: str, fallback: str) -> str:
        value = summary.get(key)
        return fallback if value is None else value

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
        "title_ko": summary_value("title_ko", canonical.get("title", "")),
        "title_ja": summary_value("title_ja", canonical.get("title", "")),
        "summary_ko": summary_value("summary_ko", canonical.get("body", "")[:300]),
        "summary_ja": summary_value("summary_ja", canonical.get("body", "")[:300]),
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

        try:
            return self._run_started(run_id)
        except Exception as exc:
            error_msg = _safe_error_message(self.cfg, exc)
            log.error("[pipeline] Run %s failed: %s", run_id, error_msg)
            try:
                self.db.fail_run(run_id, error_msg)
            except Exception:
                log.exception("[pipeline] Could not mark run %s as failed", run_id)
            raise

    def _run_started(self, run_id: str) -> dict:
        """Execute the steps for a run whose DB record already exists."""
        # ── Step 1: Collect ───────────────────────────────────
        all_raw: list[dict] = []
        source_statuses: dict[str, str] = {}
        disabled_sources: list[str] = []
        for CollectorClass in ALL_COLLECTORS:
            source_id = CollectorClass.source_id
            if not self.cfg.collector_enabled(source_id):
                disabled_sources.append(source_id)
                log.info("[pipeline] %s → disabled", source_id)
                continue

            collector = CollectorClass(self.cfg)
            result = collector.collect()
            source_statuses[collector.source_id] = result.status
            self.db.record_source_health(
                collector.source_id,
                result.status,
                result.error_msg,
            )
            all_raw.extend(result.items)
            log.info(
                "[pipeline] %s → %d items (status=%s)",
                collector.source_id,
                len(result.items),
                result.status,
            )

        log.info("[pipeline] Total raw items: %d", len(all_raw))

        if not source_statuses:
            raise RuntimeError("No compliance collectors are enabled")

        if source_statuses and all(
            status == "failed" for status in source_statuses.values()
        ):
            raise RuntimeError("All compliance collectors failed")

        if not all_raw:
            log.info("[pipeline] No items collected — finishing run")
            self.db.finish_run(run_id, 0, 0, 0)
            return {
                "run_id": run_id,
                "status": "no_items",
                "alert_count": 0,
                "source_statuses": source_statuses,
                "disabled_sources": disabled_sources,
            }

        # ── Step 1b: Relevance filter ─────────────────────────
        all_raw, filter_counts = filter_items(all_raw)
        log.info("[pipeline] After filter: %d items (rejected: %s)",
                 len(all_raw), filter_counts)

        if not all_raw:
            log.info("[pipeline] All items filtered out — finishing run")
            self.db.finish_run(run_id, 0, 0, 0)
            return {
                "run_id": run_id,
                "status": "no_items",
                "alert_count": 0,
                "source_statuses": source_statuses,
                "disabled_sources": disabled_sources,
            }

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
        dashboard_ready = True
        try:
            dashboard_ready = upload_run_snapshot(self.cfg, self.db, run_id)
        except Exception as e:
            log.error("[pipeline] Sheets upload failed: %s", e)
            dashboard_ready = False

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
        run_error = None
        run_status = "completed"
        if self.cfg.sheets_export_enabled and not dashboard_ready:
            run_error = "Google Sheets dashboard update failed"
            run_status = "failed"
        self.db.finish_run(
            run_id,
            total_alerts,
            new_count,
            updated_count,
            error_msg=run_error,
        )

        summary_result = {
            "run_id": run_id,
            "status": run_status,
            "dry_run": self.cfg.dry_run,
            "alert_count": total_alerts,
            "new_count": new_count,
            "updated_count": updated_count,
            "dashboard_ready": dashboard_ready,
            "source_statuses": source_statuses,
            "disabled_sources": disabled_sources,
        }
        log.info("[pipeline] Run %s finished: %s", run_id, summary_result)
        return summary_result
