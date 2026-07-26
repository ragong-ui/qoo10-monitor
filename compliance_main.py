"""
Japan Marketplace Compliance Briefing — entry point
Task Scheduler: python -u compliance_main.py
"""

import io
import logging
import os
import socket
import sys
from pathlib import Path

# Flush immediately (Task Scheduler file-redirect mode)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", write_through=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", write_through=True)

# Cover DNS-level hangs that requests.timeout doesn't catch
socket.setdefaulttimeout(60)

# Requests otherwise uses certifi instead of the Windows trusted root store.
_windows_truststore_enabled = False
if sys.platform == "win32":
    try:
        import truststore

        truststore.inject_into_ssl()
        _windows_truststore_enabled = True
    except ImportError:
        pass

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# ── Logging setup ─────────────────────────────────────────────
from datetime import datetime

_date_str = datetime.now().strftime("%Y%m%d")
log_file = LOGS_DIR / f"compliance_{_date_str}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


def main() -> int:
    log.info("=" * 60)
    log.info("Japan Marketplace Compliance Briefing — START")
    if sys.platform == "win32":
        if _windows_truststore_enabled:
            log.info("TLS: Windows trust store enabled")
        else:
            log.warning("TLS: truststore is not installed; using the certifi CA bundle")

    try:
        from compliance_briefing import ComplianceConfig, CompliancePipeline
        cfg = ComplianceConfig()
        log.info("Config: %s", cfg.masked_log_line())

        pipeline = CompliancePipeline(cfg)
        result = pipeline.run()

        log.info("Result: %s", result)
        log.info("Japan Marketplace Compliance Briefing — END")
        return 1 if result.get("status") == "failed" else 0
    except KeyboardInterrupt:
        log.warning("Interrupted by user")
        return 130
    except Exception:
        log.exception("Fatal error in compliance pipeline")
        return 1


if __name__ == "__main__":
    sys.exit(main())
