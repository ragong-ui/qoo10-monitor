"""Qoo10 SNS 모니터링 담당자 목록의 단일 Python 소스."""

from __future__ import annotations

from collections.abc import Iterable


REVIEWER_OPTIONS = [
    "Rani Gong",
    "Jihyun Kwon",
    "Minjong Jang",
    "Donghee Kim",
    "Whajoon Ryu",
    "Woongsoo Shin",
    "Kim Meekyoung",
    "Kim Jinsun",
    "Choi Yunju",
    "Hyejin Jegal",
]


def reviewer_dropdown_options(existing: Iterable[object] = ()) -> list[str]:
    """공식 목록을 우선하고, 기존 레거시 값은 표시 손실 없이 뒤에 보존한다."""
    options = ["", *REVIEWER_OPTIONS]
    for value in existing:
        text = str(value or "").strip()
        if text and text not in options and text not in {"nan", "None"}:
            options.append(text)
    return options


def excel_validation_formula() -> str:
    return '"' + ",".join(REVIEWER_OPTIONS) + '"'
