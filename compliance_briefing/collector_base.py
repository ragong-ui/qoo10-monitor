"""
Abstract base for all compliance data collectors.
"""

import json
import time
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from .config import ComplianceConfig

log = logging.getLogger(__name__)


class CollectorError(Exception):
    pass


class BaseCollector(ABC):
    source_id: str = ""

    def __init__(self, cfg: "ComplianceConfig"):
        self.cfg = cfg
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
            log.warning("[%s] No fixture at %s — returning empty list", self.source_id, fixture_path)
            return []
        try:
            data = json.loads(fixture_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            return data.get("items", data.get("data", []))
        except Exception as e:
            log.error("[%s] Failed to load fixture: %s", self.source_id, e)
            return []

    def collect(self) -> tuple[list[dict], bool]:
        """
        Main entry point. Returns (items, success).
        In dry_run mode, uses fixture data.
        """
        if self.cfg.dry_run:
            log.info("[%s] DRY RUN — loading fixture", self.source_id)
            return self._load_fixture(), True

        for attempt in range(1, 4):
            try:
                items = self._fetch_live()
                log.info("[%s] Collected %d items", self.source_id, len(items))
                return items, True
            except CollectorError as e:
                log.error("[%s] CollectorError: %s", self.source_id, e)
                return [], False
            except requests.exceptions.Timeout:
                log.warning("[%s] Timeout (attempt %d/3)", self.source_id, attempt)
                if attempt < 3:
                    time.sleep(5 * attempt)
            except requests.exceptions.RequestException as e:
                log.error("[%s] Request error: %s", self.source_id, e)
                return [], False
            except Exception as e:
                log.error("[%s] Unexpected error: %s", self.source_id, e, exc_info=True)
                return [], False
        return [], False

    def _get(self, url: str, params: dict | None = None, headers: dict | None = None,
             timeout: int = 30) -> requests.Response:
        resp = self._session.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
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
