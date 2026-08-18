"""기존 Google/X 시트의 PENDING 행을 AI로 2차 분석해 결과를 되쓴다."""

from __future__ import annotations

import argparse
import os
from collections import Counter
from typing import Any

from dotenv import load_dotenv

from apps_script_client import get_json_with_retry, post_json_with_retry
from sns_enrichment import SnsAiClassifier, extract_detection_evidence
from tls_utils import enable_system_trust_store


load_dotenv()
enable_system_trust_store()

APPS_SCRIPT_URL = os.getenv("GOOGLE_APPS_SCRIPT_URL", "").strip()
FRAUD_WORDS = [
    "偽物", "ニセモノ", "にせもの", "パチモン", "パチもん", "パチモノ",
    "コピー", "fake", "偽造品", "模倣品", "コピー商品", "模造品", "詐欺",
    "販売禁止商品", "規約違反", "強制返金", "中国",
]
SHEET_SPECS = {
    "google": {
        "sheet": "Google モニタリング",
        "source": "google",
        "keyword": "검색 키워드",
        "url": "URL",
        "summary": "개요 / 概要",
        "qoo10": "Qoo10 상품 / 商品P",
    },
    "x": {
        "sheet": "X モニタリング",
        "source": "x",
        "keyword": "검색 쿼리 / クエリ",
        "url": "게시물 URL / 投稿URL",
        "summary": "게시물 내용 / 投稿内容",
        "qoo10": "Qoo10 상품 URL",
    },
}


def pending_rows(
    rows: list[dict[str, Any]],
    *,
    include_errors: bool = False,
) -> list[dict[str, Any]]:
    labels = {"PENDING", ""}
    if include_errors:
        labels.add("ERROR")
    return [
        row for row in rows
        if str(row.get("AI 판정 / AI判定", "")).strip().upper() in labels
        and str(row.get("_row_index", "")).isdigit()
    ]


def prepare_item(row: dict[str, Any], spec: dict[str, str]) -> dict[str, Any]:
    text = str(row.get(spec["summary"], ""))
    evidence = str(row.get("탐지 근거 / 検知根拠", "")) or extract_detection_evidence(
        text,
        FRAUD_WORDS,
    )
    return {
        "row_id": f"{spec['source']}:{int(row['_row_index'])}",
        "text": text,
        "source": spec["source"],
        "keyword": str(row.get(spec["keyword"], "")),
        "evidence": evidence,
        "source_url": str(row.get(spec["url"], "")),
        "qoo10_link": str(row.get(spec["qoo10"], "")),
    }


def build_update(
    row: dict[str, Any],
    spec: dict[str, str],
    result,
) -> dict[str, Any]:
    return {
        "sheet": spec["sheet"],
        "row_index": int(row["_row_index"]),
        "source_url": str(row.get(spec["url"], "")),
        "ai_label": result.label,
        "ai_confidence": result.confidence,
        "ai_reason": result.reason,
        "ai_evidence": result.evidence,
        "ai_model": result.model,
    }


def analyze_row(
    classifier: SnsAiClassifier,
    row: dict[str, Any],
    spec: dict[str, str],
) -> dict[str, Any]:
    item = prepare_item(row, spec)
    result = classifier.analyze(
        text=item["text"],
        source=item["source"],
        keyword=item["keyword"],
        evidence=item["evidence"],
        source_url=item["source_url"],
        qoo10_link=item["qoo10_link"],
    )
    return build_update(row, spec, result)


def analyze_rows_batch(
    classifier: SnsAiClassifier,
    targets: list[tuple[dict[str, Any], dict[str, str]]],
) -> list[dict[str, Any]]:
    if len(targets) == 1:
        row, spec = targets[0]
        return [analyze_row(classifier, row, spec)]
    items = [prepare_item(row, spec) for row, spec in targets]
    results = classifier.analyze_batch(items)
    return [
        build_update(row, spec, results[item["row_id"]])
        for (row, spec), item in zip(targets, items)
    ]

def chunks(values: list[dict[str, Any]], size: int = 100):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Qoo10 SNS PENDING 행 AI 2차 분석")
    parser.add_argument("--sheet", choices=["all", "google", "x"], default="all")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("SNS_AI_BATCH_SIZE", "20")),
        help="Sonnet 한 번에 판정할 행 수(기본 20)",
    )
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="AI 호출만 하고 Sheets에는 쓰지 않음")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    classifier = SnsAiClassifier()
    limit = classifier.max_rows if args.limit is None else max(0, args.limit)
    batch_size = max(1, min(20, args.batch_size))

    if not APPS_SCRIPT_URL:
        print("[ERROR] GOOGLE_APPS_SCRIPT_URL 미설정")
        return 2
    if not classifier.enabled:
        print(
            "[ERROR] SNS AI 비활성: provider 인증 또는 실행 환경을 확인하세요. "
            f"provider={classifier.provider or 'disabled'}, model={classifier.model or 'unset'}"
        )
        return 2
    if limit == 0:
        print("[SKIP] AI 분석 limit=0")
        return 0

    selected = ["google", "x"] if args.sheet == "all" else [args.sheet]
    targets: list[tuple[dict[str, Any], dict[str, str]]] = []
    pending_counts: dict[str, int] = {}
    for key in selected:
        spec = SHEET_SPECS[key]
        body = get_json_with_retry(
            APPS_SCRIPT_URL,
            {"sheet": spec["sheet"]},
            timeout=60,
            max_attempts=5,
        )
        if body.get("status") == "error":
            raise RuntimeError(body.get("message", "Apps Script data load failed"))
        candidates = pending_rows(
            body.get("data", []),
            include_errors=args.retry_errors,
        )
        pending_counts[key] = len(candidates)
        remaining = max(0, limit - len(targets))
        targets.extend((row, spec) for row in candidates[:remaining])
        if len(targets) >= limit:
            break

    if not targets:
        print("[OK] 재분석할 PENDING 행이 없습니다.")
        return 0

    print(
        "PENDING 집계: "
        + ", ".join(f"{key}={value}" for key, value in pending_counts.items())
        + f", 처리대상={len(targets)}, batch_size={batch_size}"
    )
    stats: Counter[str] = Counter()
    failed = 0
    changed = 0
    processed = 0
    target_batches = list(chunks(targets, batch_size))

    for batch_number, target_batch in enumerate(target_batches, start=1):
        updates = analyze_rows_batch(classifier, target_batch)
        for update in updates:
            stats[update["ai_label"]] += 1
            processed += 1
            print(
                f"[{processed}/{len(targets)}] {update['sheet']} "
                f"row={update['row_index']} {update['ai_label']} "
                f"confidence={update['ai_confidence'] or '-'}"
            )

        if args.dry_run:
            print(f"[BATCH {batch_number}/{len(target_batches)}] DRY-RUN {len(updates)}행")
            continue

        body = post_json_with_retry(
            APPS_SCRIPT_URL,
            {"action": "ai_batch_update", "changes": updates},
            timeout=120,
            max_attempts=5,
        )
        changed += int(body.get("changed", 0))
        failed += len(body.get("errors", []))
        if body.get("status") not in {"ok", "partial"}:
            raise RuntimeError(body.get("message", "AI batch update failed"))
        print(
            f"[BATCH {batch_number}/{len(target_batches)}] 저장 완료 "
            f"changed={body.get('changed', 0)}, failed={len(body.get('errors', []))}"
        )

    print("AI 판정 집계: " + ", ".join(f"{k}={v}" for k, v in sorted(stats.items())))
    if args.dry_run:
        print(f"[DRY-RUN] {len(targets)}행 분석 완료, Google Sheets 미반영")
        return 0

    print(f"[OK] AI 2차 분석 반영 완료: changed={changed}, failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())