from __future__ import annotations

from pathlib import Path

import sns_page_verifier
from sns_page_verifier import (
    PageVerification,
    proximity_evidence,
    verify_rows,
)


def test_proximity_evidence_requires_same_nearby_context():
    assert proximity_evidence(
        "Qoo10で購入した商品は偽物でした。返金を依頼しました。",
        "Qoo10 偽物",
        ["偽物"],
    )
    assert not proximity_evidence(
        "Qoo10のセール情報です。" + ("別の話題です。" * 40) + "偽物の一般記事です。",
        "Qoo10 偽物",
        ["偽物"],
    )


def test_megawari_uses_megawari_anchor():
    evidence = proximity_evidence(
        "メガ割で買った商品が偽物でした。",
        "メガ割",
        ["偽物"],
    )
    assert "メガ割" in evidence
    assert "偽物" in evidence


def test_verify_rows_filters_only_mismatch(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SNS_PAGE_VERIFY_ENABLED", "true")
    monkeypatch.setenv("SNS_PAGE_VERIFY_FILTER_MISMATCH", "true")
    monkeypatch.setenv("SNS_PAGE_VERIFY_MAX_ROWS", "10")

    results = iter([
        PageVerification("MATCH", "matched", evidence="Qoo10 偽物", verified_text="Qoo10 偽物"),
        PageVerification("MISMATCH", "not matched", verified_text="unrelated body"),
        PageVerification("UNAVAILABLE", "blocked"),
    ])

    monkeypatch.setattr(
        sns_page_verifier.SnsPageVerifier,
        "start",
        lambda self: True,
    )
    monkeypatch.setattr(
        sns_page_verifier.SnsPageVerifier,
        "close",
        lambda self: None,
    )
    monkeypatch.setattr(
        sns_page_verifier.SnsPageVerifier,
        "verify",
        lambda self, **kwargs: next(results),
    )
    rows = [
        {"date": "2026-08-26", "keyword": "Qoo10 偽物", "url": "https://example.com/1"},
        {"date": "2026-08-26", "keyword": "Qoo10 偽物", "url": "https://example.com/2"},
        {"date": "2026-08-26", "keyword": "Qoo10 偽物", "url": "https://x.com/a/status/3"},
    ]

    kept = verify_rows(rows, ["偽物"], source="google", results_dir=tmp_path)

    assert [row["url"] for row in kept] == [
        "https://example.com/1",
        "https://x.com/a/status/3",
    ]
    assert kept[0]["page_verification"] == "MATCH"
    assert kept[1]["page_verification"] == "UNAVAILABLE"
    rejected = list(tmp_path.glob("page_verification_rejected_google_*.json"))
    assert len(rejected) == 1


def test_verify_rows_is_fail_open_when_disabled(monkeypatch):
    monkeypatch.setenv("SNS_PAGE_VERIFY_ENABLED", "false")
    rows = [{"keyword": "Qoo10 偽物", "url": "https://example.com"}]
    kept = verify_rows(rows, ["偽物"], source="google")
    assert kept == rows
    assert rows[0]["page_verification"] == "SKIPPED"
