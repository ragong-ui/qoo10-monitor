"""
Qoo10 SNS モニタリング — Google/X 전용 Web 대시보드.

기존 Google/X 탐지 결과를 유지하면서 상품 단위 Case 관리, 담당자·메모,
변경 이력 및 선택형 AI 2차 분석 결과를 제공한다.
Japan Compliance Briefing은 별도 Apps Script 대시보드에서 운영한다.
"""

from __future__ import annotations

import hashlib
import html
import os
import re
from typing import Any

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import apps_script_client
from sns_enrichment import (
    build_case_id,
    extract_detection_evidence,
    extract_product_number,
)
from reviewers import REVIEWER_OPTIONS, reviewer_dropdown_options
from tls_utils import enable_system_trust_store

enable_system_trust_store()

load_dotenv()


def _get_url() -> str:
    try:
        return st.secrets["GOOGLE_APPS_SCRIPT_URL"]
    except Exception:
        return os.getenv("GOOGLE_APPS_SCRIPT_URL", "")


APPS_SCRIPT_URL = _get_url()
SHEET_GOOGLE = "Google モニタリング"
SHEET_X = "X モニタリング"
SHEET_HISTORY = "ReviewHistory"

FRAUD_WORDS = [
    "偽物", "ニセモノ", "にせもの", "パチモン", "パチもん", "パチモノ",
    "コピー", "fake", "偽造品", "模倣品", "コピー商品", "模造品", "詐欺",
    "販売禁止商品", "規約違反", "強制返金", "中国",
]
AI_LABELS = [
    "PENDING",
    "PURCHASE_COUNTERFEIT",
    "GENERAL_WARNING",
    "AD_OR_AFFILIATE",
    "UNRELATED",
    "INSUFFICIENT_CONTENT",
    "ERROR",
]
STATUS_OPTIONS = ["New", "Reviewing", "Actioned", "Closed"]

COMMON_EXTRA_MAP = {
    "상품번호 / 商品番号": "상품번호",
    "Case ID": "Case ID",
    "탐지 근거 / 検知根拠": "탐지 근거",
    "AI 판정 / AI判定": "AI 판정",
    "AI 신뢰도 / AI信頼度": "AI 신뢰도",
    "AI 판정 이유 / AI判定理由": "AI 판정 이유",
    "AI 근거 / AI根拠": "AI 근거",
    "담당자 / 担当者": "담당자",
    "조치 메모 / 対応メモ": "조치 메모",
    "최종 변경일 / 最終更新": "최종 변경일",
    "AI 모델 / AI Model": "AI 모델",
}
COL_GOOGLE = {
    "검색일 / 検索日": "검색일",
    "검색 키워드": "키워드",
    "URL": "URL",
    "개요 / 概要": "개요",
    "Qoo10 상품 / 商品P": "Qoo10 상품",
    "위험도 / 危険度": "위험도",
    "검색확인 / 検索確認": "검색확인",
    "오탐지여부": "오탐지여부",
    "Status": "Status",
    **COMMON_EXTRA_MAP,
}
COL_X = {
    "검색일 / 検索日": "검색일",
    "검색 쿼리 / クエリ": "쿼리",
    "게시물 URL / 投稿URL": "URL",
    "게시물 내용 / 投稿内容": "개요",
    "Qoo10 상품 URL": "Qoo10 상품",
    "위험도 / 危険度": "위험도",
    "검색확인 / 検索確認": "검색확인",
    "오탐지여부": "오탐지여부",
    "Status": "Status",
    **COMMON_EXTRA_MAP,
}


def _clean(value: Any, default: str = "") -> str:
    if value is None or pd.isna(value):
        return default
    text = str(value)
    return default if text in {"nan", "None", "NaT"} else text


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_data(sheet_name: str) -> pd.DataFrame:
    if not APPS_SCRIPT_URL:
        return pd.DataFrame()
    get_json = getattr(apps_script_client, "get_json_with_retry", None)
    if get_json is None:
        raise RuntimeError(
            "Streamlit 배포 파일 버전이 일치하지 않습니다. 앱을 다시 시작해 주세요."
        )
    body = get_json(
        APPS_SCRIPT_URL,
        {"sheet": sheet_name},
        timeout=45,
        max_attempts=5,
    )
    if body.get("status") == "error":
        raise RuntimeError(body.get("message", "Apps Script error"))
    rows = body.get("data", [])
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _snapshot_key(sheet_name: str) -> str:
    return f"_qoo10_remote_snapshot::{sheet_name}"


def _data_revision() -> int:
    return int(st.session_state.get("_qoo10_data_revision", 0))


def invalidate_data_cache() -> None:
    """저장 성공 또는 수동 새로고침 때만 원격 데이터 임시본을 폐기한다."""
    for sheet_name in (SHEET_GOOGLE, SHEET_X, SHEET_HISTORY):
        st.session_state.pop(_snapshot_key(sheet_name), None)
    st.session_state["_qoo10_data_revision"] = _data_revision() + 1
    st.cache_data.clear()


def load_data(sheet_name: str) -> pd.DataFrame:
    """편집 중에는 세션 임시본을 반환해 Google Sheets를 다시 읽지 않는다."""
    key = _snapshot_key(sheet_name)
    if key not in st.session_state:
        try:
            st.session_state[key] = _fetch_data(sheet_name)
        except Exception as exc:
            st.error(f"데이터 로드 실패 ({sheet_name}): {exc}")
            return pd.DataFrame()
    snapshot = st.session_state[key]
    if isinstance(snapshot, pd.DataFrame):
        return snapshot.copy()
    st.session_state.pop(key, None)
    return pd.DataFrame()

def prepare_data(
    df_raw: pd.DataFrame,
    sheet_name: str,
    col_map: dict[str, str],
    kw_col: str,
    platform: str,
) -> tuple[pd.DataFrame, list[str]]:
    if df_raw.empty:
        return pd.DataFrame(), []

    df = df_raw.rename(columns=col_map).copy()
    required = [
        "_row_index", "검색일", kw_col, "URL", "개요", "Qoo10 상품",
        "위험도", "검색확인", "오탐지여부", "Status",
    ]
    missing = [column for column in required if column not in df.columns]
    if missing:
        return pd.DataFrame(), missing

    defaults = {
        "상품번호": "",
        "Case ID": "",
        "탐지 근거": "",
        "AI 판정": "PENDING",
        "AI 신뢰도": "",
        "AI 판정 이유": "",
        "AI 근거": "",
        "담당자": "",
        "조치 메모": "",
        "최종 변경일": "",
        "AI 모델": "",
    }
    for column, default in defaults.items():
        if column not in df.columns:
            df[column] = default

    string_columns = [
        "검색일", kw_col, "URL", "개요", "Qoo10 상품", "위험도",
        "검색확인", "오탐지여부", "Status", *defaults.keys(),
    ]
    for column in string_columns:
        df[column] = df[column].map(lambda value, d=defaults.get(column, ""): _clean(value, d))

    df["Status"] = df["Status"].replace("", "New")
    df["AI 판정"] = df["AI 판정"].replace("", "PENDING")
    df["상품번호"] = [
        product or extract_product_number(link)
        for product, link in zip(df["상품번호"], df["Qoo10 상품"])
    ]
    df["Case ID"] = [
        case_id or build_case_id(qoo10_link, source_url)[0]
        for case_id, qoo10_link, source_url in zip(
            df["Case ID"], df["Qoo10 상품"], df["URL"]
        )
    ]
    df["탐지 근거"] = [
        evidence or extract_detection_evidence(summary, FRAUD_WORDS)
        for evidence, summary in zip(df["탐지 근거"], df["개요"])
    ]
    df["_sheet"] = sheet_name
    df["_platform"] = platform
    df["_kw_col"] = kw_col
    df["_date"] = pd.to_datetime(df["검색일"], errors="coerce")
    return df, []


def load_prepared(
    sheet_name: str,
    col_map: dict[str, str],
    kw_col: str,
    platform: str,
) -> tuple[pd.DataFrame, list[str]]:
    return prepare_data(load_data(sheet_name), sheet_name, col_map, kw_col, platform)


def save_changes(changes: list[dict[str, Any]]) -> tuple[bool, list[dict[str, Any]]]:
    """Apps Script batch_update를 400행 단위로 호출한다."""
    if not changes:
        return True, []

    errors: list[dict[str, Any]] = []
    for start in range(0, len(changes), 400):
        chunk = changes[start:start + 400]
        try:
            body = apps_script_client.post_json_with_retry(
                APPS_SCRIPT_URL,
                {"action": "batch_update", "changes": chunk},
                timeout=90,
                max_attempts=5,
            )
            if body.get("status") not in {"ok", "partial"}:
                errors.append({"message": body.get("message", "batch update failed")})
            for error in body.get("errors", []):
                errors.append({**error, "start": start})
        except Exception as exc:
            errors.append({"message": str(exc), "start": start})
    return not errors, errors


def _filter_rows(df: pd.DataFrame, kw_col: str, key: str) -> pd.DataFrame:
    f1, f2, f3, f4, f5 = st.columns([1, 1.2, 1.2, 1.4, 2])
    with f1:
        risk = st.selectbox("위험도", ["전체", "HIGH", "MEDIUM"], key=f"risk_{key}")
    with f2:
        false_positive = st.selectbox(
            "오탐지여부",
            ["전체", "O (오탐지)", "X (실검지)", "미확인"],
            key=f"fp_{key}",
        )
    with f3:
        status = st.selectbox("Status", ["전체", *STATUS_OPTIONS], key=f"status_{key}")
    with f4:
        ai_label = st.selectbox("AI 판정", ["전체", *AI_LABELS], key=f"ai_{key}")
    with f5:
        text = st.text_input(
            "검색",
            placeholder="키워드 / URL / 상품번호 / Case ID 검색...",
            key=f"text_{key}",
        )

    with st.expander("기간·상품 필터", expanded=False):
        e1, e2, e3 = st.columns([2, 2, 1])
        valid_dates = df["_date"].dropna()
        with e1:
            if valid_dates.empty:
                date_range = ()
                st.caption("날짜 데이터 없음")
            else:
                date_range = st.date_input(
                    "탐지 기간",
                    value=(valid_dates.min().date(), valid_dates.max().date()),
                    min_value=valid_dates.min().date(),
                    max_value=valid_dates.max().date(),
                    key=f"date_{key}",
                )
        with e2:
            product = st.text_input(
                "상품번호",
                placeholder="예: 1097671463",
                key=f"product_{key}",
            )
        with e3:
            hide_closed = st.checkbox("Closed 숨김", value=False, key=f"closed_{key}")

    mask = pd.Series(True, index=df.index)
    if risk != "전체":
        mask &= df["위험도"] == risk
    if false_positive == "O (오탐지)":
        mask &= df["오탐지여부"] == "O"
    elif false_positive == "X (실검지)":
        mask &= df["오탐지여부"] == "X"
    elif false_positive == "미확인":
        mask &= df["오탐지여부"] == ""
    if status != "전체":
        mask &= df["Status"] == status
    if ai_label != "전체":
        mask &= df["AI 판정"] == ai_label
    if hide_closed:
        mask &= df["Status"] != "Closed"
    if product:
        mask &= df["상품번호"].str.contains(product.strip(), case=False, na=False)
    if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
        start_date, end_date = date_range
        mask &= df["_date"].dt.date.between(start_date, end_date)
    if text:
        needle = text.lower()
        searchable = (
            df[kw_col] + " " + df["URL"] + " " + df["개요"] + " " +
            df["상품번호"] + " " + df["Case ID"]
        ).str.lower()
        mask &= searchable.str.contains(re.escape(needle), na=False)
    return df[mask].copy().reset_index(drop=True)


def _build_row_changes(
    filtered: pd.DataFrame,
    edited: pd.DataFrame,
    original: pd.DataFrame,
    reviewer_default: str,
) -> tuple[list[dict[str, Any]], list[int]]:
    changes: list[dict[str, Any]] = []
    missing_reviewer: list[int] = []
    fields = {
        "오탐지여부": "falspos",
        "Status": "status",
        "담당자": "reviewer",
        "조치 메모": "action_note",
    }

    for i in range(len(edited)):
        delta: dict[str, Any] = {
            "sheet": filtered.loc[i, "_sheet"],
            "row_index": int(filtered.loc[i, "_row_index"]),
        }
        changed = False
        for column, api_field in fields.items():
            old_value = _clean(original.loc[i, column])
            new_value = _clean(edited.loc[i, column])
            if old_value != new_value:
                delta[api_field] = new_value
                changed = True
        if not changed:
            continue

        reviewer = _clean(edited.loc[i, "담당자"]) or reviewer_default.strip()
        if not reviewer:
            missing_reviewer.append(i + 1)
            continue
        delta["reviewer"] = reviewer
        if "action_note" not in delta:
            delta["action_note"] = _clean(edited.loc[i, "조치 메모"])
        changes.append(delta)
    return changes, missing_reviewer


def render_source_tab(
    sheet_name: str,
    col_map: dict[str, str],
    kw_col: str,
    platform: str,
) -> None:
    df, missing = load_prepared(sheet_name, col_map, kw_col, platform)
    if missing:
        st.error("Google Sheets 컬럼 불일치: " + ", ".join(missing))
        return
    if df.empty:
        st.info("데이터가 없습니다. 모니터링 실행 후 새로고침 해주세요.")
        return

    filtered = _filter_rows(df, kw_col, sheet_name)
    high_count = int((filtered["위험도"] == "HIGH").sum())
    medium_count = int((filtered["위험도"] == "MEDIUM").sum())
    st.markdown(
        f"**총 {len(filtered)}건** &nbsp;|&nbsp; "
        f":red[HIGH **{high_count}**건] &nbsp; :orange[MEDIUM **{medium_count}**건]"
    )
    if filtered.empty:
        st.info("필터 조건에 해당하는 데이터가 없습니다.")
        return

    reviewer_default = st.selectbox(
        "현재 담당자",
        options=["", *REVIEWER_OPTIONS],
        key=f"reviewer_{sheet_name}",
        help="변경 이력에 기록할 담당자를 선택하세요.",
    )
    st.caption("✏️ 편집 내용은 브라우저 임시 상태이며, ‘변경사항 저장’을 눌러야 Google Sheets에 반영됩니다.")
    display_columns = [
        "검색일", kw_col, "URL", "개요", "Qoo10 상품", "상품번호", "Case ID",
        "위험도", "탐지 근거", "AI 판정", "검색확인",
        "오탐지여부", "Status", "담당자", "조치 메모",
    ]
    editor_df = filtered[display_columns].copy()
    editor_df.insert(0, "선택", False)
    editor_df["개요"] = editor_df["개요"].map(lambda text: _clean(text).split("\n")[0][:120])
    editor_df["탐지 근거"] = editor_df["탐지 근거"].map(lambda text: _clean(text)[:180])
    original = editor_df.copy()
    editor_signature = hashlib.sha1(
        "|".join(str(value) for value in filtered["_row_index"]).encode("utf-8")
    ).hexdigest()[:12]

    edited = st.data_editor(
        editor_df,
        column_config={
            "선택": st.column_config.CheckboxColumn("선택", width="small"),
            "URL": st.column_config.LinkColumn("SNS URL", display_text="링크 🔗"),
            "Qoo10 상품": st.column_config.LinkColumn("Qoo10 상품", display_text="상품 🔗"),
            "검색확인": st.column_config.LinkColumn("검색확인", display_text="검색 🔍"),
            "개요": st.column_config.TextColumn("개요", width="large"),
            "탐지 근거": st.column_config.TextColumn("탐지 근거", width="large"),
            "오탐지여부": st.column_config.SelectboxColumn(
                "오탐지여부",
                options=["", "O", "X"],
                help="O = 오탐지 / X = 실검지",
                width="small",
            ),
            "Status": st.column_config.SelectboxColumn(
                "Status", options=STATUS_OPTIONS, width="medium"
            ),
            "담당자": st.column_config.SelectboxColumn(
                "담당자",
                options=reviewer_dropdown_options(filtered["담당자"]),
                width="medium",
            ),
            "조치 메모": st.column_config.TextColumn("조치 메모", width="large"),
        },
        disabled=[
            "검색일", kw_col, "URL", "개요", "Qoo10 상품", "상품번호",
            "Case ID", "위험도", "탐지 근거", "AI 판정", "검색확인",
        ],
        hide_index=True,
        use_container_width=True,
        key=f"editor_{sheet_name}_{editor_signature}_{_data_revision()}",
    )

    changes, missing_reviewer = _build_row_changes(
        filtered, edited, original, reviewer_default
    )
    if missing_reviewer:
        st.error("변경 저장 전 ‘현재 담당자’ 또는 행의 담당자를 입력해 주세요.")
    if changes:
        st.warning(f"⚠️ {len(changes)}개 행에 변경사항이 있습니다.")
        if st.button(
            "💾 변경사항 저장",
            type="primary",
            disabled=bool(missing_reviewer),
            key=f"save_{sheet_name}",
        ):
            with st.spinner("Google Sheets와 변경 이력에 저장 중..."):
                ok, errors = save_changes(changes)
            if ok:
                st.success("저장 완료")
                invalidate_data_cache()
                st.rerun()
            else:
                st.error(f"일부 저장 실패: {errors[:3]}")

    st.divider()
    checked = edited[edited["선택"] == True]
    if checked.empty:
        st.caption("💡 체크박스를 선택하면 개요와 탐지 근거 전체를 복사할 수 있습니다.")
    else:
        for row_index in checked.index:
            source = filtered.loc[row_index]
            st.text_area(
                f"📋 [{source['검색일']}] {source[kw_col]} — 개요 전체",
                value=source["개요"],
                height=150,
                key=f"full_{sheet_name}_{source['_row_index']}",
            )
            st.text_area(
                "탐지 근거",
                value=source["탐지 근거"],
                height=90,
                key=f"evidence_{sheet_name}_{source['_row_index']}",
            )


def _first_nonempty(series: pd.Series, default: str = "") -> str:
    values = [_clean(value) for value in series]
    values = [value for value in values if value]
    return values[-1] if values else default


def _combined_value(series: pd.Series, default: str = "") -> str:
    values = sorted({_clean(value) for value in series if _clean(value)})
    if not values:
        return default
    return values[0] if len(values) == 1 else "Mixed"


def build_case_table(evidence: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    if evidence.empty:
        return pd.DataFrame(), {}

    groups: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []
    risk_score = {"HIGH": 2, "MEDIUM": 1}
    ai_score = {
        "PURCHASE_COUNTERFEIT": 6,
        "GENERAL_WARNING": 5,
        "AD_OR_AFFILIATE": 4,
        "UNRELATED": 3,
        "INSUFFICIENT_CONTENT": 2,
        "ERROR": 1,
        "PENDING": 0,
    }

    for case_id, group in evidence.groupby("Case ID", sort=False):
        group = group.copy().sort_values(["_date", "_row_index"], na_position="first")
        groups[case_id] = group
        risks = [_clean(value) for value in group["위험도"]]
        top_risk = max(risks, key=lambda value: risk_score.get(value, 0))
        labels = [_clean(value, "PENDING") for value in group["AI 판정"]]
        top_ai = max(labels, key=lambda value: ai_score.get(value, -1))
        valid_dates = group["_date"].dropna()
        latest_date = (
            valid_dates.max().strftime("%Y-%m-%d")
            if not valid_dates.empty
            else _first_nonempty(group["검색일"])
        )
        rows.append({
            "선택": False,
            "Case ID": case_id,
            "상품번호": _first_nonempty(group["상품번호"]),
            "플랫폼": " / ".join(sorted(set(group["_platform"]))),
            "탐지건수": len(group),
            "최신 탐지일": latest_date,
            "최고 위험도": top_risk,
            "AI 판정": top_ai,
            "오탐지여부": _combined_value(group["오탐지여부"]),
            "Status": _combined_value(group["Status"], "New"),
            "담당자": _first_nonempty(group["담당자"]),
            "조치 메모": _first_nonempty(group["조치 메모"]),
        })
    return pd.DataFrame(rows), groups


def _filter_cases(cases: pd.DataFrame) -> pd.DataFrame:
    c1, c2, c3, c4, c5 = st.columns([1.1, 1, 1.2, 1.4, 2])
    with c1:
        platform = st.selectbox("플랫폼", ["전체", "Google", "X"], key="case_platform")
    with c2:
        risk = st.selectbox("위험도", ["전체", "HIGH", "MEDIUM"], key="case_risk")
    with c3:
        false_positive = st.selectbox(
            "오탐지여부", ["전체", "O", "X", "미확인", "Mixed"], key="case_fp"
        )
    with c4:
        status = st.selectbox(
            "Status", ["전체", *STATUS_OPTIONS, "Mixed"], key="case_status"
        )
    with c5:
        text = st.text_input(
            "Case 검색", placeholder="상품번호 / Case ID / 담당자 검색...", key="case_text"
        )
    a1, a2, a3 = st.columns([1.5, 1.5, 1])
    with a1:
        ai_label = st.selectbox("AI 판정", ["전체", *AI_LABELS], key="case_ai")
    with a2:
        product = st.text_input("상품번호", key="case_product")
    with a3:
        hide_closed = st.checkbox("Closed 숨김", value=True, key="case_hide_closed")

    mask = pd.Series(True, index=cases.index)
    if platform != "전체":
        mask &= cases["플랫폼"].str.contains(platform, regex=False)
    if risk != "전체":
        mask &= cases["최고 위험도"] == risk
    if false_positive == "미확인":
        mask &= cases["오탐지여부"] == ""
    elif false_positive != "전체":
        mask &= cases["오탐지여부"] == false_positive
    if status != "전체":
        mask &= cases["Status"] == status
    if ai_label != "전체":
        mask &= cases["AI 판정"] == ai_label
    if hide_closed:
        mask &= cases["Status"] != "Closed"
    if product:
        mask &= cases["상품번호"].str.contains(product.strip(), case=False, na=False)
    if text:
        needle = re.escape(text.lower())
        searchable = (
            cases["Case ID"] + " " + cases["상품번호"] + " " + cases["담당자"]
        ).str.lower()
        mask &= searchable.str.contains(needle, na=False)
    return cases[mask].copy().reset_index(drop=True)


def _case_changes(
    page: pd.DataFrame,
    edited: pd.DataFrame,
    original: pd.DataFrame,
    groups: dict[str, pd.DataFrame],
    reviewer_default: str,
) -> tuple[list[dict[str, Any]], bool]:
    changes: list[dict[str, Any]] = []
    reviewer_missing = False
    editable = {
        "오탐지여부": "falspos",
        "Status": "status",
        "담당자": "reviewer",
        "조치 메모": "action_note",
    }
    for i in range(len(edited)):
        changed_fields: dict[str, str] = {}
        for column, api_name in editable.items():
            old_value = _clean(original.loc[i, column])
            new_value = _clean(edited.loc[i, column])
            if old_value != new_value and new_value != "Mixed":
                changed_fields[api_name] = new_value
        if not changed_fields:
            continue

        case_id = page.loc[i, "Case ID"]
        reviewer = _clean(edited.loc[i, "담당자"]) or reviewer_default.strip()
        if not reviewer:
            reviewer_missing = True
            continue
        changed_fields["reviewer"] = reviewer
        if "action_note" not in changed_fields:
            changed_fields["action_note"] = _clean(edited.loc[i, "조치 메모"])

        for _, member in groups[case_id].iterrows():
            changes.append({
                "sheet": member["_sheet"],
                "row_index": int(member["_row_index"]),
                **changed_fields,
            })
    return changes, reviewer_missing


def _highlight_evidence(text: str) -> str:
    escaped = html.escape(_clean(text))
    pattern = re.compile(
        "(" + "|".join(re.escape(word) for word in sorted(FRAUD_WORDS, key=len, reverse=True)) + ")",
        flags=re.IGNORECASE,
    )
    return pattern.sub(r"<mark>\1</mark>", escaped).replace("\n", "<br>")


def render_case_tab() -> None:
    google, missing_g = load_prepared(
        SHEET_GOOGLE, COL_GOOGLE, "키워드", "Google"
    )
    x_data, missing_x = load_prepared(SHEET_X, COL_X, "쿼리", "X")
    missing = missing_g + missing_x
    if missing:
        st.error("Google Sheets 컬럼 불일치: " + ", ".join(sorted(set(missing))))
        return
    evidence = pd.concat([google, x_data], ignore_index=True)
    if evidence.empty:
        st.info("Case로 묶을 데이터가 없습니다.")
        return

    cases, groups = build_case_table(evidence)
    filtered = _filter_cases(cases)
    st.markdown(
        f"**상품/게시물 Case {len(filtered)}건** &nbsp;|&nbsp; "
        f"근거 게시물 **{int(filtered['탐지건수'].sum()) if not filtered.empty else 0}건**"
    )
    if filtered.empty:
        st.info("필터 조건에 해당하는 Case가 없습니다.")
        return

    p1, p2, p3 = st.columns([1.5, 1.5, 3])
    with p1:
        page_size = st.selectbox("페이지당 Case", [25, 50, 100], index=0)
    total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
    with p2:
        page_number = st.number_input(
            "페이지", min_value=1, max_value=total_pages, value=1, step=1
        )
    with p3:
        reviewer_default = st.selectbox(
            "현재 담당자",
            options=["", *REVIEWER_OPTIONS],
            key="case_reviewer",
            help="Case 변경 이력에 기록할 담당자를 선택하세요.",
        )
        st.caption("✏️ Case 편집 내용은 ‘Case 변경사항 저장’을 눌러야 Google Sheets에 반영됩니다.")

    start = (int(page_number) - 1) * page_size
    page = filtered.iloc[start:start + page_size].copy().reset_index(drop=True)
    original = page.copy()
    editor_signature = hashlib.sha1(
        "|".join(page["Case ID"]).encode("utf-8")
    ).hexdigest()[:12]
    edited = st.data_editor(
        page,
        column_config={
            "선택": st.column_config.CheckboxColumn("선택", width="small"),
            "Case ID": st.column_config.TextColumn("Case ID", width="medium"),
            "상품번호": st.column_config.TextColumn("상품번호", width="medium"),
            "탐지건수": st.column_config.NumberColumn("탐지건수", width="small"),
            "오탐지여부": st.column_config.SelectboxColumn(
                "오탐지여부", options=["", "O", "X", "Mixed"], width="small"
            ),
            "Status": st.column_config.SelectboxColumn(
                "Status", options=[*STATUS_OPTIONS, "Mixed"], width="medium"
            ),
            "담당자": st.column_config.SelectboxColumn(
                "담당자",
                options=reviewer_dropdown_options(page["담당자"]),
                width="medium",
            ),
            "조치 메모": st.column_config.TextColumn("조치 메모", width="large"),
        },
        disabled=[
            "Case ID", "상품번호", "플랫폼", "탐지건수", "최신 탐지일",
            "최고 위험도", "AI 판정",
        ],
        hide_index=True,
        use_container_width=True,
        key=f"case_editor_{page_number}_{page_size}_{editor_signature}_{_data_revision()}",
    )

    changes, reviewer_missing = _case_changes(
        page, edited, original, groups, reviewer_default
    )
    if reviewer_missing:
        st.error("Case 변경 저장 전 ‘현재 담당자’ 또는 Case 담당자를 입력해 주세요.")
    if changes:
        st.warning(f"⚠️ 근거 행 {len(changes)}건에 반영할 Case 변경사항이 있습니다.")
        if st.button(
            "💾 Case 변경사항 저장",
            type="primary",
            disabled=reviewer_missing,
            key="save_cases",
        ):
            with st.spinner("Case의 모든 근거 행과 변경 이력에 저장 중..."):
                ok, errors = save_changes(changes)
            if ok:
                st.success("Case 저장 완료")
                invalidate_data_cache()
                st.rerun()
            else:
                st.error(f"일부 저장 실패: {errors[:3]}")

    selected = edited[edited["선택"] == True]["Case ID"].tolist()
    st.divider()
    if not selected:
        st.caption("💡 Case를 선택하면 Google/X 탐지 근거를 한 곳에서 확인할 수 있습니다.")
        return

    for case_id in selected:
        members = groups[case_id]
        product = _first_nonempty(members["상품번호"], "상품번호 없음")
        with st.expander(
            f"📦 {product} · {case_id} · 근거 {len(members)}건",
            expanded=True,
        ):
            for _, row in members.iterrows():
                kw_col = row["_kw_col"]
                st.markdown(
                    f"**{row['_platform']} · {row['검색일']} · {row[kw_col]} · "
                    f"{row['위험도']} · {row['AI 판정']}**"
                )
                links = []
                if row["URL"]:
                    links.append(f"[SNS/외부 페이지]({row['URL']})")
                if row["검색확인"]:
                    links.append(f"[검색결과]({row['검색확인']})")
                if row["Qoo10 상품"]:
                    links.append(f"[Qoo10 상품]({row['Qoo10 상품']})")
                if links:
                    st.markdown(" · ".join(links))
                st.markdown(
                    '<div class="evidence-box"><strong>탐지 근거:</strong><br>' +
                    _highlight_evidence(row["탐지 근거"]) +
                    "</div>",
                    unsafe_allow_html=True,
                )
                if row["AI 판정 이유"] or row["AI 근거"]:
                    st.caption(
                        f"AI 이유: {row['AI 판정 이유']} | AI 근거: {row['AI 근거']}"
                    )
                st.text_area(
                    "개요 전체 (Ctrl+C)",
                    value=row["개요"],
                    height=120,
                    key=f"case_full_{row['_sheet']}_{row['_row_index']}",
                )
                st.markdown("---")


def render_history_tab() -> None:
    history = load_data(SHEET_HISTORY)
    if history.empty:
        st.info("아직 저장된 변경 이력이 없습니다.")
        return
    history = history.rename(columns={
        "변경일시 / 変更日時": "변경일시",
        "시트 / シート": "시트",
        "행번호 / 行番号": "행번호",
        "상품번호 / 商品番号": "상품번호",
        "변경항목 / 変更項目": "변경항목",
        "변경전 / 変更前": "변경전",
        "변경후 / 変更後": "변경후",
        "담당자 / 担当者": "담당자",
        "조치 메모 / 対応メモ": "조치 메모",
        "근거 URL / 証拠URL": "근거 URL",
    })
    h1, h2, h3 = st.columns([1.5, 1.5, 2])
    with h1:
        history_reviewers = reviewer_dropdown_options(
            history.get("담당자", pd.Series(dtype=str))
        )
        reviewer = st.selectbox(
            "담당자", ["전체", *[name for name in history_reviewers if name]], key="history_reviewer"
        )
    with h2:
        field = st.selectbox(
            "변경항목",
            ["전체", *sorted(history.get("변경항목", pd.Series(dtype=str)).unique())],
            key="history_field",
        )
    with h3:
        text = st.text_input(
            "이력 검색", placeholder="상품번호 / Case ID / 메모 검색...", key="history_text"
        )
    mask = pd.Series(True, index=history.index)
    if reviewer != "전체" and "담당자" in history:
        mask &= history["담당자"].astype(str).str.contains(
            re.escape(reviewer), case=False, na=False
        )
    if field != "전체":
        mask &= history["변경항목"] == field
    if text:
        searchable = (
            history.get("상품번호", "").astype(str) + " " +
            history.get("Case ID", "").astype(str) + " " +
            history.get("조치 메모", "").astype(str)
        )
        mask &= searchable.str.contains(re.escape(text), case=False, na=False)
    filtered = history[mask].copy()
    if "변경일시" in filtered:
        filtered = filtered.sort_values("변경일시", ascending=False)
    st.dataframe(
        filtered,
        column_config={
            "근거 URL": st.column_config.LinkColumn("근거 URL", display_text="링크 🔗")
        },
        hide_index=True,
        use_container_width=True,
    )


def main() -> None:
    st.set_page_config(
        page_title="Qoo10 SNS モニタリング",
        page_icon="🔍",
        layout="wide",
    )
    st.markdown(
        """
        <style>
          .stMainBlockContainer { padding-top: 1.5rem; }
          [data-testid="stDataEditorRow"] { font-size: 0.85rem; }
          .evidence-box {
            padding: 0.75rem 0.9rem;
            border-left: 4px solid #ff4b4b;
            background: rgba(255, 75, 75, 0.08);
            border-radius: 0.25rem;
            line-height: 1.6;
            user-select: text;
          }
          mark { background: #ffe08a; color: #111; padding: 0 0.12rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    header, refresh = st.columns([5, 1])
    with header:
        st.title("🔍 Qoo10 SNS モニタリング")
        st.caption("Google/X 탐지 → 상품 Case 검토 → 조치 → 변경 이력")
    with refresh:
        if st.button("🔄 새로고침", use_container_width=True):
            invalidate_data_cache()
            st.rerun()

    if not APPS_SCRIPT_URL:
        st.error("GOOGLE_APPS_SCRIPT_URL이 설정되지 않았습니다.")
        return

    view = st.radio(
        "화면 선택",
        [
            "🌐 Google モニタリング",
            "𝕏 X モニタリング",
            "📦 상품 Case",
            "🕘 변경 이력",
        ],
        horizontal=True,
        label_visibility="collapsed",
        key="main_view",
    )
    st.caption("선택한 화면의 데이터만 불러오며, 편집 중에는 세션 임시본을 사용합니다.")

    if view == "🌐 Google モニタリング":
        render_source_tab(SHEET_GOOGLE, COL_GOOGLE, "키워드", "Google")
    elif view == "𝕏 X モニタリング":
        render_source_tab(SHEET_X, COL_X, "쿼리", "X")
    elif view == "📦 상품 Case":
        render_case_tab()
    else:
        render_history_tab()


if __name__ == "__main__":
    main()
