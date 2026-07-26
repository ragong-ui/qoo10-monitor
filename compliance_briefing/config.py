"""
Compliance briefing configuration — reads from environment / .env
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def _int(key: str, default: int, minimum: int = 1) -> int:
    val = os.getenv(key, "").strip()
    if not val:
        return default
    try:
        return max(minimum, int(val))
    except ValueError:
        return default


@dataclass
class ComplianceConfig:
    # ── Feature flags ─────────────────────────────────────────
    enabled: bool = field(default_factory=lambda: _bool("COMPLIANCE_BRIEFING_ENABLED", True))
    dry_run: bool = field(default_factory=lambda: _bool("COMPLIANCE_DRY_RUN", True))
    sheets_export_enabled: bool = field(default_factory=lambda: _bool("GOOGLE_SHEETS_EXPORT_ENABLED", False))
    slack_publish_enabled: bool = field(default_factory=lambda: _bool("SLACK_PUBLISH_ENABLED", False))

    # ── Search / collection ───────────────────────────────────
    brave_api_key: str = field(default_factory=lambda: os.getenv("BRAVE_SEARCH_API_KEY", ""))
    brave_results_per_query: int = 10
    gdelt_max_records: int = 50
    gdelt_time_budget_seconds: int = field(
        default_factory=lambda: _int("GDELT_TIME_BUDGET_SECONDS", 60)
    )
    gdelt_retry_after_cap_seconds: int = field(
        default_factory=lambda: _int("GDELT_RETRY_AFTER_CAP_SECONDS", 15)
    )
    nikkei_lookback_days: int = field(
        default_factory=lambda: _int("NIKKEI_LOOKBACK_DAYS", 7)
    )
    collector_enabled_overrides: dict[str, bool] = field(
        default_factory=dict,
        repr=False,
    )

    # ── Safety Korea (data.go.kr) ─────────────────────────────
    safety_korea_api_key: str = field(default_factory=lambda: os.getenv("SAFETY_KOREA_API_KEY", ""))

    # ── LLM ──────────────────────────────────────────────────
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "disabled"))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    llm_model_anthropic: str = "claude-haiku-4-5-20251001"
    llm_model_openai: str = "gpt-4o-mini"
    llm_timeout: int = 30

    # ── Google Sheets (compliance-specific Apps Script) ───────
    compliance_apps_script_url: str = field(
        default_factory=lambda: os.getenv("COMPLIANCE_APPS_SCRIPT_URL", "")
    )
    compliance_apps_script_token: str = field(
        default_factory=lambda: os.getenv("COMPLIANCE_APPS_SCRIPT_TOKEN", "")
    )
    sheets_worksheet: str = "ComplianceBriefing"

    # ── Slack ─────────────────────────────────────────────────
    slack_bot_token: str = field(default_factory=lambda: os.getenv("SLACK_BOT_TOKEN", ""))
    slack_compliance_channel: str = field(
        default_factory=lambda: os.getenv("SLACK_COMPLIANCE_CHANNEL_ID", "C0BKB580VBM")
    )

    # ── Storage ──────────────────────────────────────────────
    base_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent)

    @property
    def db_path(self) -> Path:
        filename = (
            "compliance_briefing.dryrun.db"
            if self.dry_run
            else "compliance_briefing.db"
        )
        return self.base_dir / filename

    @property
    def fixtures_dir(self) -> Path:
        return self.base_dir / "tests" / "compliance" / "fixtures"

    # ── Severity / confidence thresholds ─────────────────────
    critical_sources: tuple = ("nite", "caa", "safety_korea_mfds")
    high_sources: tuple = ("egov", "meti", "jftc", "ppc", "mhlw", "safety_korea_kats")
    medium_sources: tuple = ("brave_news", "gdelt", "ncac")

    def collector_enabled(self, source_id: str) -> bool:
        """Return whether a collector should run for this configuration."""
        if source_id in self.collector_enabled_overrides:
            return self.collector_enabled_overrides[source_id]

        flag_names = {
            "brave_news": "BRAVE",
            "safety_korea_kca": "SAFETY_KOREA",
        }
        flag_name = flag_names.get(source_id, source_id).upper()
        default = source_id != "gdelt"
        return _bool(f"COMPLIANCE_SOURCE_{flag_name}_ENABLED", default)

    def masked_log_line(self) -> str:
        """Return a safe config summary with secrets redacted."""
        def _mask(v: str) -> str:
            return f"{v[:4]}***{v[-2:]}" if len(v) > 8 else ("***" if v else "(unset)")

        return (
            f"dry_run={self.dry_run} "
            f"sheets={self.sheets_export_enabled} "
            f"slack={self.slack_publish_enabled} "
            f"llm={self.llm_provider} "
            f"brave={'set' if self.brave_api_key else 'unset'} "
            f"slack_token={'set' if self.slack_bot_token else 'unset'}"
        )
