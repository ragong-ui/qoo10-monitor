# Japan Compliance Briefing — Google Apps Script 배포 가이드

이 문서는 **Compliance Briefing Dashboard**를 Google Apps Script Web App으로 배포하는 절차를 설명합니다.

기존 위조품 모니터링 Apps Script(`/qoo10-monitor/Code.gs`)와는 **별도** 프로젝트로 배포해야 합니다.

---

## 1. 파일 구조 및 각 파일 역할

```
apps-script/compliance/
├── appsscript.json   — Apps Script 매니페스트 (시간대·런타임 설정)
├── Code.gs           — Web App 메인 (doGet / doPost 라우팅)
├── DataService.gs    — 시트 CRUD 헬퍼 함수
├── Index.html        — 대시보드 HTML 템플릿
├── Styles.html       — CSS (dark-mode, CSS 변수 기반)
└── Scripts.html      — 클라이언트 JavaScript (데이터 로딩·렌더링)
```

| 파일 | 역할 |
|---|---|
| `Code.gs` | HTTP 요청 라우팅. `batch_append` / `mark_dashboard_ready` / `update_status` POST 처리, `getData` GET 처리 |
| `DataService.gs` | `getSheetOrCreate`, `appendRows`, `getRows`, `updateRow`, `getHeaders` 등 시트 공통 함수 |
| `Index.html` | `<?!= include("Styles"); ?>` / `<?!= include("Scripts"); ?>` 로 CSS·JS 인클루드 |
| `Styles.html` | `<style>` 블록. CSS 변수로 다크모드 컬러 관리 |
| `Scripts.html` | `<script>` 블록. `google.script.run`으로 서버 함수 호출 |

---

## 2. Google Apps Script 프로젝트 생성 방법

1. [https://script.google.com](https://script.google.com) 접속
2. **새 프로젝트** 클릭
3. 프로젝트 이름: `Japan Compliance Briefing Dashboard`
4. 좌측 파일 패널에서 **+** → 파일 추가:
   - `Code.gs` (기본 파일에 덮어쓰기)
   - `DataService.gs` (Script 파일)
   - `Index.html` (HTML 파일)
   - `Styles.html` (HTML 파일)
   - `Scripts.html` (HTML 파일)
5. **프로젝트 설정** (톱니바퀴) → **매니페스트 파일 표시** 체크
6. `appsscript.json` 내용으로 덮어쓰기

---

## 3. 배포(Deploy as Web App) 단계

1. 에디터 오른쪽 상단 **배포** → **새 배포**
2. 배포 유형: **웹 앱** 선택
3. 배포 설정:

   | 항목 | 권장 값 |
   |---|---|
   | 설명 | `Japan Compliance Briefing v1` |
   | 실행 계정 | 나 (배포자) |
   | 액세스 권한 | 모든 사용자(익명 포함) |

4. **배포** 클릭 → 표시된 **웹 앱 URL** 복사 (`https://script.google.com/macros/s/.../exec`)

> 코드 수정 후에는 반드시 **배포 관리** → **새 버전** 또는 **새 배포**를 해야 변경사항이 반영됩니다.

---

## 4. `COMPLIANCE_APPS_SCRIPT_URL` 환경변수 설정

`.env` 파일 (또는 Windows 환경변수)에 배포된 URL을 추가합니다:

```env
# C:\Users\ragong\qoo10-monitor\.env

COMPLIANCE_APPS_SCRIPT_URL=https://script.google.com/macros/s/AKfycb.../exec
COMPLIANCE_APPS_SCRIPT_TOKEN=충분히-긴-임의-문자열
GOOGLE_SHEETS_EXPORT_ENABLED=true
```

동일한 토큰을 Git에서 제외된 `Secrets.gs`의
`COMPLIANCE_API_TOKEN`에도 설정합니다. Python 코드는 URL과 토큰을 함께
사용하며, 토큰이 없거나 불일치하면 모든 쓰기 요청을 거부합니다.

### API 호출 예시

```python
import requests

url = "https://script.google.com/macros/s/.../exec"

# 데이터 추가 (batch_append)
resp = requests.post(url, json={
    "action": "batch_append",
    "run_id": "abc12345",
    "rows": [{
        "detected_at": "2026-07-24T09:00:00Z",
        "run_id": "abc12345",
        "category": "recall",
        "country": "KR",
        "severity": "high",
        "confidence": "0.85",
        "title_ko": "[리콜] 가전제품 A — 과열 위험",
        "title_ja": "[リコール] 家電製品A — 過熱リスク",
        "summary_ko": "소비자원 리콜 공고",
        "summary_ja": "消費者院リコール公告",
        "source_url": "https://www.safetykorea.go.kr/...",
        "brand": "브랜드명",
        "marketplace": "Qoo10",
        "status": "new",
        "notes": "",
    }]
})
print(resp.json())  # {"status": "ok", "rows": 1, "run_id": "abc12345"}

# 대시보드 준비 완료 표시
requests.post(url, json={"action": "mark_dashboard_ready", "run_id": "abc12345"})

# 상태 업데이트 (row_index는 1-based, 헤더=1)
requests.post(url, json={
    "action": "update_status",
    "row_index": 3,
    "status": "Actioned",
    "notes": "대응 완료"
})

# 데이터 조회 (GET)
resp = requests.get(url, params={
    "action": "getData",
    "severity": "critical",
    "status": "new",
    "limit": "100",
})
data = resp.json()["data"]
```

---

## 5. 권한 설정

| 상황 | `access` 설정 | 설명 |
|---|---|---|
| 현재 자동 실행 | `"ANYONE_ANONYMOUS"` | 페이지·읽기 API는 공개, 모든 쓰기는 공유 비밀 토큰 필수 |
| 사내 팀 전용 | `"DOMAIN"` | 동일 Google Workspace 도메인 사용자만 접근하며 별도 OAuth 업로더 필요 |
| 개발·테스트 | `"MYSELF"` | 배포자 본인만 접근 |

`appsscript.json`의 `"access"` 값을 변경하고 **재배포**합니다.
`ANYONE_ANONYMOUS`를 사용할 때는 반드시 `Secrets.gs` 토큰 검증을 유지합니다.

---

## 6. 시트 컬럼 구조

| 열 | 헤더(일본어) | 설명 |
|---|---|---|
| A | 検出日時 | ISO 8601 UTC |
| B | Run ID | 실행 ID |
| C | カテゴリ | recall / regulation / safety / competitor |
| D | 国 | JP / KR / MULTI |
| E | 重要度 | critical / high / medium / low |
| F | 信頼度 | 0.0 ~ 1.0 |
| G | タイトル(KO) | 한국어 제목 |
| H | タイトル(JA) | 일본어 제목 |
| I | 概要(KO) | 한국어 요약 |
| J | 概要(JA) | 일본어 요약 |
| K | ソースURL | 원문 URL |
| L | ブランド | 브랜드명 |
| M | マーケットプレイス | Qoo10 / 楽天 / Amazon 등 |
| N | ステータス | new / Reviewing / Actioned / Closed / FalsePositive |
| O | 備考 | 담당자 메모 |
| P | dashboard_ready | 내부 플래그 (자동 숨김) |

---

## 7. 문제해결

### "Authorization required" / 권한 오류
- Apps Script 에디터에서 `doGet()` 함수를 직접 실행 → OAuth 동의 화면에서 승인
- 자동 실행 배포는 `ANYONE_ANONYMOUS`와 쓰기 API 토큰을 함께 설정 후 재배포

### 시트가 자동 생성되지 않는 경우
- 스크립트가 올바른 스프레드시트에 연결되어 있는지 확인
- 에디터에서 직접 `getSheetOrCreate("ComplianceBriefing", HEADERS)` 실행

### POST 요청이 403/404 반환
- 배포 URL이 `/exec`로 끝나는지 확인 (`/dev`는 개발용 — 인증 필요)
- 코드 수정 후 반드시 **재배포** 필요

### 대시보드 데이터가 표시되지 않음
- 브라우저 개발자 도구 Console에서 오류 확인
- `google.script.run`은 배포된 Web App URL(`/exec`)에서만 동작
- 로컬 HTML 파일 직접 열기로는 동작하지 않음

### clasp를 이용한 로컬 개발 (선택)

```bash
npm install -g @google/clasp
clasp login
# 기존 프로젝트 clone
clasp clone <scriptId>
# 파일 업로드
clasp push
# 새 버전 배포
clasp deploy --description "v2"
```

`scriptId`는 Apps Script 에디터 URL의 `/projects/<scriptId>/edit`에서 확인합니다.

### 운영 프로젝트와 스프레드시트 고정

- 운영 Google 계정: `lany052007@gmail.com`
- 공식 Apps Script 프로젝트 ID: `1dYnlS14VXQ2PPQZgS6xteVJ94FuiFEIgSFR8gwCoDUpaAdI3pqm0TLbq`
- 운영 스프레드시트 ID: `1rF_QXLRL7XXw34myMmXkw4wHSO7sCYXFSI9x_E74M5Q`
- 시트 탭: `ComplianceBriefing`

Web App에는 활성 스프레드시트 문맥이 없으므로 `Code.gs`의
`COMPLIANCE_SPREADSHEET_ID`를 `SpreadsheetApp.openById()`로 직접 엽니다.
Script Property나 활성 시트로 우회하지 않으므로 다른 프로젝트·시트로 잘못
연결되는 것을 방지합니다. 배포 후 `/exec?action=getData&limit=1`이
`{"data":[...]}`를 반환하는지 확인합니다. `batch_append`는 같은 `run_id`의
완료된 행을 재사용하므로 안전하게 재시도할 수 있습니다.
