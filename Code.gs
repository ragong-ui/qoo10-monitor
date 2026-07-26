// ============================================================
// Qoo10 SNS モニタリング — Google Apps Script Web App
// 기존 A~I 유지, J 이후에 Case·AI·담당자 필드를 추가한다.
// ============================================================

const GOOGLE_SHEET = "Google モニタリング";
const X_SHEET = "X モニタリング";
const HISTORY_SHEET = "ReviewHistory";
const DATA_SHEETS = [GOOGLE_SHEET, X_SHEET];

const EXTRA_HEADERS = [
  "상품번호 / 商品番号",       // J
  "Case ID",                    // K
  "탐지 근거 / 検知根拠",      // L
  "AI 판정 / AI判定",           // M
  "AI 신뢰도 / AI信頼度",      // N
  "AI 판정 이유 / AI判定理由",  // O
  "AI 근거 / AI根拠",           // P
  "담당자 / 担当者",            // Q
  "조치 메모 / 対応メモ",      // R
  "최종 변경일 / 最終更新",     // S
  "AI 모델 / AI Model",         // T
];

const HISTORY_HEADERS = [
  "변경일시 / 変更日時",
  "시트 / シート",
  "행번호 / 行番号",
  "Case ID",
  "상품번호 / 商品番号",
  "변경항목 / 変更項目",
  "변경전 / 変更前",
  "변경후 / 変更後",
  "담당자 / 担当者",
  "조치 메모 / 対応メモ",
  "근거 URL / 証拠URL",
];

const FRAUD_WORDS = [
  "偽物", "ニセモノ", "にせもの", "パチモン", "パチもん", "パチモノ",
  "コピー", "fake", "偽造品", "模倣品", "コピー商品", "模造品", "詐欺",
  "販売禁止商品", "規約違反", "強制返金", "中国",
];


// ── GET ──────────────────────────────────────────────────────
function doGet(e) {
  try {
    const sheetName = (e.parameter && e.parameter.sheet) || "";
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(sheetName);

    if (!sheet || sheet.getLastRow() <= 1) {
      return jsonOut({ data: [] });
    }

    if (DATA_SHEETS.indexOf(sheetName) >= 0) {
      ensureDataSchema(sheet);
      backfillDerivedFields(sheet);
    }

    return jsonOut({ data: sheetToObjects(sheet) });
  } catch (err) {
    return jsonOut({ status: "error", message: String(err) });
  }
}


// ── POST 라우팅 ───────────────────────────────────────────────
function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents || "{}");
    if (payload.action === "update") return handleUpdate(payload);
    if (payload.action === "batch_update") return handleBatchUpdate(payload);
    return handleAppend(payload);
  } catch (err) {
    return jsonOut({ status: "error", message: String(err) });
  }
}


// ── 데이터 조회 ───────────────────────────────────────────────
function sheetToObjects(sheet) {
  const range = sheet.getDataRange();
  const values = range.getValues();
  const formulas = range.getFormulas();
  const headers = values[0];
  const result = [];

  for (let i = 1; i < values.length; i++) {
    const row = {};
    for (let j = 0; j < headers.length; j++) {
      let value = values[i][j];
      const formula = formulas[i][j];
      if (formula && formula.startsWith("=HYPERLINK(")) {
        const match = formula.match(/=HYPERLINK\("([^"]+)"/);
        value = match ? match[1] : "";
      } else if (value instanceof Date) {
        value = Utilities.formatDate(value, "Asia/Seoul", "yyyy-MM-dd HH:mm:ss");
      }
      row[headers[j]] = value === null || value === undefined ? "" : String(value);
    }
    row["_row_index"] = i + 1;
    result.push(row);
  }
  return result;
}


// ── 행/Case 업데이트와 이력 ──────────────────────────────────
function handleUpdate(payload) {
  const result = applySingleUpdate(payload);
  return jsonOut(result);
}


function handleBatchUpdate(payload) {
  const changes = Array.isArray(payload.changes) ? payload.changes : [];
  if (changes.length > 500) {
    return jsonOut({ status: "error", message: "changes must be 500 rows or fewer" });
  }

  let changed = 0;
  const errors = [];
  for (let i = 0; i < changes.length; i++) {
    const result = applySingleUpdate(changes[i]);
    if (result.status === "ok") {
      changed += result.changed || 0;
    } else {
      errors.push({ index: i, message: result.message || "update failed" });
    }
  }

  return jsonOut({
    status: errors.length ? "partial" : "ok",
    changed: changed,
    errors: errors,
  });
}


function applySingleUpdate(payload) {
  const sheetName = String(payload.sheet || "");
  if (DATA_SHEETS.indexOf(sheetName) < 0) {
    return { status: "error", message: "invalid sheet" };
  }

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(sheetName);
  if (!sheet) return { status: "error", message: "sheet not found" };

  ensureDataSchema(sheet);
  const rowIndex = Number(payload.row_index);
  if (!rowIndex || rowIndex < 2 || rowIndex > sheet.getLastRow()) {
    return { status: "error", message: "invalid row_index" };
  }

  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const columns = headerMap(headers);
  const fieldMap = {
    falspos: "오탐지여부",
    status: "Status",
    reviewer: "담당자 / 担当者",
    action_note: "조치 메모 / 対応メモ",
  };

  const reviewer = String(
    payload.reviewer ||
    sheet.getRange(rowIndex, columns["담당자 / 担当者"]).getValue() ||
    "미지정"
  );
  const note = String(payload.action_note || "");
  const historyRows = [];

  Object.keys(fieldMap).forEach(function(key) {
    if (payload[key] === undefined) return;
    const header = fieldMap[key];
    const column = columns[header];
    const cell = sheet.getRange(rowIndex, column);
    const oldValue = String(cell.getValue() || "");
    const newValue = String(payload[key] || "");
    if (oldValue === newValue) return;

    cell.setValue(newValue);
    historyRows.push(buildHistoryRow(
      sheet,
      columns,
      rowIndex,
      header,
      oldValue,
      newValue,
      reviewer,
      note
    ));
  });

  if (historyRows.length > 0) {
    const now = new Date();
    sheet.getRange(rowIndex, columns["담당자 / 担当者"]).setValue(reviewer);
    if (payload.action_note !== undefined) {
      sheet.getRange(rowIndex, columns["조치 메모 / 対応メモ"]).setValue(note);
    }
    sheet.getRange(rowIndex, columns["최종 변경일 / 最終更新"]).setValue(now);
    appendHistoryRows(historyRows);
  }

  return { status: "ok", changed: historyRows.length };
}


function buildHistoryRow(
  sheet,
  columns,
  rowIndex,
  field,
  oldValue,
  newValue,
  reviewer,
  note
) {
  return [
    new Date(),
    sheet.getName(),
    rowIndex,
    String(sheet.getRange(rowIndex, columns["Case ID"]).getValue() || ""),
    String(sheet.getRange(rowIndex, columns["상품번호 / 商品番号"]).getValue() || ""),
    field,
    oldValue,
    newValue,
    reviewer,
    note,
    String(sheet.getRange(rowIndex, 3).getValue() || ""),
  ];
}


function appendHistoryRows(rows) {
  if (!rows.length) return;
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(HISTORY_SHEET);
  if (!sheet) {
    sheet = ss.insertSheet(HISTORY_SHEET);
    sheet.getRange(1, 1, 1, HISTORY_HEADERS.length).setValues([HISTORY_HEADERS]);
    applyHeaderStyle(sheet, HISTORY_HEADERS.length);
    sheet.setFrozenRows(1);
  }
  sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, HISTORY_HEADERS.length)
    .setValues(rows);
}


// ── 행 추가 ──────────────────────────────────────────────────
function handleAppend(payload) {
  const sheetName = String(payload.sheet || "");
  if (DATA_SHEETS.indexOf(sheetName) < 0) {
    return jsonOut({ status: "error", message: "invalid sheet" });
  }

  const inputHeaders = Array.isArray(payload.headers) ? payload.headers : [];
  const inputRows = Array.isArray(payload.rows) ? payload.rows : [];
  if (!inputHeaders.length) {
    return jsonOut({ status: "error", message: "headers are required" });
  }

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(sheetName);
  if (!sheet) sheet = ss.insertSheet(sheetName);

  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, inputHeaders.length).setValues([inputHeaders]);
  }
  ensureDataSchema(sheet);

  const targetHeaders = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const inputMap = headerMap(inputHeaders);
  const targetRows = [];

  for (let i = 0; i < inputRows.length; i++) {
    const input = inputRows[i];
    const byHeader = {};
    inputHeaders.forEach(function(header, index) {
      byHeader[header] = input[index] === undefined ? "" : input[index];
    });

    deriveMissingFields(byHeader, sheetName);
    targetRows.push(targetHeaders.map(function(header) {
      return byHeader[header] === undefined ? "" : byHeader[header];
    }));
  }

  if (targetRows.length) {
    const startRow = sheet.getLastRow() + 1;
    sheet.getRange(startRow, 1, targetRows.length, targetHeaders.length)
      .setValues(targetRows);

    const columns = headerMap(targetHeaders);
    for (let i = 0; i < targetRows.length; i++) {
      const likelihood = targetRows[i][columns["위험도 / 危険度"] - 1];
      const color = likelihood === "HIGH"
        ? "#FFD2D2"
        : likelihood === "MEDIUM" ? "#FFF3CD" : null;
      if (color) {
        sheet.getRange(startRow + i, 1, 1, targetHeaders.length).setBackground(color);
      }
    }
  }

  return jsonOut({ status: "ok", rows: targetRows.length });
}


function deriveMissingFields(row, sheetName) {
  const sourceUrlHeader = sheetName === GOOGLE_SHEET ? "URL" : "게시물 URL / 投稿URL";
  const summaryHeader = sheetName === GOOGLE_SHEET ? "개요 / 概要" : "게시물 내용 / 投稿内容";
  const qoo10Header = sheetName === GOOGLE_SHEET ? "Qoo10 상품 / 商品P" : "Qoo10 상품 URL";
  const sourceUrl = String(row[sourceUrlHeader] || "");
  const qoo10Link = String(row[qoo10Header] || "");
  const productNumber = String(row["상품번호 / 商品番号"] || extractProductNumber(qoo10Link));

  row["상품번호 / 商品番号"] = productNumber;
  row["Case ID"] = row["Case ID"] || buildCaseId(productNumber, sourceUrl);
  row["탐지 근거 / 検知根拠"] = row["탐지 근거 / 検知根拠"] ||
    extractEvidence(String(row[summaryHeader] || ""));
  row["AI 판정 / AI判定"] = row["AI 판정 / AI判定"] || "PENDING";
  row["Status"] = row["Status"] || "New";
}


// ── 기존 시트 스키마 확장 및 백필 ────────────────────────────
function ensureDataSchema(sheet) {
  let headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const missing = EXTRA_HEADERS.filter(function(header) {
    return headers.indexOf(header) < 0;
  });
  if (missing.length) {
    sheet.getRange(1, headers.length + 1, 1, missing.length).setValues([missing]);
    headers = headers.concat(missing);
  }

  applyHeaderStyle(sheet, headers.length);
  applyDropdowns(sheet, headers);
  applyColumnWidths(sheet);
  sheet.setFrozenRows(1);
}


function backfillDerivedFields(sheet) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return;

  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const columns = headerMap(headers);
  const sourceUrlHeader = sheet.getName() === GOOGLE_SHEET ? "URL" : "게시물 URL / 投稿URL";
  const summaryHeader = sheet.getName() === GOOGLE_SHEET ? "개요 / 概要" : "게시물 내용 / 投稿内容";
  const qoo10Header = sheet.getName() === GOOGLE_SHEET ? "Qoo10 상품 / 商品P" : "Qoo10 상품 URL";
  const values = sheet.getRange(2, 1, lastRow - 1, headers.length).getValues();
  const productNumbers = [];
  const caseIds = [];
  const evidences = [];
  const aiLabels = [];
  let changed = false;

  for (let i = 0; i < values.length; i++) {
    const sourceUrl = String(values[i][columns[sourceUrlHeader] - 1] || "");
    const qoo10Link = String(values[i][columns[qoo10Header] - 1] || "");
    const summary = String(values[i][columns[summaryHeader] - 1] || "");
    let productNumber = String(values[i][columns["상품번호 / 商品番号"] - 1] || "");
    if (!productNumber) {
      productNumber = extractProductNumber(qoo10Link);
      values[i][columns["상품번호 / 商品番号"] - 1] = productNumber;
      changed = true;
    }
    if (!values[i][columns["Case ID"] - 1]) {
      values[i][columns["Case ID"] - 1] = buildCaseId(productNumber, sourceUrl);
      changed = true;
    }
    if (!values[i][columns["탐지 근거 / 検知根拠"] - 1]) {
      values[i][columns["탐지 근거 / 検知根拠"] - 1] = extractEvidence(summary);
      changed = true;
    }
    if (!values[i][columns["AI 판정 / AI判定"] - 1]) {
      values[i][columns["AI 판정 / AI判定"] - 1] = "PENDING";
      changed = true;
    }
    productNumbers.push([values[i][columns["상품번호 / 商品番号"] - 1]]);
    caseIds.push([values[i][columns["Case ID"] - 1]]);
    evidences.push([values[i][columns["탐지 근거 / 検知根拠"] - 1]]);
    aiLabels.push([values[i][columns["AI 판정 / AI判定"] - 1]]);
  }

  if (changed) {
    // G열 등의 HYPERLINK 수식을 표시값으로 덮어쓰지 않도록
    // 파생 필드 J:M만 개별 갱신한다.
    sheet.getRange(2, columns["상품번호 / 商品番号"], values.length, 1).setValues(productNumbers);
    sheet.getRange(2, columns["Case ID"], values.length, 1).setValues(caseIds);
    sheet.getRange(2, columns["탐지 근거 / 検知根拠"], values.length, 1).setValues(evidences);
    sheet.getRange(2, columns["AI 판정 / AI判定"], values.length, 1).setValues(aiLabels);
  }
}


// ── 파생값 유틸리티 ──────────────────────────────────────────
function extractProductNumber(value) {
  let text = String(value || "");
  try {
    text = decodeURIComponent(text);
  } catch (_err) {}

  const patterns = [
    /[?&]goodscode=(\d+)/i,
    /\/g\/(\d+)(?:[/?#]|$)/i,
    /\/item\/(?:[^/?#]+\/)*(\d+)(?:[/?#]|$)/i,
    /\/(Q\d{7,})(?:[/?#]|$)/i,
  ];
  for (let i = 0; i < patterns.length; i++) {
    const match = text.match(patterns[i]);
    if (match) return String(match[1]).toUpperCase();
  }
  return "";
}


function buildCaseId(productNumber, sourceUrl) {
  if (productNumber) return "PRODUCT:" + productNumber;
  const normalized = String(sourceUrl || "").split("#")[0];
  const bytes = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    normalized,
    Utilities.Charset.UTF_8
  );
  const hex = bytes.map(function(value) {
    return ("0" + (value & 255).toString(16)).slice(-2);
  }).join("");
  return "URL:" + hex.substring(0, 16);
}


function extractEvidence(text) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  const lower = normalized.toLowerCase();
  let found = -1;
  for (let i = 0; i < FRAUD_WORDS.length; i++) {
    const index = lower.indexOf(FRAUD_WORDS[i].toLowerCase());
    if (index >= 0 && (found < 0 || index < found)) found = index;
  }
  if (found < 0) return normalized.substring(0, 240);
  const start = Math.max(0, found - 120);
  const end = Math.min(normalized.length, found + 120);
  return (start > 0 ? "… " : "") +
    normalized.substring(start, end) +
    (end < normalized.length ? " …" : "");
}


// ── 스타일·헬퍼 ──────────────────────────────────────────────
function headerMap(headers) {
  const result = {};
  headers.forEach(function(header, index) {
    result[header] = index + 1;
  });
  return result;
}


function applyHeaderStyle(sheet, numCols) {
  sheet.getRange(1, 1, 1, numCols)
    .setBackground("#4A4A4A")
    .setFontColor("#FFFFFF")
    .setFontWeight("bold")
    .setWrap(false);
}


function applyDropdowns(sheet, headers) {
  const columns = headerMap(headers);
  const fpRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(["O", "X"], true)
    .setAllowInvalid(true)
    .build();
  sheet.getRange(2, columns["오탐지여부"], 4999, 1).setDataValidation(fpRule);

  const statusRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(["New", "Reviewing", "Actioned", "Closed"], true)
    .setAllowInvalid(false)
    .build();
  sheet.getRange(2, columns["Status"], 4999, 1).setDataValidation(statusRule);
}


function applyColumnWidths(sheet) {
  const widths = [
    90, 150, 250, 350, 150, 80, 100, 90, 110, 120,
    170, 350, 150, 100, 260, 300, 120, 240, 150, 180,
  ];
  widths.forEach(function(width, index) {
    sheet.setColumnWidth(index + 1, width);
  });
}


function jsonOut(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
