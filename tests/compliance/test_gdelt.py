"""Tests for GDELT runtime budgeting and rate-limit handling."""

from types import SimpleNamespace

import requests

import compliance_briefing.collectors.gdelt as gdelt_module
from compliance_briefing.collectors.gdelt import GDELTCollector
from compliance_briefing.config import ComplianceConfig


def _cfg() -> ComplianceConfig:
    cfg = ComplianceConfig()
    cfg.dry_run = False
    cfg.gdelt_time_budget_seconds = 60
    cfg.gdelt_retry_after_cap_seconds = 5
    return cfg


def test_successful_queries_have_no_fixed_sleep(monkeypatch):
    cfg = _cfg()
    collector = GDELTCollector(cfg)
    sleeps: list[float] = []

    monkeypatch.setattr(
        gdelt_module,
        "_GDELT_QUERIES",
        [("query-1", "Japanese", "regulation"),
         ("query-2", "Korean", "safety")],
    )
    monkeypatch.setattr(gdelt_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        collector,
        "_get",
        lambda *_args, **_kwargs: SimpleNamespace(json=lambda: {"articles": []}),
    )

    result = collector.collect()

    assert result.status == "ok"
    assert sleeps == []


def test_429_retries_once_using_bounded_retry_after(monkeypatch):
    cfg = _cfg()
    collector = GDELTCollector(cfg)
    sleeps: list[float] = []
    calls = 0

    response_429 = requests.Response()
    response_429.status_code = 429
    response_429.headers["Retry-After"] = "120"
    rate_limit_error = requests.HTTPError(
        "rate limited",
        response=response_429,
    )

    def fake_get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise rate_limit_error
        return SimpleNamespace(json=lambda: {"articles": []})

    monkeypatch.setattr(
        gdelt_module,
        "_GDELT_QUERIES",
        [("query-1", "Japanese", "regulation")],
    )
    monkeypatch.setattr(gdelt_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(collector, "_get", fake_get)

    result = collector.collect()

    assert result.status == "ok"
    assert calls == 2
    assert sleeps == [5.0]


def test_time_budget_stops_remaining_queries(monkeypatch):
    cfg = _cfg()
    cfg.gdelt_time_budget_seconds = 1
    collector = GDELTCollector(cfg)
    monotonic_values = iter([100.0, 102.0])

    monkeypatch.setattr(
        gdelt_module,
        "_GDELT_QUERIES",
        [("query-1", "Japanese", "regulation")],
    )
    monkeypatch.setattr(
        gdelt_module.time,
        "monotonic",
        lambda: next(monotonic_values),
    )

    result = collector.collect()

    assert result.status == "partial"
    assert result.items == []
    assert "time budget" in result.error_msg.lower()
