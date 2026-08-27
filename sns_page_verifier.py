"""Playwright 기반 Qoo10 SNS 의심 URL 원문 2차 검증.

검색 스니펫을 1차 후보로 유지하되, 공개 원문을 읽을 수 있는 경우에만
Qoo10/메가割과 위조품 관련 표현의 근접 문맥을 다시 확인한다.
SNS 로그인·차단 페이지는 접근 실패만으로 탐지 결과를 버리지 않는다.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_SPACE_RE = re.compile(r"\s+")
_SOCIAL_HOSTS = {
    "x.com", "twitter.com", "instagram.com", "tiktok.com", "youtube.com",
    "youtu.be", "facebook.com", "threads.net",
}
_BLOCK_MARKERS = (
    "access denied", "forbidden", "captcha", "robot check", "verify you are human",
    "ログインしてください", "ログインが必要", "sign in to continue",
)


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_page_text(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().split(":", 1)[0].removeprefix("www.")


def _is_social(url: str) -> bool:
    host = _host(url)
    return any(host == domain or host.endswith("." + domain) for domain in _SOCIAL_HOSTS)


def choose_page_context(
    *, title: str, metadata: str, main_text: str, body_text: str, social: bool,
) -> tuple[str, bool]:
    """추천/배너가 섞인 전체 body보다 실제 게시물·본문 문맥을 우선한다.

    반환값의 bool은 메타데이터나 main/article에서 충분한 집중 문맥을
    확보했는지를 뜻한다.
    """
    title = normalize_page_text(title)
    metadata = normalize_page_text(metadata)
    main_text = normalize_page_text(main_text)
    body_text = normalize_page_text(body_text)
    focused = max((metadata, main_text), key=len)
    minimum = 30 if social else 120
    if len(focused) >= minimum:
        return normalize_page_text(f"{title} {focused}"), True
    return normalize_page_text(f"{title} {body_text}"), False


def _anchors(keyword: str) -> list[str]:
    text = str(keyword or "")
    if "メガ割" in text:
        return ["メガ割り", "メガ割"]
    return ["Qoo10"]


def proximity_evidence(
    text: str,
    keyword: str,
    fraud_words: list[str],
    *,
    window: int = 100,
) -> str:
    """공개 원문에서 기준어와 위조품 표현이 가까운 첫 문맥을 반환한다."""
    normalized = normalize_page_text(text)
    lower = normalized.lower()
    for anchor in _anchors(keyword):
        anchor_lower = anchor.lower()
        start = 0
        while True:
            index = lower.find(anchor_lower, start)
            if index < 0:
                break
            left = max(0, index - window)
            right = min(len(normalized), index + len(anchor) + window)
            nearby = normalized[left:right]
            if any(word.lower() in nearby.lower() for word in fraud_words):
                return ("… " if left else "") + nearby + (" …" if right < len(normalized) else "")
            start = index + 1
    return ""


@dataclass
class PageVerification:
    status: str
    reason: str
    evidence: str = ""
    verified_text: str = ""
    verified_at: str = ""


class SnsPageVerifier:
    """한 번 실행한 Chromium을 재사용해 후보 URL을 순차 검증한다."""

    def __init__(self) -> None:
        self.timeout_ms = max(
            5_000, int(os.getenv("SNS_PAGE_VERIFY_TIMEOUT_SECONDS", "25")) * 1_000
        )
        self.render_wait_ms = max(
            0, int(os.getenv("SNS_PAGE_VERIFY_RENDER_WAIT_MS", "1200"))
        )
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self.start_error = ""

    def start(self) -> bool:
        if self._browser is not None:
            return True
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                locale="ja-JP",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            return True
        except Exception as exc:
            self.start_error = f"{type(exc).__name__}: {exc}"
            self.close()
            return False

    def close(self) -> None:
        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._browser = None
        self._playwright = None

    def __enter__(self) -> "SnsPageVerifier":
        self.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def verify(
        self,
        *,
        url: str,
        keyword: str,
        fraud_words: list[str],
    ) -> PageVerification:
        verified_at = datetime.now().astimezone().isoformat(timespec="seconds")
        if not self.start():
            return PageVerification(
                "UNAVAILABLE",
                "Playwright 브라우저 시작 실패",
                verified_at=verified_at,
            )

        page = None
        try:
            page = self._context.new_page()
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
            if self.render_wait_ms:
                page.wait_for_timeout(self.render_wait_ms)

            status_code = response.status if response is not None else 0
            title = normalize_page_text(page.title())
            body = normalize_page_text(
                page.locator("body").inner_text(timeout=min(8_000, self.timeout_ms))
            )
            metadata_parts = page.locator(
                'meta[property="og:title"], meta[property="og:description"], '
                'meta[name="twitter:title"], meta[name="twitter:description"], '
                'meta[name="description"]'
            ).evaluate_all(
                "els => els.map(el => el.content || '').filter(Boolean)"
            )
            metadata = normalize_page_text(" ".join(metadata_parts))
            main_parts = page.locator("article, main, [role='main']").all_inner_texts()
            main_text = max(
                (normalize_page_text(value) for value in main_parts),
                key=len,
                default="",
            )
            combined, focused = choose_page_context(
                title=title,
                metadata=metadata,
                main_text=main_text,
                body_text=body,
                social=_is_social(url),
            )
            combined = combined[:20_000]

            evidence = proximity_evidence(combined, keyword, fraud_words)
            if evidence:
                return PageVerification(
                    "MATCH",
                    "공개 원문에서 기준어와 위조품 표현의 근접 문맥 확인",
                    evidence=evidence[:600],
                    verified_text=combined[:6_000],
                    verified_at=verified_at,
                )

            lower = combined.lower()
            blocked = status_code >= 400 or (
                len(combined) < 900 and any(marker in lower for marker in _BLOCK_MARKERS)
            )
            if blocked:
                return PageVerification(
                    "UNAVAILABLE",
                    f"페이지 접근 제한 또는 오류 응답({status_code or 'unknown'})",
                    verified_at=verified_at,
                )
            if len(combined) < 250:
                return PageVerification(
                    "UNAVAILABLE",
                    "판정 가능한 공개 원문 텍스트가 부족함",
                    verified_at=verified_at,
                )
            if _is_social(url) and focused:
                return PageVerification(
                    "MISMATCH",
                    "SNS 메타데이터/게시물 본문에서 기준어와 위조품 문맥을 확인하지 못함",
                    verified_text=combined[:6_000],
                    verified_at=verified_at,
                )
            if _is_social(url):
                return PageVerification(
                    "UNAVAILABLE",
                    "SNS 원문이 로그인·동적 화면으로 노출되지 않아 스니펫을 유지",
                    verified_at=verified_at,
                )
            return PageVerification(
                "MISMATCH",
                "공개 원문에서 기준어와 위조품 표현의 근접 문맥을 확인하지 못함",
                verified_text=combined[:6_000],
                verified_at=verified_at,
            )
        except Exception as exc:
            return PageVerification(
                "UNAVAILABLE",
                f"원문 확인 실패: {type(exc).__name__}",
                verified_at=verified_at,
            )
        finally:
            try:
                if page is not None:
                    page.close()
            except Exception:
                pass


def verify_rows(
    rows: list[dict[str, Any]],
    fraud_words: list[str],
    *,
    source: str,
    results_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """후보 행을 검증하고 확실한 일반 웹 문맥 불일치만 리포트에서 제외한다."""
    enabled = _env_flag("SNS_PAGE_VERIFY_ENABLED", True)
    max_rows = max(0, int(os.getenv("SNS_PAGE_VERIFY_MAX_ROWS", "30")))
    filter_mismatch = _env_flag("SNS_PAGE_VERIFY_FILTER_MISMATCH", True)
    if not enabled or not rows:
        for row in rows:
            row["page_verification"] = "SKIPPED"
            row["page_verification_reason"] = (
                "원문 검증 비활성" if not enabled else "검증 대상 없음"
            )
            row["page_verified_at"] = ""
        return rows

    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    with SnsPageVerifier() as verifier:
        for index, row in enumerate(rows):
            if index >= max_rows:
                result = PageVerification("SKIPPED", f"실행당 검증 상한 {max_rows}건 초과")
            else:
                result = verifier.verify(
                    url=str(row.get("url", "")),
                    keyword=str(row.get("keyword") or row.get("query") or ""),
                    fraud_words=fraud_words,
                )

            row["page_verification"] = result.status
            row["page_verification_reason"] = result.reason
            row["page_verified_at"] = result.verified_at
            if result.evidence:
                row["verified_evidence"] = result.evidence
            if result.verified_text:
                row["verified_text"] = result.verified_text

            print(
                f"  [VERIFY {index + 1}/{len(rows)}] "
                f"{result.status}: {str(row.get('url', ''))[:90]}"
            )
            if result.status == "MISMATCH" and filter_mismatch:
                rejected.append({
                    "source": source,
                    "date": row.get("date", ""),
                    "keyword": row.get("keyword") or row.get("query") or "",
                    "url": row.get("url", ""),
                    **asdict(result),
                })
            else:
                kept.append(row)

    if rejected and results_dir is not None:
        results_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        path = results_dir / f"page_verification_rejected_{source}_{stamp}.json"
        path.write_text(
            json.dumps(rejected, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  [VERIFY] 문맥 불일치 제외 {len(rejected)}건 → {path.name}")

    return kept
