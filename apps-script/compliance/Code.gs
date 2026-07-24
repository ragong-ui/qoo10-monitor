// ============================================================
// Japan Marketplace Compliance Briefing — Google Apps Script
// Web App: doGet / doPost
// ============================================================

var SHEET_NAME = "ComplianceBriefing";

var HEADERS = [
  "検出日時",         // A  detected_at
  "Run ID",          // B  run_id
  "カテゴリ",         // C  category
  "国",              // D  country
  "重要度",           // E  severity
  "信頼度",           // F  confidence
  "タイトル(KO)",     // G  title_ko
  "タイトル(JA)",     // H  title_ja
  "概要(KO)",        // I  summary_ko
  "概要(JA)",        // J  summary_ja
  "ソースURL",        // K  source_url
  "ブランド",         // L  brand
  "マーケットプレイス", // M  marketplace
  "ステータス",        // N  status
  "備考",            // O  notes
  "dashboard_ready", // P  内部フラグ (非表示)
];

var COL = {};
HEADERS.forEach(function(h, i) { COL[h] = i + 1; });

// ── doGet ─────────────────────────────────────────────────────
/**
 * HTTP GET エントリポイント。
 * ?action=getData  → JSON データ API
 * (その他)         → ダッシュボード HTML を返す
 */
function doGet(e) {
  try {
    var params = (e && e.parameter) ? e.parameter : {};

    if (params.action === "getData") {
      return _handleGetData(params);
    }

    // ダッシュボード HTML ページを返す
    var html = HtmlService.createTemplateFromFile("Index")
      .evaluate()
      .setTitle("🗾 Japan Compliance Briefing")
      .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
    return html;

  } catch (err) {
    return _jsonOut({ status: "error", message: err.message });
  }
}

// ── doPost ────────────────────────────────────────────────────
/**
 * HTTP POST エントリポイント。
 * action="batch_append"      → 行を一括追加
 * action="mark_dashboard_ready" → run_id の dashboard_ready を更新
 * action="update_status"     → ステータス / 備考を更新
 */
function doPost(e) {
  try {
    var payload = JSON.parse(e.postData.contents);
    var action = payload.action;

    if (action === "batch_append") {
      return _handleBatchAppend(payload);
    }
    if (action === "mark_dashboard_ready") {
      return _handleMarkDashboardReady(payload);
    }
    if (action === "update_status") {
      return _handleUpdateStatus(payload);
    }

    return _jsonOut({ status: "error", message: "Unknown action: " + action });

  } catch (err) {
    return _jsonOut({ status: "error", message: err.message });
  }
}

// ── action: batch_append ──────────────────────────────────────
/**
 * ComplianceBriefing シートに行を一括追加する。
 *
 * payload:
 *   { action: "batch_append",
 *     run_id: "abc123",
 *     rows: [
 *       { detected_at, run_id, category, country, severity, confidence,
 *         title_ko, title_ja, summary_ko, summary_ja, source_url,
 *         brand, marketplace, status, notes }
 *       ...
 *     ]
 *   }
 */
function _handleBatchAppend(payload) {
  var sheet = getSheetOrCreate(SHEET_NAME, HEADERS);
  var rows = payload.rows || [];
  var run_id = payload.run_id || "";
  var appended = 0;

  for (var i = 0; i < rows.length; i++) {
    var r = rows[i];
    var rowArr = [
      r.detected_at   || new Date().toISOString(),
      r.run_id        || run_id,
      r.category      || "",
      r.country       || "",
      r.severity      || "medium",
      r.confidence    || "",
      r.title_ko      || "",
      r.title_ja      || "",
      r.summary_ko    || "",
      r.summary_ja    || "",
      r.source_url    || "",
      r.brand         || "",
      r.marketplace   || "",
      r.status        || "new",
      r.notes         || "",
      "",  // dashboard_ready — 初期値は空
    ];

    sheet.appendRow(rowArr);

    // 重要度によって行の背景色を設定
    var lastRow = sheet.getLastRow();
    var sev = (r.severity || "").toLowerCase();
    var bg = _severityBg(sev);
    if (bg) {
      sheet.getRange(lastRow, 1, 1, HEADERS.length).setBackground(bg);
    }

    appended++;
  }

  return _jsonOut({ status: "ok", rows: appended, run_id: run_id });
}

// ── action: mark_dashboard_ready ──────────────────────────────
/**
 * 指定 run_id のすべての行の dashboard_ready 列を "1" にする。
 *
 * payload: { action: "mark_dashboard_ready", run_id: "abc123" }
 */
function _handleMarkDashboardReady(payload) {
  var run_id = payload.run_id || "";
  if (!run_id) {
    return _jsonOut({ status: "error", message: "run_id is required" });
  }

  var sheet = _getSheet(SHEET_NAME);
  if (!sheet) {
    return _jsonOut({ status: "error", message: "Sheet not found: " + SHEET_NAME });
  }

  var lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    return _jsonOut({ status: "ok", updated: 0 });
  }

  var runIdColIndex = COL["Run ID"] - 1;
  var dashColIndex  = COL["dashboard_ready"] - 1;
  var data = sheet.getRange(2, 1, lastRow - 1, HEADERS.length).getValues();
  var updated = 0;

  for (var i = 0; i < data.length; i++) {
    if (String(data[i][runIdColIndex]) === run_id) {
      sheet.getRange(i + 2, COL["dashboard_ready"]).setValue("1");
      updated++;
    }
  }

  return _jsonOut({ status: "ok", updated: updated });
}

// ── action: update_status ──────────────────────────────────────
/**
 * 指定行のステータスと備考を更新する。
 *
 * payload: { action: "update_status", row_index: 5, status: "Actioned", notes: "対応済み" }
 * row_index は 1-based (ヘッダー = 1、最初のデータ行 = 2)
 */
function _handleUpdateStatus(payload) {
  var sheet = _getSheet(SHEET_NAME);
  if (!sheet) {
    return _jsonOut({ status: "error", message: "Sheet not found: " + SHEET_NAME });
  }

  var rowIndex = Number(payload.row_index);
  if (!rowIndex || rowIndex < 2) {
    return _jsonOut({ status: "error", message: "Invalid row_index: " + payload.row_index });
  }

  if (payload.status !== undefined) {
    sheet.getRange(rowIndex, COL["ステータス"]).setValue(payload.status);
  }
  if (payload.notes !== undefined) {
    sheet.getRange(rowIndex, COL["備考"]).setValue(payload.notes);
  }

  return _jsonOut({ status: "ok" });
}

// ── action: getData (GET) ─────────────────────────────────────
/**
 * フィルタ付きでシートデータを JSON 返却。
 *
 * クエリパラメータ:
 *   severity  = critical | high | medium | low
 *   status    = new | Reviewing | Actioned | Closed
 *   category  = recall | regulation | safety | competitor
 *   country   = JP | KR | MULTI
 *   limit     = 数値 (デフォルト 200)
 *   q         = テキスト検索 (タイトル KO / JA)
 */
function _handleGetData(params) {
  var sheet = _getSheet(SHEET_NAME);
  if (!sheet || sheet.getLastRow() < 2) {
    return _jsonOut({ data: [] });
  }

  var lastRow = sheet.getLastRow();
  var data    = sheet.getRange(2, 1, lastRow - 1, HEADERS.length).getValues();
  var limit   = Math.min(parseInt(params.limit || "200", 10), 1000);

  var result = [];
  for (var i = 0; i < data.length; i++) {
    var row = data[i];
    var obj = {};
    for (var j = 0; j < HEADERS.length; j++) {
      var v = row[j];
      obj[HEADERS[j]] = (v === null || v === undefined) ? "" : String(v);
    }
    obj["_row_index"] = i + 2; // 1-based, header=1

    // フィルタ適用
    if (params.severity && obj["重要度"].toLowerCase() !== params.severity.toLowerCase()) continue;
    if (params.status   && obj["ステータス"] !== params.status) continue;
    if (params.category && obj["カテゴリ"].toLowerCase() !== params.category.toLowerCase()) continue;
    if (params.country  && obj["国"] !== params.country) continue;
    if (params.q) {
      var q = params.q.toLowerCase();
      var hay = (obj["タイトル(KO)"] + " " + obj["タイトル(JA)"]).toLowerCase();
      if (hay.indexOf(q) === -1) continue;
    }

    result.push(obj);
    if (result.length >= limit) break;
  }

  return _jsonOut({ data: result });
}

// ── シートヘルパー ─────────────────────────────────────────────

/**
 * ComplianceBriefing シートを取得、なければ作成してヘッダーを設定する。
 */
function getSheetOrCreate(name, headers) {
  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(name);

  if (!sheet) {
    sheet = ss.insertSheet(name);
    sheet.appendRow(headers);
    _applyHeaderStyle(sheet, headers.length);
    _applyStatusDropdown(sheet);
    _applyColumnWidths(sheet);
    sheet.setFrozenRows(1);

    // dashboard_ready 列を非表示
    var dcol = headers.indexOf("dashboard_ready") + 1;
    if (dcol > 0) sheet.hideColumns(dcol);
  }

  return sheet;
}

function _getSheet(name) {
  return SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name);
}

// ── スタイル設定 ───────────────────────────────────────────────

function _applyHeaderStyle(sheet, numCols) {
  sheet.getRange(1, 1, 1, numCols)
    .setBackground("#1a1f36")
    .setFontColor("#e2e8f0")
    .setFontWeight("bold")
    .setWrap(false);
}

function _applyStatusDropdown(sheet) {
  var rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(["new", "Reviewing", "Actioned", "Closed", "FalsePositive"], true)
    .setAllowInvalid(false)
    .build();
  sheet.getRange(2, COL["ステータス"], 999, 1).setDataValidation(rule);
}

function _applyColumnWidths(sheet) {
  // A   B    C    D   E    F    G    H    I    J    K    L    M    N    O    P
  var widths = [140, 90, 80, 50, 70, 60, 200, 200, 250, 250, 200, 100, 120, 90, 150, 10];
  widths.forEach(function(w, i) {
    sheet.setColumnWidth(i + 1, w);
  });
}

function _severityBg(severity) {
  var map = {
    "critical": "#fde8e8",
    "high":     "#fef3e2",
    "medium":   "#fefce8",
    "low":      "#f0fdf4",
  };
  return map[severity] || null;
}

// ── JSON レスポンスヘルパー ────────────────────────────────────

function _jsonOut(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// ── インクルードヘルパー (テンプレート用) ─────────────────────

function include(filename) {
  return HtmlService.createHtmlOutputFromFile(filename).getContent();
}
