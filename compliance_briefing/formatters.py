"""
Bilingual formatting helpers for Slack messages and email bodies.
"""

from __future__ import annotations

_SEV_EMOJI = {
    "critical": "🚨",
    "high":     "🔴",
    "medium":   "🟡",
    "low":      "⚪",
}

_CAT_EMOJI = {
    "recall":     "⚠️",
    "regulation": "📋",
    "safety":     "🛡️",
    "competitor": "🏪",
}

_COUNTRY_FLAG = {
    "JP": "🇯🇵",
    "KR": "🇰🇷",
    "MULTI": "🌏",
}


def sev_emoji(severity: str) -> str:
    return _SEV_EMOJI.get(severity, "⚪")


def cat_emoji(category: str) -> str:
    return _CAT_EMOJI.get(category, "📌")


def country_flag(country: str) -> str:
    return _COUNTRY_FLAG.get(country, "🌐")


def format_alert_block(alert: dict, lang: str = "ko") -> dict:
    """Format a single alert as a Slack Block Kit section block."""
    sev = alert.get("severity", "medium")
    cat = alert.get("category", "")
    country = alert.get("country", "JP")

    title = alert.get("title_ko" if lang == "ko" else "title_ja", "")
    summary = alert.get("summary_ko" if lang == "ko" else "summary_ja", "")
    url = alert.get("source_url", "")

    header = (
        f"{sev_emoji(sev)} {cat_emoji(cat)} {country_flag(country)} "
        f"*{title}*"
    )
    body_lines = [summary]
    if alert.get("brand"):
        body_lines.append(f"Brand: `{alert['brand']}`")
    if alert.get("marketplace"):
        body_lines.append(f"Marketplace: `{alert['marketplace']}`")
    if url:
        body_lines.append(f"<{url}|Source>")

    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": header + "\n" + "\n".join(body_lines),
        },
    }


def format_slack_message(run_id: str, alerts: list[dict], dry_run: bool = False) -> list[dict]:
    """Build a full Slack Block Kit message for the compliance briefing."""
    new_alerts = [a for a in alerts if a.get("status") in ("new", "updated")]
    critical = [a for a in new_alerts if a.get("severity") == "critical"]
    high = [a for a in new_alerts if a.get("severity") == "high"]
    medium = [a for a in new_alerts if a.get("severity") == "medium"]

    dry_tag = " `[DRY RUN]`" if dry_run else ""
    header_text = (
        f"*🗾 Japan Marketplace Compliance Briefing*{dry_tag}\n"
        f"Run: `{run_id[:8]}`  |  "
        f"🚨 Critical: *{len(critical)}*  🔴 High: *{len(high)}*  🟡 Medium: *{len(medium)}*"
    )

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": "🗾 Japan Compliance Briefing", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": header_text}},
        {"type": "divider"},
    ]

    if not new_alerts:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "✅ No new compliance alerts in this run."},
        })
        return blocks

    # Critical + High alerts (full detail)
    for alert in (critical + high)[:8]:
        blocks.append(format_alert_block(alert, lang="ko"))
        blocks.append({"type": "divider"})

    # Medium alerts (compact list)
    if medium:
        medium_lines = []
        for a in medium[:5]:
            title = a.get("title_ko") or a.get("title_ja") or ""
            url = a.get("source_url", "")
            flag = country_flag(a.get("country", "JP"))
            medium_lines.append(f"• {flag} <{url}|{title[:60]}>" if url else f"• {flag} {title[:60]}")
        if len(medium) > 5:
            medium_lines.append(f"… and {len(medium) - 5} more")
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*🟡 Medium Priority*\n" + "\n".join(medium_lines),
            },
        })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"Japan Compliance Briefing · Run `{run_id[:8]}`"}],
    })
    return blocks


def format_email_body(run_id: str, alerts: list[dict]) -> str:
    """Plain-text email body for compliance briefing."""
    new_alerts = [a for a in alerts if a.get("status") in ("new", "updated")]
    lines = [
        f"Japan Marketplace Compliance Briefing",
        f"Run ID: {run_id}",
        f"",
        f"총 {len(new_alerts)}건 신규/업데이트 알림",
        "─" * 50,
    ]
    for a in new_alerts[:20]:
        sev = a.get("severity", "medium").upper()
        title = a.get("title_ko") or a.get("title_ja") or ""
        summary = a.get("summary_ko") or a.get("summary_ja") or ""
        url = a.get("source_url", "")
        lines += [f"[{sev}] {title}", summary, url, ""]
    return "\n".join(lines)
