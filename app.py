"""
Qoo10 SNS モニタリング — Web 대시보드
Streamlit Cloud 배포용 / 외부 벤더 공유 가능
"""

import os
import requests
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ── 설정 ─────────────────────────────────────────────────────
def _get_url() -> str:
    try:
        return st.secrets["GOOGLE_APPS_SCRIPT_URL"]
    except Exception:
        return os.getenv("GOOGLE_APPS_SCRIPT_URL", "")

APPS_SCRIPT_URL = _get_url()

SHEET_GOOGLE = "Google モニタリング"
SHEET_X      = "X モニタリング"

# Apps Script 헤더 → 표시 컬럼명
COL_GOOGLE = {
    "검색일 / 検索日":     "검색일",
    "검색 키워드":          "키워드",
    "URL":                  "URL",
    "개요 / 概要":          "개요",
    "Qoo10 상품 / 商品P":  "Qoo10 상품",
    "위험도 / 危険度":      "위험도",
    "검색확인 / 検索確認": "검색확인",   # G
    "오탐지여부":           "오탐지여부", # H
    "Status":               "Status",     # I
    "아카이브 / Archive":   "아카이브",
}
COL_X = {
    "검색일 / 検索日":        "검색일",
    "검색 쿼리 / クエリ":    "쿼리",
    "게시물 URL / 投稿URL":  "URL",
    "게시물 내용 / 投稿内容": "내용",
    "Qoo10 상품 URL":         "Qoo10 상품",
    "위험도 / 危険度":        "위험도",
    "검색확인 / 検索確認":   "검색확인",   # G
    "오탐지여부":             "오탐지여부", # H
    "Status":                 "Status",     # I
    "아카이브 / Archive":     "아카이브",
}


# ── 데이터 CRUD ──────────────────────────────────────────────
@st.cache_data(ttl=60)
def load_data(sheet_name: str) -> pd.DataFrame:
    if not APPS_SCRIPT_URL:
        return pd.DataFrame()
    try:
        resp = requests.get(
            APPS_SCRIPT_URL, params={"sheet": sheet_name}, timeout=30
        )
        rows = resp.json().get("data", [])
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception as e:
        st.error(f"데이터 로드 실패: {e}")
        return pd.DataFrame()


def save_changes(sheet_name: str, changes: list[dict]) -> bool:
    errors = []
    for c in changes:
        try:
            payload = {
                "action":    "update",
                "sheet":     sheet_name,
                "row_index": int(c["row_index"]),
                "falspos":   c["falspos"],
                "status":    c["status"],
            }
            resp = requests.post(APPS_SCRIPT_URL, json=payload, timeout=30)
            if resp.json().get("status") != "ok":
                errors.append(c)
        except Exception as e:
            errors.append({"error": str(e)})
    return len(errors) == 0


# ── 탭 렌더링 ────────────────────────────────────────────────
def render_tab(sheet_name: str, col_map: dict, kw_col: str):
    df_raw = load_data(sheet_name)

    if df_raw.empty:
        st.info("데이터가 없습니다. monitor.py 실행 후 새로고침 해주세요.")
        return

    df = df_raw.rename(columns=col_map)

    # ── 필터 ────────────────────────────────────────────────
    f1, f2, f3, f4 = st.columns([1, 1, 1, 2])
    with f1:
        lvl_f = st.selectbox("위험도", ["전체", "HIGH", "MEDIUM"],
                             key=f"lvl_{sheet_name}")
    with f2:
        fp_f = st.selectbox("오탐지여부",
                            ["전체", "O (오탐지)", "X (실검지)", "미확인"],
                            key=f"fp_{sheet_name}")
    with f3:
        st_f = st.selectbox("Status",
                            ["전체", "New", "Reviewing", "Actioned", "Closed"],
                            key=f"st_{sheet_name}")
    with f4:
        kw_f = st.text_input("검색", placeholder="키워드 / URL 검색...",
                             key=f"kw_{sheet_name}")

    # 필터 적용
    mask = pd.Series(True, index=df.index)
    if lvl_f != "전체":
        mask &= df["위험도"] == lvl_f
    if fp_f == "O (오탐지)":
        mask &= df["오탐지여부"].astype(str) == "O"
    elif fp_f == "X (실검지)":
        mask &= df["오탐지여부"].astype(str) == "X"
    elif fp_f == "미확인":
        mask &= df["오탐지여부"].astype(str).isin(["", "nan", "None"])
    if st_f != "전체":
        mask &= df["Status"].astype(str) == st_f
    if kw_f:
        kl = kw_f.lower()
        mask &= (
            df[kw_col].astype(str).str.lower().str.contains(kl, na=False)
            | df["URL"].astype(str).str.lower().str.contains(kl, na=False)
        )

    filtered = df[mask].copy().reset_index(drop=True)

    # ── 요약 배지 ────────────────────────────────────────────
    high_n = (filtered["위험도"] == "HIGH").sum()
    med_n  = (filtered["위험도"] == "MEDIUM").sum()
    st.markdown(
        f"**총 {len(filtered)}건** &nbsp;|&nbsp; "
        f":red[HIGH **{high_n}**건] &nbsp; :orange[MEDIUM **{med_n}**건]"
    )

    if filtered.empty:
        st.info("필터 조건에 해당하는 데이터가 없습니다.")
        return

    # ── 에디터 준비 ──────────────────────────────────────────
    row_indices = filtered["_row_index"].tolist()
    display_cols = ["검색일", kw_col, "URL", "개요", "Qoo10 상품",
                    "위험도", "검색확인", "오탐지여부", "Status", "아카이브"]

    editor_df = filtered[display_cols].copy()
    # 개요: 첫 줄(일본어)만 표시, 120자 제한
    editor_df["개요"] = editor_df["개요"].astype(str).apply(
        lambda x: (x.split("\n")[0])[:120]
    )
    # 빈 값 정규화
    editor_df["오탐지여부"] = editor_df["오탐지여부"].astype(str).replace(
        {"nan": "", "None": ""}
    )
    editor_df["Status"] = editor_df["Status"].astype(str).replace(
        {"nan": "New", "None": "New"}
    )

    orig_fp = editor_df["오탐지여부"].tolist()
    orig_st = editor_df["Status"].tolist()

    edited = st.data_editor(
        editor_df,
        column_config={
            "URL":        st.column_config.LinkColumn("URL",      display_text="링크 🔗"),
            "검색확인":   st.column_config.LinkColumn("검색확인", display_text="検索 🔍"),
            "아카이브":   st.column_config.LinkColumn("아카이브", display_text="Wayback 📦"),
            "개요":       st.column_config.TextColumn("개요",      width="large"),
            "Qoo10 상품": st.column_config.TextColumn("Qoo10 상품"),
            "위험도":     st.column_config.TextColumn("위험도"),
            "오탐지여부": st.column_config.SelectboxColumn(
                "오탐지여부",
                options=["", "O", "X"],
                help="O = 오탐지(오검지)  /  X = 실검지(위조품 확인)",
                width="small",
            ),
            "Status": st.column_config.SelectboxColumn(
                "Status",
                options=["New", "Reviewing", "Actioned", "Closed"],
                width="medium",
            ),
        },
        disabled=["검색일", kw_col, "URL", "개요", "Qoo10 상품",
                  "위험도", "검색확인", "아카이브"],
        hide_index=True,
        use_container_width=True,
        key=f"editor_{sheet_name}",
    )

    # ── 변경 감지 & 저장 ─────────────────────────────────────
    new_fp = edited["오탐지여부"].tolist()
    new_st = edited["Status"].tolist()

    changes = [
        {
            "row_index": row_indices[i],
            "falspos":   new_fp[i] if new_fp[i] not in ("nan", "None") else "",
            "status":    new_st[i],
        }
        for i in range(len(orig_fp))
        if orig_fp[i] != new_fp[i] or orig_st[i] != new_st[i]
    ]

    if changes:
        st.warning(f"⚠️ {len(changes)}건 변경됨")
        if st.button("💾 저장", type="primary", key=f"save_{sheet_name}"):
            with st.spinner("Google Sheets에 저장 중..."):
                ok = save_changes(sheet_name, changes)
            if ok:
                st.success("✅ 저장 완료!")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error("일부 항목 저장 실패. 다시 시도해주세요.")


# ── 메인 ─────────────────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="Qoo10 SNS モニタリング",
        page_icon="🔍",
        layout="wide",
    )

    # CSS: HIGH/MEDIUM 배지 색상
    st.markdown("""
    <style>
        .stMainBlockContainer { padding-top: 1.5rem; }
        [data-testid="stDataEditorRow"] { font-size: 0.85rem; }
    </style>
    """, unsafe_allow_html=True)

    # 헤더
    hc1, hc2 = st.columns([5, 1])
    with hc1:
        st.title("🔍 Qoo10 SNS モニタリング")
    with hc2:
        if st.button("🔄 새로고침", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    if not APPS_SCRIPT_URL:
        st.error("GOOGLE_APPS_SCRIPT_URL이 설정되지 않았습니다. Streamlit secrets를 확인해주세요.")
        return

    tab_g, tab_x = st.tabs(["🌐 Google モニタリング", "𝕏 X モニタリング"])

    with tab_g:
        render_tab(SHEET_GOOGLE, COL_GOOGLE, "키워드")

    with tab_x:
        render_tab(SHEET_X, COL_X, "쿼리")


if __name__ == "__main__":
    main()
