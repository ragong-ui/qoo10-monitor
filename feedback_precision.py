"""누적 Google/X 피드백을 감사하고 활성 정밀도 규칙을 백테스트한다.

O=오탐지, X=실검지로 해석한다. 사람 판정과 충돌하는 규칙이 있으면 종료
코드 2를 반환하므로 향후 필터 갱신 전 안전 점검에 사용할 수 있다.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv

from apps_script_client import get_json_with_retry
from sns_precision import exclusion_reason
from tls_utils import enable_system_trust_store

load_dotenv()

SPECS = {
    "google": {
        "sheet": "Google モニタリング",
        "url": "URL",
        "text": "개요 / 概要",
    },
    "x": {
        "sheet": "X モニタリング",
        "url": "게시물 URL / 投稿URL",
        "text": "게시물 내용 / 投稿内容",
    },
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _load_file(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload.get("data", payload.get("rows", []))


def _load_live(sheet: str) -> list[dict[str, Any]]:
    enable_system_trust_store()
    url = os.getenv("GOOGLE_APPS_SCRIPT_URL", "").strip()
    payload = get_json_with_retry(url, {"sheet": sheet}, timeout=90, max_attempts=5)
    return payload.get("data", payload.get("rows", []))


def audit(rows: list[dict[str, Any]], source: str) -> dict[str, Any]:
    spec = SPECS[source]
    labels = Counter(_clean(row.get("오탐지여부")).upper() or "BLANK" for row in rows)
    ai_labels = Counter(_clean(row.get("AI 판정 / AI判定")).upper() or "BLANK" for row in rows)
    excluded = Counter()
    conflicts: list[dict[str, str]] = []
    domain_labels: dict[str, Counter] = defaultdict(Counter)

    for row in rows:
        human = _clean(row.get("오탐지여부")).upper() or "BLANK"
        url = _clean(row.get(spec["url"]))
        text = _clean(row.get(spec["text"]))
        host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
        domain_labels[host][human] += 1
        reason = exclusion_reason(url=url, text=text, source=source)
        if reason:
            excluded[human] += 1
            if human == "X":
                conflicts.append({"url": url, "reason": reason})

    candidates = []
    if source == "google":
        for host, counts in domain_labels.items():
            if counts["O"] >= 3 and counts["X"] == 0:
                candidates.append({"domain": host, "O": counts["O"], "X": 0})
        candidates.sort(key=lambda item: (-item["O"], item["domain"]))

    return {
        "source": source,
        "rows": len(rows),
        "human": dict(labels),
        "ai": dict(ai_labels),
        "active_rule_excluded": dict(excluded),
        "active_rule_conflicts": conflicts,
        "strict_domain_candidates": candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SNS 누적 피드백 정밀도 감사")
    parser.add_argument("--google-json", type=Path)
    parser.add_argument("--x-json", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    reports = []
    for source, path in (("google", args.google_json), ("x", args.x_json)):
        rows = _load_file(path) if path else _load_live(SPECS[source]["sheet"])
        reports.append(audit(rows, source))

    text = json.dumps(reports, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 2 if any(report["active_rule_conflicts"] for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
