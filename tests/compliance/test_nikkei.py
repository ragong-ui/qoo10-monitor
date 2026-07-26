"""Tests for Nikkei date parsing and live-result freshness filtering."""

from datetime import date, timedelta
from types import SimpleNamespace

import compliance_briefing.collectors.nikkei as nikkei_module
from compliance_briefing.collectors.nikkei import (
    NikkeiCollector,
    _is_recent,
    _parse_article_date,
)
from compliance_briefing.config import ComplianceConfig


def test_parse_article_date_normalizes_supported_formats():
    assert _parse_article_date("2026年7月23日 04時00分") == "2026-07-23"
    assert _parse_article_date("掲載日 2026/07/03") == "2026-07-03"
    assert _parse_article_date("日付なし") is None


def test_is_recent_rejects_old_undated_and_future_articles():
    today = date(2026, 7, 26)

    assert _is_recent("2026-07-20", lookback_days=7, today=today) is True
    assert _is_recent("2026-07-18", lookback_days=7, today=today) is False
    assert _is_recent(None, lookback_days=7, today=today) is False
    assert _is_recent("2026-07-28", lookback_days=7, today=today) is False


def test_search_keeps_only_recent_dated_articles(monkeypatch):
    recent = date.today() - timedelta(days=1)
    old = date.today() - timedelta(days=30)
    html = f"""
    <div>
      <a href="/article/RECENT123/">消費者庁、景品表示法違反に措置命令</a>
      <span>{recent.year}年{recent.month}月{recent.day}日</span>
    </div>
    <div>
      <a href="/article/OLD123/">消費者庁、過去の景品表示法違反</a>
      <span>{old.year}年{old.month}月{old.day}日</span>
    </div>
    <div>
      <a href="/article/UNDATED123/">消費者庁、掲載日不明の記事</a>
    </div>
    """

    cfg = ComplianceConfig()
    cfg.dry_run = False
    cfg.nikkei_lookback_days = 7
    collector = NikkeiCollector(cfg)
    monkeypatch.setattr(
        collector,
        "_get",
        lambda *_args, **_kwargs: SimpleNamespace(text=html),
    )

    items = collector._search("消費者庁 措置命令", set())

    assert [item["external_id"] for item in items] == [
        "https://www.nikkei.com/article/RECENT123/"
    ]
    assert items[0]["published_at"] == recent.isoformat()


def test_collect_reports_partial_when_one_request_fails(monkeypatch):
    recent = date.today() - timedelta(days=1)
    html = f"""
    <div>
      <a href="/article/RECENT123/">消費者庁、景品表示法違反に措置命令</a>
      <span>{recent.year}年{recent.month}月{recent.day}日</span>
    </div>
    """

    cfg = ComplianceConfig()
    cfg.dry_run = False
    cfg.nikkei_lookback_days = 7
    collector = NikkeiCollector(cfg)

    monkeypatch.setattr(nikkei_module, "_SEARCH_QUERIES", ["success", "failure"])
    monkeypatch.setattr(nikkei_module, "_CATEGORY_URLS", [])
    monkeypatch.setattr(nikkei_module.time, "sleep", lambda *_args: None)

    def fake_get(_url, params=None, **_kwargs):
        if params and params["keyword"] == "failure":
            raise RuntimeError("forced query failure")
        return SimpleNamespace(text=html)

    monkeypatch.setattr(collector, "_get", fake_get)

    result = collector.collect()

    assert result.status == "partial"
    assert len(result.items) == 1
    assert "forced query failure" in result.error_msg


def test_collect_reports_failed_when_all_requests_fail(monkeypatch):
    cfg = ComplianceConfig()
    cfg.dry_run = False
    collector = NikkeiCollector(cfg)

    monkeypatch.setattr(nikkei_module, "_SEARCH_QUERIES", ["failure"])
    monkeypatch.setattr(nikkei_module, "_CATEGORY_URLS", [])
    monkeypatch.setattr(nikkei_module.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(
        collector,
        "_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("source unavailable")
        ),
    )

    result = collector.collect()

    assert result.status == "failed"
    assert result.items == []
    assert "All Nikkei requests failed" in result.error_msg
