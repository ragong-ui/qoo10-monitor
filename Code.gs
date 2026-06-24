// ============================================================
// Qoo10 SNS モニタリング — Google Apps Script Web App
// 컬럼 순서: A=검색일  B=키워드  C=URL  D=개요  E=Qoo10상품P
//            F=위험도  G=검색확인  H=오탐지여부  I=Status  J=아카이브
// ============================================================

const LIKELIHOOD_COL = 6;  // F열
const SEARCH_COL     = 7;  // G열 = 검색확인
const FALSPOS_COL    = 8;  // H열 = 오탐지여부
const STATUS_COL     = 9;  // I열 = Status
const ARCHIVE_COL    = 10; // J열 = 아카이브

// ── GET: 전체 데이터 반환 ──────────────────────────────────────
function doGet(e) {
  const sheetName = (e.parameter && e.parameter.sheet) || "";
  const ss    = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(sheetName);

  if (!sheet || sheet.getLastRow() <= 1) {
    return jsonOut({ data: [] });
  }

  const range    = sheet.getDataRange();
  const values   = range.getValues();
  const formulas = range.getFormulas();
  const headers  = values[0];
  const result   = [];

  for (let i = 1; i < values.length; i++) {
    const row = {};
    for (let j = 0; j < headers.length; j++) {
      let v = values[i][j];
      const formula = formulas[i][j];
      // HYPERLINK 수식에서 URL 추출 (Streamlit LinkColumn용)
      if (formula && formula.startsWith("=HYPERLINK(")) {
        const m = formula.match(/=HYPERLINK\("([^"]+)"/);
        v = m ? m[1] : "";
      }
      row[headers[j]] = (v === null || v === undefined) ? "" : String(v);
    }
    row["_row_index"] = i + 1; // 헤더=행1, 첫데이터=행2
    result.push(row);
  }

  return jsonOut({ data: result });
}

// ── POST 라우팅 ───────────────────────────────────────────────
function doPost(e) {
  const payload = JSON.parse(e.postData.contents);
  if (payload.action === "update") return handleUpdate(payload);
  return handleAppend(payload);
}

// ── 셀 업데이트 (오탐지여부 / Status) ─────────────────────────
function handleUpdate(payload) {
  const ss    = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(payload.sheet);
  if (!sheet) return jsonOut({ status: "error", message: "sheet not found" });

  const ri = Number(payload.row_index);
  if (payload.falspos !== undefined)
    sheet.getRange(ri, FALSPOS_COL).setValue(payload.falspos);
  if (payload.status !== undefined)
    sheet.getRange(ri, STATUS_COL).setValue(payload.status);

  return jsonOut({ status: "ok" });
}

// ── 행 추가 ──────────────────────────────────────────────────
function handleAppend(payload) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(payload.sheet);
  if (!sheet) sheet = ss.insertSheet(payload.sheet);

  const headers = payload.headers;
  const rows    = payload.rows;

  if (sheet.getLastRow() === 0) {
    sheet.appendRow(headers);
    applyHeaderStyle(sheet, headers.length);
    applyDropdowns(sheet);
    applyColumnWidths(sheet);
    sheet.setFrozenRows(1);
  }

  for (const row of rows) {
    sheet.appendRow(row);
    const lr = sheet.getLastRow();
    const likelihood = row[LIKELIHOOD_COL - 1]; // 0-indexed
    if (likelihood === "HIGH") {
      sheet.getRange(lr, 1, 1, headers.length).setBackground("#FFD2D2");
    } else if (likelihood === "MEDIUM") {
      sheet.getRange(lr, 1, 1, headers.length).setBackground("#FFF3CD");
    }
  }

  return jsonOut({ status: "ok", rows: rows.length });
}

// ── 헤더 스타일 ───────────────────────────────────────────────
function applyHeaderStyle(sheet, numCols) {
  sheet.getRange(1, 1, 1, numCols)
    .setBackground("#4A4A4A")
    .setFontColor("#FFFFFF")
    .setFontWeight("bold")
    .setWrap(false);
}

// ── 드롭다운 설정 (H=오탐지여부, I=Status) ────────────────────
function applyDropdowns(sheet) {
  const fpRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(["O", "X"], true)
    .setAllowInvalid(true)
    .build();
  sheet.getRange(2, FALSPOS_COL, 999, 1).setDataValidation(fpRule);

  const stRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(["New", "Reviewing", "Actioned", "Closed"], true)
    .setAllowInvalid(false)
    .build();
  sheet.getRange(2, STATUS_COL, 999, 1).setDataValidation(stRule);
}

// ── 열 너비 ──────────────────────────────────────────────────
function applyColumnWidths(sheet) {
  // A    B    C    D    E    F   G   H   I    J
  [90, 150, 250, 350, 120, 80, 90, 80, 100, 90]
    .forEach((w, i) => sheet.setColumnWidth(i + 1, w));
}

// ── JSON 응답 헬퍼 ────────────────────────────────────────────
function jsonOut(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
