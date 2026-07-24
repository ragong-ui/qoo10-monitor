"""
Slack notification for the compliance briefing.
Posts to #japan-compliance (C0BKB580VBM) using Bot Token.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from .config import ComplianceConfig

log = logging.getLogger(__name__)

_SLACK_API_POST = "https://slack.com/api/chat.postMessage"


def _post_to_slack(token: str, channel: str, blocks: list[dict], text: str) -> bool:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "channel": channel,
        "text": text,
        "blocks": blocks,
        "unfurl_links": False,
        "unfurl_media": False,
    }
    try:
        resp = requests.post(_SLACK_API_POST, headers=headers, json=payload, timeout=30)
        data = resp.json()
        if data.get("ok"):
            return True
        log.error("[slack] API error: %s", data.get("error", "unknown"))
        return False
    except Exception as e:
        log.error("[slack] Request failed: %s", e)
        return False


def post_compliance_briefing(
    cfg: "ComplianceConfig",
    run_id: str,
    alerts: list[dict],
) -> bool:
    """
    Post the compliance briefing to Slack.
    Returns True on success.
    Respects cfg.slack_publish_enabled and cfg.dry_run.
    """
    from .formatters import format_slack_message

    if not cfg.slack_publish_enabled:
        log.info("[slack] SLACK_PUBLISH_ENABLED=false — skipping post")
        return True

    if not cfg.slack_bot_token:
        log.warning("[slack] SLACK_BOT_TOKEN not set — cannot post")
        return False

    blocks = format_slack_message(run_id, alerts, dry_run=cfg.dry_run)

    new_count = sum(1 for a in alerts if a.get("status") in ("new", "updated"))
    fallback_text = (
        f"[Japan Compliance Briefing] Run {run_id[:8]} — {new_count} new/updated alerts"
    )

    channel = cfg.slack_compliance_channel
    log.info("[slack] Posting to channel %s (%d blocks)", channel, len(blocks))

    # Slack has a 50-block limit per message; split if needed
    MAX_BLOCKS = 50
    for chunk_start in range(0, len(blocks), MAX_BLOCKS):
        chunk = blocks[chunk_start:chunk_start + MAX_BLOCKS]
        ok = _post_to_slack(cfg.slack_bot_token, channel, chunk, fallback_text)
        if not ok:
            return False

    return True
