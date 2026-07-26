# Japan Compliance Briefing — Operations Runbook

---

## 일일 점검

**Task Scheduler 상태 확인:**
```powershell
Get-ScheduledTask -TaskName "Japan_Compliance_Briefing" | Select-Object State, LastRunTime, LastTaskResult
```

**로그 확인:**
```bash
# 오늘 로그
type logs\compliance_%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%.log
```

**DB 알림 현황:**
```sql
-- 오늘 알림 현황
SELECT status, severity, COUNT(*) FROM alerts
WHERE date(first_seen) = date('now') GROUP BY status, severity;

-- 소스별 수집 상태
SELECT source_id, last_status, last_success, consecutive_failures, error_msg
FROM source_health
ORDER BY CASE last_status
  WHEN 'failed' THEN 1
  WHEN 'partial' THEN 2
  WHEN 'ok' THEN 3
  ELSE 4
END, consecutive_failures DESC;
```

비활성화한 소스는 실행하지 않으므로 `source_health`가 갱신되지 않습니다.
실행 로그의 `source_statuses`와 `disabled_sources`를 함께 확인하세요.

---

## 장애 대응

### 운영 화면과 연결 상태 확인

- Qoo10 SNS Google/X 검토 화면:
  `https://qoo10-monitor-kpcsgufhoixrfo6ekyxmc7.streamlit.app/`
- Japan Compliance Briefing 검토 화면:
  `https://script.google.com/macros/s/AKfycbxP0EOrSkh5PQhUlpZfSL3bbheQYmR9JWjoO0uaz_u1FkVpgBIhwSGSDrypUqOWITLw/exec`
- Compliance 읽기 API 상태 확인:
  위 URL 뒤에 `?action=getData&limit=1`을 붙여 `{"data":[...]}` 응답을 확인합니다.

Streamlit에는 `GOOGLE_APPS_SCRIPT_URL`만 사용하고, Compliance에는
`COMPLIANCE_APPS_SCRIPT_URL`만 사용합니다. 화면이나 컬럼이 서로 뒤섞여
보이면 가장 먼저 두 환경변수가 서로 바뀌지 않았는지 확인합니다.

### 케이스 1: 로그 파일이 0바이트 (작업이 멈춘 경우)

**원인:** 네트워크 초기화 전 DNS hang
**확인:**
```powershell
Get-ScheduledTask -TaskName "Japan_Compliance_Briefing" | Format-List State
```
**조치:**
```powershell
Stop-ScheduledTask -TaskName "Japan_Compliance_Briefing"
# 수동 실행
python -u compliance_main.py
```

### 케이스 2: Brave Search API 오류 (402/429)

**원인:** API 크레딧 소진 또는 rate limit
**확인:** `logs/compliance_*.log` 에서 `[brave_news]` 오류 확인
**조치:**
1. `BRAVE_SEARCH_API_KEY` 확인 (Brave 대시보드)
2. `COMPLIANCE_DRY_RUN=true` 로 전환해 드라이런으로 운영

### 케이스 3: Slack 포스팅 실패

**확인:** 로그에서 `[slack] API error` 확인
**조치:**
```python
# 수동 테스트
from compliance_briefing.config import ComplianceConfig
from compliance_briefing.slack_notifier import post_compliance_briefing
cfg = ComplianceConfig()
cfg.slack_publish_enabled = True
# 알림 없이 포스팅 테스트 (빈 리스트)
post_compliance_briefing(cfg, "test-run", [])
```

### 케이스 4: Google Sheets 업로드 실패

**확인:** 로그에서 `[sheets] Upload failed` 확인
**조치:**
1. `COMPLIANCE_APPS_SCRIPT_URL` 확인
2. `COMPLIANCE_APPS_SCRIPT_TOKEN`이 Apps Script `Secrets.gs`와 일치하는지 확인
3. Apps Script 배포 상태 확인 (Google Apps Script 콘솔)
4. Apps Script 실행 로그 확인 (Stackdriver)

### 케이스 5: LLM API 오류

**결과:** 자동으로 규칙 기반 요약으로 폴백됨 (정상 동작)
**로그 확인:** `[llm/anthropic] Failed for item` 메시지
**조치:** `LLM_PROVIDER=disabled` 로 전환하면 API 호출 없이 규칙 기반만 사용

---

## 수동 실행

```bash
# 드라이런 (픽스처 데이터)
set COMPLIANCE_DRY_RUN=true && python -u compliance_main.py

# 실제 수집 (Sheets/Slack 비활성)
set COMPLIANCE_DRY_RUN=false && python -u compliance_main.py

# 전체 활성화 (실제 수집 + Sheets + Slack)
set COMPLIANCE_DRY_RUN=false
set GOOGLE_SHEETS_EXPORT_ENABLED=true
set SLACK_PUBLISH_ENABLED=true
python -u compliance_main.py
```

---

## 알림 상태 관리

SQLite `alerts.status` 값:
- `new` — 이번 run에서 처음 발견
- `updated` — 기존 알림인데 내용 변경 있음
- `ongoing` — 이전 run에서도 있었고 변경 없음
- `closed` — 수동으로 종료 처리 (자동 전환 없음)
- `corrected` — 오탐 처리 완료

```sql
-- 알림을 closed로 수동 처리
UPDATE alerts SET status='closed' WHERE alert_id='xxx';

-- 오탐 처리
UPDATE alerts SET status='corrected' WHERE fingerprint='xxx';
```

---

## 소스 확인

```sql
-- 소스별 최근 24시간 수집 현황
SELECT source_id, COUNT(*) as count, MAX(last_seen) as latest
FROM alerts
WHERE datetime(first_seen) >= datetime('now', '-24 hours')
GROUP BY source_id;

-- 연속 실패 소스 (3회 이상)
SELECT source_id, last_status, consecutive_failures, error_msg
FROM source_health
WHERE last_status IN ('partial', 'failed')
   OR consecutive_failures >= 3;
```

---

## 픽스처 업데이트

드라이런 테스트에 사용되는 픽스처는 `tests/compliance/fixtures/` 에 있습니다.
실제 API 응답과 다를 경우 픽스처를 업데이트하세요:

```bash
# 실제 응답 캡처 후 픽스처로 저장
set COMPLIANCE_DRY_RUN=false
python -c "
from compliance_briefing.config import ComplianceConfig
from compliance_briefing.collectors.brave_search import BraveSearchCollector
import json
cfg = ComplianceConfig()
c = BraveSearchCollector(cfg)
result = c.collect()
print("status:", result.status, "error:", result.error_msg)
print(json.dumps(result.items[:2], ensure_ascii=False, indent=2))
"
```

---

## 작업 스케줄러 관리

```powershell
# 상태 확인
Get-ScheduledTaskInfo -TaskName "Japan_Compliance_Briefing"

# 즉시 실행
Start-ScheduledTask -TaskName "Japan_Compliance_Briefing"

# 일시 중지
Disable-ScheduledTask -TaskName "Japan_Compliance_Briefing"

# 재활성화
Enable-ScheduledTask -TaskName "Japan_Compliance_Briefing"

# 삭제
Unregister-ScheduledTask -TaskName "Japan_Compliance_Briefing" -Confirm:$false
```
