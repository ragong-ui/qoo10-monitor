"""
Deduplication — fingerprint generation and cluster merging.
"""

import hashlib
import re
import unicodedata
from collections import defaultdict


def make_fingerprint(source_id: str, external_id: str) -> str:
    """Primary fingerprint: SHA-256(source_id + ':' + external_id)."""
    raw = f"{source_id}:{external_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize(text: str) -> str:
    """Lowercased, NFKC-normalized, punctuation-stripped text for fuzzy compare."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[\s\-_/・：:、。！？!?]+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return text.strip()


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    norm = _normalize(text)
    if len(norm) < n:
        return {norm}
    return {norm[i:i+n] for i in range(len(norm) - n + 1)}


def jaccard(a: str, b: str, n: int = 3) -> float:
    sa = _char_ngrams(a, n)
    sb = _char_ngrams(b, n)
    if not sa and not sb:
        return 1.0
    intersection = len(sa & sb)
    union = len(sa | sb)
    return intersection / union if union else 0.0


# ── Cluster merging ────────────────────────────────────────────────────────────

def cluster_items(
    items: list[dict],
    title_threshold: float = 0.80,
) -> list[list[dict]]:
    """
    Group raw items that look like the same real-world event.
    Returns list of clusters (each cluster = list of raw_items).
    """
    clusters: list[list[dict]] = []

    for item in items:
        placed = False
        for cluster in clusters:
            rep = cluster[0]
            if (item.get("category") == rep.get("category")
                    and item.get("country") == rep.get("country")):
                sim = jaccard(item.get("title", ""), rep.get("title", ""))
                if sim >= title_threshold:
                    cluster.append(item)
                    placed = True
                    break
        if not placed:
            clusters.append([item])

    return clusters


def pick_canonical(cluster: list[dict]) -> dict:
    """Choose the most authoritative item in a cluster as the canonical one."""
    _priority_sources = [
        "nite", "caa", "egov", "meti", "jftc", "ppc", "mhlw",
        "safety_korea_mfds", "safety_korea_kats", "safety_korea_kca",
    ]
    for src in _priority_sources:
        for item in cluster:
            if item.get("source_id") == src:
                return item
    return cluster[0]


def dedup_items(items: list[dict]) -> list[tuple[dict, list[str]]]:
    """
    Full dedup pipeline.
    Returns list of (canonical_item, list_of_all_source_ids_in_cluster).
    """
    # First pass: exact fingerprint dedup
    seen: dict[str, dict] = {}
    for item in items:
        fp = make_fingerprint(item["source_id"], item["external_id"])
        item["fingerprint"] = fp
        if fp not in seen:
            seen[fp] = item

    unique = list(seen.values())

    # Second pass: semantic clustering
    clusters = cluster_items(unique)

    result = []
    for cluster in clusters:
        canonical = pick_canonical(cluster)
        all_sources = list({i["source_id"] for i in cluster})
        result.append((canonical, all_sources))

    return result
