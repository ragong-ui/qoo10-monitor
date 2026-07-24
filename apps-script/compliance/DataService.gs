// ============================================================
// DataService.gs — シートデータ操作ヘルパー
// Code.gs の HEADERS / COL / SHEET_NAME を参照する
// ============================================================

/**
 * 指定シートを取得、存在しなければ headers を 1 行目に設定して新規作成。
 *
 * @param {string} name    シート名
 * @param {Array}  headers ヘッダー文字列配列 (省略可)
 * @returns {GoogleAppsScript.Spreadsheet.Sheet}
 */
function getSheetOrCreate(name, headers) {  // eslint-disable-line no-redeclare
  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(name);

  if (!sheet) {
    sheet = ss.insertSheet(name);
    if (headers && headers.length) {
      sheet.appendRow(headers);
      sheet.getRange(1, 1, 1, headers.length)
        .setBackground("#1a1f36")
        .setFontColor("#e2e8f0")
        .setFontWeight("bold");
      sheet.setFrozenRows(1);
    }
  }

  return sheet;
}

/**
 * シートに行配列を一括追記する。
 *
 * @param {string}        sheetName  シート名
 * @param {Array<Array>}  rows       追記する行の配列 (各行は値の配列)
 * @returns {number} 追記した行数
 */
function appendRows(sheetName, rows) {
  if (!rows || rows.length === 0) return 0;
  var sheet = getSheetOrCreate(sheetName, HEADERS);

  for (var i = 0; i < rows.length; i++) {
    sheet.appendRow(rows[i]);
  }
  return rows.length;
}

/**
 * シートのデータ行をオブジェクト配列で返す (ヘッダー行をキーとして使用)。
 *
 * @param {string} sheetName  シート名
 * @param {Object} filters    { columnName: value, ... } — 完全一致フィルタ (省略可)
 * @returns {Array<Object>}   各行を { header: value } 形式で返す
 */
function getRows(sheetName, filters) {
  var sheet = _getSheetByName(sheetName);
  if (!sheet || sheet.getLastRow() < 2) return [];

  var lastRow  = sheet.getLastRow();
  var lastCol  = sheet.getLastColumn();
  var allData  = sheet.getRange(1, 1, lastRow, lastCol).getValues();
  var headers  = allData[0].map(function(h) { return String(h); });
  var result   = [];

  for (var i = 1; i < allData.length; i++) {
    var row = allData[i];
    var obj = {};
    for (var j = 0; j < headers.length; j++) {
      var v = row[j];
      obj[headers[j]] = (v === null || v === undefined) ? "" : String(v);
    }
    obj["_row_index"] = i + 1; // 1-based (header=1, first data=2)

    // フィルタ適用
    if (filters) {
      var match = true;
      for (var key in filters) {
        if (filters.hasOwnProperty(key)) {
          if (obj[key] !== String(filters[key])) {
            match = false;
            break;
          }
        }
      }
      if (!match) continue;
    }

    result.push(obj);
  }

  return result;
}

/**
 * 指定シートの指定行 (1-based) の特定カラムを更新する。
 *
 * @param {string} sheetName  シート名
 * @param {number} rowIndex   更新対象行 (1-based、ヘッダー=1)
 * @param {Object} updates    { columnName: newValue, ... }
 * @returns {boolean} 成功した場合 true
 */
function updateRow(sheetName, rowIndex, updates) {
  var sheet = _getSheetByName(sheetName);
  if (!sheet) return false;
  if (rowIndex < 2 || rowIndex > sheet.getLastRow()) return false;

  var headers = getHeaders(sheet);

  for (var key in updates) {
    if (!updates.hasOwnProperty(key)) continue;
    var colIndex = headers.indexOf(key);
    if (colIndex < 0) continue; // 存在しない列はスキップ
    sheet.getRange(rowIndex, colIndex + 1).setValue(updates[key]);
  }

  return true;
}

/**
 * シートのヘッダー行 (1 行目) を文字列配列で返す。
 *
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @returns {Array<string>}
 */
function getHeaders(sheet) {
  if (!sheet || sheet.getLastColumn() < 1) return [];
  return sheet.getRange(1, 1, 1, sheet.getLastColumn())
    .getValues()[0]
    .map(function(h) { return String(h); });
}

/**
 * 指定シート名でシートを取得する内部ヘルパー。
 *
 * @param {string} name
 * @returns {GoogleAppsScript.Spreadsheet.Sheet|null}
 */
function _getSheetByName(name) {
  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  return ss.getSheetByName(name) || null;
}

/**
 * run_id に一致するすべての行を返す。
 *
 * @param {string} runId
 * @returns {Array<Object>}
 */
function getRowsByRunId(runId) {
  return getRows(SHEET_NAME, { "Run ID": runId });
}

/**
 * 指定の重要度 / ステータスに一致する行を返す (件数制限あり)。
 *
 * @param {string} severity   critical | high | medium | low (空文字で全件)
 * @param {string} status     new | Reviewing | Actioned | Closed (空文字で全件)
 * @param {number} limit      最大取得件数 (デフォルト 200)
 * @returns {Array<Object>}
 */
function getFilteredRows(severity, status, limit) {
  limit = limit || 200;
  var all = getRows(SHEET_NAME);
  var result = [];

  for (var i = 0; i < all.length; i++) {
    var row = all[i];
    if (severity && row["重要度"].toLowerCase() !== severity.toLowerCase()) continue;
    if (status   && row["ステータス"] !== status) continue;
    result.push(row);
    if (result.length >= limit) break;
  }

  return result;
}

/**
 * ComplianceBriefing シートのサマリ統計を返す。
 *
 * @returns {Object} { total, critical, high, medium, low, by_status }
 */
function getSummaryStats() {
  var all = getRows(SHEET_NAME);
  var stats = { total: all.length, critical: 0, high: 0, medium: 0, low: 0, by_status: {} };

  for (var i = 0; i < all.length; i++) {
    var sev    = (all[i]["重要度"] || "").toLowerCase();
    var status = all[i]["ステータス"] || "new";

    if (sev === "critical") stats.critical++;
    else if (sev === "high") stats.high++;
    else if (sev === "medium") stats.medium++;
    else if (sev === "low") stats.low++;

    stats.by_status[status] = (stats.by_status[status] || 0) + 1;
  }

  return stats;
}
