# Japan Marketplace Compliance Briefing

일본 시장 규제·리콜·경쟁사 동향을 매일 자동으로 수집·분석·공유하는 시스템.
기존 Qoo10 위조품 모니터링(`monitor.py`)과 동일 저장소에 **최소 침습적으로 통합**.

---

## 수집 소스

| 소스 | 분류 | 설명 |
|------|------|------|
| e-Gov (경제산업성) | 규제 | 법령 개정·고시 RSS |
| 消費者庁 (CAA) | 규제 | 소비자청 행정처분·조치명령 |
| NITE | 리콜 | 제품안전·제품사고 정보 |
| Safety Korea (KATS/KCA/MFDS) | 리콜 | 한국 제품 안전 리콜 (일본 수출품 포함) |
| Brave Search API | 뉴스 | 규제·리콜·경쟁사 관련 최신 기사 |
| GDELT | 뉴스 | 다언어 뉴스 모니터링 |

---

## 아키텍처

```
compliance_main.py          ← Task Scheduler 진입점
compliance_briefing/
  config.py                 ← 환경변수 로딩, feature flags
  db.py                     ← SQLite (운영/드라이런 DB 분리)
  collector_base.py         ← retry, dry-run, ok/partial/failed 결과 모델
  collectors/               ← 소스별 수집기
    brave_search.py
    egov.py / caa.py / nite.py
    nikkei.py
    safety_korea.py
    gdelt.py
  filter.py                 ← 도메인 차단 + 규제 관련성 조합 필터
  dedup.py                  ← SHA-256 지문 + n-gram 클러스터링
  scoring.py                ← 중요도(critical/high/medium/low) 규칙
  llm.py                    ← Anthropic/OpenAI 요약 (optional)
  formatters.py             ← Slack Block Kit, 이메일 포맷
  pipeline.py               ← 수집 → 필터 → 중복제거 → 요약 → DB → 알림
  slack_notifier.py         ← #japan-compliance 포스팅
  sheets_uploader.py        ← Google Sheets 스냅샷 업로드

apps-script/compliance/     ← 전용 대시보드 Apps Script
tests/compliance/           ← pytest 테스트 + 픽스처
```

---

## 초기 설정

### 1. Python 의존성 설치

```bash
pip install feedparser beautifulsoup4
# LLM 요약을 원하는 경우:
pip install anthropic   # 또는
pip install openai
```

### 2. 환경변수 설정

`.env.example`을 `.env`로 복사 후 값 입력:

```bash
# 필수
BRAVE_SEARCH_API_KEY=your_key

# 선택 (dry-run 모드에서는 불필요)
SAFETY_KOREA_API_KEY=your_key
LLM_PROVIDER=apps_script # or anthropic / openai / disabled
ANTHROPIC_API_KEY=sk-ant-...
SLACK_BOT_TOKEN=xoxb-...
GOOGLE_SHEETS_EXPORT_ENABLED=true
COMPLIANCE_APPS_SCRIPT_URL=https://script.google.com/...
COMPLIANCE_APPS_SCRIPT_TOKEN=long-random-secret
```

### 3. 드라이런 테스트

```bash
# 픽스처 데이터로 실제 API 호출 없이 파이프라인 전체 검증
python -u compliance_main.py
# 로그: logs/compliance_YYYYMMDD.log
# DB:   compliance_briefing.dryrun.db
```

### 4. 테스트 실행

```bash
cd C:\Users\ragong\qoo10-monitor
pytest tests/compliance/ -v
```

### 5. Task Scheduler 등록

```bash
# setup_compliance_scheduler.bat을 관리자 권한으로 실행
# → 매일 08:00 KST/JST에 compliance_main.py 실행
```

---

## Feature Flags (환경변수)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `COMPLIANCE_BRIEFING_ENABLED` | `true` | 전체 활성/비활성 |
| `COMPLIANCE_DRY_RUN` | `true` | 픽스처 사용 (실제 API 호출 없음) |
| `GOOGLE_SHEETS_EXPORT_ENABLED` | `false` | Google Sheets 업로드 |
| `SLACK_PUBLISH_ENABLED` | `false` | Slack 포스팅 |
| `NIKKEI_LOOKBACK_DAYS` | `7` | Nikkei 실수집 기사 최대 나이(일), 날짜 없는 기사 제외 |

### 소스별 활성화

| 변수 | 기본값 |
|------|--------|
| `COMPLIANCE_SOURCE_EGOV_ENABLED` | `true` |
| `COMPLIANCE_SOURCE_CAA_ENABLED` | `true` |
| `COMPLIANCE_SOURCE_NITE_ENABLED` | `true` |
| `COMPLIANCE_SOURCE_NIKKEI_ENABLED` | `true` |
| `COMPLIANCE_SOURCE_SAFETY_KOREA_ENABLED` | `true` |
| `COMPLIANCE_SOURCE_BRAVE_ENABLED` | `true` |
| `COMPLIANCE_SOURCE_GDELT_ENABLED` | `false` |

GDELT는 기본 비활성입니다. 활성화할 경우 `GDELT_TIME_BUDGET_SECONDS`
(기본 60초) 안에서만 새 쿼리를 시작하며, HTTP 429 응답의 `Retry-After`는
`GDELT_RETRY_AFTER_CAP_SECONDS`(기본 15초)까지만 기다립니다.

**권장 롤아웃 순서:**
1. `DRY_RUN=true` → 로그·DB 확인
2. `DRY_RUN=false` → 실제 수집 확인
3. `SHEETS=true` → Sheets 업로드 확인
4. `SLACK=true` → #japan-compliance 포스팅 활성화

---

## 대시보드 분리 운영

두 대시보드는 데이터 계약과 담당 업무가 다르므로 하나의 앱으로 합치지 않습니다.

| 시스템 | 용도 | 운영 화면 | 데이터 연결 |
|---|---|---|---|
| Qoo10 SNS 모니터링 | Google/X 위조품 게시물 검토 | `https://qoo10-monitor-kpcsgufhoixrfo6ekyxmc7.streamlit.app/` | `GOOGLE_APPS_SCRIPT_URL` |
| Japan Compliance Briefing | 일본 규제·리콜·경쟁사 동향 검토 | `https://script.google.com/macros/s/AKfycbxP0EOrSkh5PQhUlpZfSL3bbheQYmR9JWjoO0uaz_u1FkVpgBIhwSGSDrypUqOWITLw/exec` | `COMPLIANCE_APPS_SCRIPT_URL` |

운영 원칙:

- `app.py`와 Streamlit Secrets에는 Compliance URL·토큰을 넣지 않습니다.
- Compliance 화면은 `apps-script/compliance/`만 배포합니다.
- 두 시스템의 URL 환경변수와 데이터 스키마를 서로 재사용하지 않습니다.
- 두 서비스의 운영 계정은 `lany052007@gmail.com`으로 통일합니다.

### Compliance Google Sheets 설정

1. [apps-script/compliance/README.md](apps-script/compliance/README.md) 참조
2. 공식 Apps Script 프로젝트 `1dYnlS14VXQ2PPQZgS6xteVJ94FuiFEIgSFR8gwCoDUpaAdI3pqm0TLbq`만 배포
3. 운영 스프레드시트 `1rF_QXLRL7XXw34myMmXkw4wHSO7sCYXFSI9x_E74M5Q` 사용
4. 배포 URL을 `COMPLIANCE_APPS_SCRIPT_URL`에 설정
5. `GOOGLE_SHEETS_EXPORT_ENABLED=true` 설정

---

## 보안 주의사항

- `.env` 파일은 Git에 커밋하지 마세요 (`.gitignore` 확인)
- 로그에 API 키·토큰이 출력되지 않습니다 (`masked_log_line()` 사용)
- 외부 벤더가 읽을 수 있도록 화면은 공개하되 모든 쓰기 요청은 `COMPLIANCE_APPS_SCRIPT_TOKEN`으로 검증합니다.
- `credentials.json`, `token.json` 등 서비스 계정 파일은 저장소에 포함하지 마세요

---

## 데이터베이스

SQLite 파일:

- 운영 (`COMPLIANCE_DRY_RUN=false`): `compliance_briefing.db`
- 드라이런 (`COMPLIANCE_DRY_RUN=true`): `compliance_briefing.dryrun.db`

드라이런 픽스처 데이터는 운영 알림 및 실행 이력과 섞이지 않습니다.

주요 테이블:
- `runs` — 실행 이력 (run_id, 시작/종료, 건수)
- `alerts` — 수집된 알림 (중복제거·점수화 완료)
- `alert_history` — 알림 변경 이력 (필드별 old/new 값)
- `source_health` — 소스별 최근 상태(`ok`/`partial`/`failed`)와 연속 실패 이력

```bash
# 최근 알림 확인
sqlite3 compliance_briefing.db "SELECT severity, title_ko, status FROM alerts ORDER BY first_seen DESC LIMIT 10;"
```
