"""Tests for collector result status handling."""

from types import SimpleNamespace

import requests

from compliance_briefing.collector_base import BaseCollector, CollectorError


class _HealthyCollector(BaseCollector):
    source_id = "healthy"

    def _fetch_live(self) -> list[dict]:
        return [{"external_id": "1"}]


class _PartialCollector(BaseCollector):
    source_id = "partial"

    def _fetch_live(self) -> list[dict]:
        self._record_partial_error("second query timed out")
        return [{"external_id": "1"}]


class _FailedCollector(BaseCollector):
    source_id = "failed"

    def _fetch_live(self) -> list[dict]:
        raise CollectorError("source unavailable")


def _cfg(tmp_path):
    return SimpleNamespace(dry_run=False, fixtures_dir=tmp_path)


def test_collect_returns_ok_result(tmp_path):
    result = _HealthyCollector(_cfg(tmp_path)).collect()

    assert result.status == "ok"
    assert result.items == [{"external_id": "1"}]
    assert result.error_msg is None


def test_collect_returns_partial_result_with_items_and_error(tmp_path):
    result = _PartialCollector(_cfg(tmp_path)).collect()

    assert result.status == "partial"
    assert result.items == [{"external_id": "1"}]
    assert result.error_msg == "second query timed out"


def test_collect_returns_failed_result(tmp_path):
    result = _FailedCollector(_cfg(tmp_path)).collect()

    assert result.status == "failed"
    assert result.items == []
    assert "source unavailable" in result.error_msg


def test_get_corrects_default_latin1_for_utf8_html(tmp_path):
    collector = _HealthyCollector(_cfg(tmp_path))
    response = requests.Response()
    response.status_code = 200
    response._content = "消費者庁が措置命令".encode("utf-8")
    response.encoding = "ISO-8859-1"
    collector._session.get = lambda *args, **kwargs: response

    fetched = collector._get("https://example.com")

    assert fetched.encoding.lower() == "utf-8"
    assert fetched.text == "消費者庁が措置命令"
