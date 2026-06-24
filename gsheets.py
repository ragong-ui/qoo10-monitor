"""
Qoo10 SNS モニタリング — Google Sheets 연동 (Apps Script Web App 방식)
서비스 계정 / Google Cloud Console 불필요
HTTP POST 한 번으로 Apps Script가 Sheets에 직접 기록
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

APPS_SCRIPT_URL = os.getenv("GOOGLE_APPS_SCRIPT_URL", "")

HEADERS_GOOGLE = [
    "검색일 / 検索日", "검색 키워드", "URL", "개요 / 概要",
    "Qoo10 상품 / 商品P", "위험도 / 危険度", "오탐지여부", "Status",
    "검색확인 / 検索確認", "아카이브 / Archive",
]
HEADERS_X = [
    "검색일 / 検索日", "검색 쿼리 / クエリ", "게시물 URL / 投稿URL", "게시물 내용 / 投稿内容",
    "Qoo10 상품 URL", "위험도 / 危険度", "오탐지여부", "Status",
    "검색확인 / 検索確認", "아카이브 / Archive",
]


def write_to_sheets(rows: list, kr_list: list, sheet_type: str = "google"):
    """
    Apps Script Web App에 POST하여 Google Sheets에 모니터링 결과를 기록.

    Parameters
    ----------
    rows      : run_searches() / run_x_searches() 반환값
    kr_list   : _translate_batch_ja_ko() 반환값
    sheet_type: "google" or "x"
    """
    if not APPS_SCRIPT_URL:
        print("  [SKIP] GOOGLE_APPS_SCRIPT_URL 미설정 (.env 확인)")
        return
    if not rows:
        return

    headers  = HEADERS_GOOGLE if sheet_type == "google" else HEADERS_X
    ws_title = "Google モニタリング" if sheet_type == "google" else "X モニタリング"

    def _bilingual(jp: str, kr: str) -> str:
        kr = (kr or "").strip()
        return f"{jp}\n{'─'*22}\n[한국어]\n{kr}" if kr and kr != jp.strip() else jp

    batch_rows = []
    for row, kr in zip(rows, kr_list):
        summary    = _bilingual(row["summary"], kr)
        kw_or_q    = row.get("keyword") or row.get("query", "")
        search_url = row.get("search_url", "")

        search_f  = (
            f'=HYPERLINK("{search_url}","Google検索")' if sheet_type == "google" and search_url
            else f'=HYPERLINK("{search_url}","Yahoo検索")' if search_url
            else ""
        )
        wayback_f = f'=HYPERLINK("https://web.archive.org/web/{row["url"]}","Wayback")'

        batch_rows.append([
            row["date"], kw_or_q, row["url"],
            summary, row["qoo10_link"], row["likelihood"],
            "", "New", search_f, wayback_f,
        ])

    payload = {
        "sheet":   ws_title,
        "headers": headers,
        "rows":    batch_rows,
    }

    try:
        resp = requests.post(APPS_SCRIPT_URL, json=payload, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") == "ok":
            print(f"  Google Sheets 기록 완료: {result.get('rows', len(batch_rows))}행 → '{ws_title}' 탭")
        else:
            print(f"  [WARN] Apps Script 응답 이상: {result}")
    except Exception as e:
        print(f"  [ERROR] Google Sheets 전송 실패: {e}")
