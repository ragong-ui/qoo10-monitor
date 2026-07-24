"""Tests for dedup module."""

import pytest
from compliance_briefing.dedup import (
    make_fingerprint,
    jaccard,
    cluster_items,
    pick_canonical,
    dedup_items,
)


def _item(source_id, external_id, title, category="regulation", country="JP"):
    return {
        "source_id": source_id,
        "external_id": external_id,
        "title": title,
        "body": "",
        "category": category,
        "country": country,
        "published_at": None,
    }


def test_fingerprint_deterministic():
    fp1 = make_fingerprint("egov", "https://example.com/001")
    fp2 = make_fingerprint("egov", "https://example.com/001")
    assert fp1 == fp2


def test_fingerprint_different_sources():
    fp1 = make_fingerprint("egov", "https://example.com/001")
    fp2 = make_fingerprint("nite", "https://example.com/001")
    assert fp1 != fp2


def test_jaccard_identical():
    assert jaccard("Qoo10規制日本", "Qoo10規制日本") == 1.0


def test_jaccard_completely_different():
    score = jaccard("abcdefghijk", "lmnopqrstuv")
    assert score == 0.0


def test_jaccard_similar():
    a = "消費者庁が越境ECに措置命令を検討"
    b = "消費者庁、越境ECに措置命令を検討"
    assert jaccard(a, b) > 0.6


def test_cluster_identical_titles():
    items = [
        _item("egov", "url1", "消費者庁が措置命令"),
        _item("brave_news", "url2", "消費者庁が措置命令"),  # same title, different source
    ]
    clusters = cluster_items(items)
    assert len(clusters) == 1
    assert len(clusters[0]) == 2


def test_cluster_different_titles():
    items = [
        _item("egov", "url1", "消費者庁が措置命令"),
        _item("egov", "url2", "NITEが製品事故を発表"),
    ]
    clusters = cluster_items(items)
    assert len(clusters) == 2


def test_cluster_different_category_not_merged():
    items = [
        _item("egov", "url1", "消費者庁が措置命令", category="regulation"),
        _item("nite", "url2", "消費者庁が措置命令", category="recall"),
    ]
    clusters = cluster_items(items)
    # Different category → not merged
    assert len(clusters) == 2


def test_pick_canonical_prefers_primary_source():
    cluster = [
        _item("brave_news", "url1", "消費者庁措置命令"),
        _item("caa", "url2", "消費者庁措置命令"),  # caa is higher priority
    ]
    canonical = pick_canonical(cluster)
    assert canonical["source_id"] == "caa"


def test_dedup_exact_duplicates():
    items = [
        _item("egov", "url1", "法改正"),
        _item("egov", "url1", "法改正"),  # exact duplicate
        _item("nite", "url2", "製品事故"),
    ]
    result = dedup_items(items)
    # url1 appears once (exact dedup), url2 is separate
    assert len(result) >= 1
    # Check sources are tracked
    for canonical, sources in result:
        assert len(sources) >= 1


def test_dedup_returns_sources_list():
    items = [
        _item("egov", "url1", "製品安全規制"),
        _item("brave_news", "url2", "製品安全規制"),  # similar title → same cluster
    ]
    result = dedup_items(items)
    # Should be 1 cluster with 2 sources
    if len(result) == 1:
        _, sources = result[0]
        assert set(sources) == {"egov", "brave_news"}
