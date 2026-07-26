"""
Abstract base for all compliance data collectors.
"""

import json
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import requests

if TYPE_CHECKING:
    from .config import ComplianceConfig

log = logging.getLogger(__name__)


class CollectorError(Exception):
    pass


CollectionStatus = Literal["ok", "partial", "failed"]


@dataclass(frozen=True)
class CollectionResult:
    items: list[dict]
    status: CollectionStatus
    error_msg: str | None = None


class BaseCollector(ABC):
    source_id: str = ""

    def __init__(self, cfg: "ComplianceConfig"):
        self.cfg = cfg
        self._partial_errors: list[str] = []
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        })

    @abstractmethod
    def _fetch_live(self) -> list[dict]:
        """Fetch real data from the source. Returns list of RawItem dicts."""

    def _load_fixture(self) -> list[dict]:
        """Load fixture data for dry-run mode."""
        fixture_path = self.cfg.fixtures_dir / f"{self.source_id}_response.json"
        if not fixture_path.exists():
            raise CollectorError(f"No fixture at {fixture_path}")
        try:
            data = json.loads(fixture_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            return data.get("items", data.get("data", []))
        except Exception as e:
            raise CollectorError(f"Failed to load fixture: {e}") from e

    def collect(self) -> CollectionResult:
        """
        Main entry point. Returns items with ok / partial / failed status.
        In dry_run mode, uses fixture data.
        """
        self._partial_errors.clear()

        if self.cfg.dry_run:
            log.info("[%s] DRY RUN — loading fixture", self.source_id)
            try:
                return CollectionResult(self._load_fixture(), "ok")
            except CollectorError as exc:
                error_msg = self._safe_error(exc)
                log.error("[%s] Dry-run fixture error: %s", self.source_id, error_msg)
                return CollectionResult([], "failed", error_msg)

        last_error = "collection failed"
        for attempt in range(1, 4):
            try:
                items = self._fetch_live()
                if self._partial_errors:
                    error_msg = "; ".join(self._partial_errors)[:2000]
                    log.warning(
                        "[%s] Collected %d items with partial errors: %s",
                        self.source_id,
                        len(items),
                        error_msg,
                    )
                    return CollectionResult(items, "partial", error_msg)
                log.info("[%s] Collected %d items", self.source_id, len(items))
                return CollectionResult(items, "ok")
            except CollectorError as e:
                last_error = self._safe_error(e)
                log.error("[%s] CollectorError: %s", self.source_id, last_error)
                return CollectionResult([], "failed", last_error)
            except requests.exceptions.Timeout:
                last_error = f"Timeout (attempt {attempt}/3)"
                log.warning("[%s] Timeout (attempt %d/3)", self.source_id, attempt)
                if attempt < 3:
                    time.sleep(5 * attempt)
            except requests.exceptions.RequestException as e:
                last_error = self._safe_error(e)
                log.error("[%s] Request error: %s", self.source_id, last_error)
                return CollectionResult([], "failed", last_error)
            except Exception as e:
                last_error = self._safe_error(e)
                log.error(
                    "[%s] Unexpected error: %s",
                    self.source_id,
                    last_error,
                    exc_info=True,
                )
                return CollectionResult([], "failed", last_error)
        return CollectionResult([], "failed", last_error)

    def _record_partial_error(self, message: str | Exception) -> None:
        """Record a recoverable sub-request error for the current collection."""
        error_msg = self._safe_error(message)
        if error_msg not in self._partial_errors:
            self._partial_errors.append(error_msg)

    def _safe_error(self, error: str | Exception) -> str:
        """Return a bounded error string with configured secrets redacted."""
        message = str(error)
        secret_fields = (
            "brave_api_key",
            "safety_korea_api_key",
            "anthropic_api_key",
            "openai_api_key",
            "slack_bot_token",
            "compliance_apps_script_url",
        )
        for field in secret_fields:
            secret = getattr(self.cfg, field, "")
            if secret:
                message = message.replace(secret, "***")
        return message[:2000]

    def _get(self, url: str, params: dict | None = None, headers: dict | None = None,
             timeout: int = 30) -> requests.Response:
        resp = self._session.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        # requests follows the HTTP/1.1 default of ISO-8859-1 for text/* when
        # the server omits a charset. Japanese government sites commonly send
        # UTF-8 HTML without that header, which otherwise produces mojibake.
        declared_encoding = (resp.encoding or "").lower().replace("_", "-")
        if not declared_encoding or declared_encoding in {
            "iso-8859-1",
            "latin-1",
            "latin1",
        }:
            detected_encoding = resp.apparent_encoding
            if detected_encoding:
                resp.encoding = detected_encoding
        return resp

    @staticmethod
    def _raw_item(
        source_id: str,
        external_id: str,
        url: str,
        title: str,
        body: str,
        category: str,
        country: str,
        published_at: str | None = None,
        marketplace: str | None = None,
        brand: str | None = None,
        extra: dict | None = None,
    ) -> dict:
        return {
            "source_id": source_id,
            "external_id": external_id,
            "url": url,
            "title": title,
            "body": body,
            "category": category,
            "country": country,
            "published_at": published_at,
            "marketplace": marketplace,
            "brand": brand,
            "extra": extra or {},
        }
