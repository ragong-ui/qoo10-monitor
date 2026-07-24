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
  db.py                     ← SQLite (compliance_briefing.db)
  collector_base.py         ← 추상 기반 클래스 (retry, dry-run)
  collectors/               ← 소스별 수집기
    brave_search.py
    egov.py / caa.py / nite.py
    safety_korea.py
    gdelt.py
  dedup.py                  ← SHA-256 지문 + n-gram 클러스터링
  scoring.py                ← 중요도(critical/high/medium/low) 규칙
  llm.py                    ← Anthropic/OpenAI 요약 (optional)
  formatters.py             ← Slack Block Kit, 이메일 포맷
  pipeline.py               ← 수집 → 중복제거 → 요약 → DB → 알림
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
LLM_PROVIDER=anthropic   # or openai or disabled
ANTHROPIC_API_KEY=sk-ant-...
SLACK_BOT_TOKEN=xoxb-...
GOOGLE_SHEETS_EXPORT_ENABLED=true
COMPLIANCE_APPS_SCRIPT_URL=https://script.google.com/...
```

### 3. 드라이런 테스트

```bash
# 픽스처 데이터로 실제 API 호출 없이 파이프라인 전체 검증
python -u compliance_main.py
# 로그: logs/compliance_YYYYMMDD.log
# DB:   compliance_briefing.db
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

**권장 롤아웃 순서:**
1. `DRY_RUN=true` → 로그·DB 확인
2. `DRY_RUN=false` → 실제 수집 확인
3. `SHEETS=true` → Sheets 업로드 확인
4. `SLACK=true` → #japan-compliance 포스팅 활성화

---

## Google Sheets 대시보드 설정

`apps-script/compliance/` 디렉토리를 별도 Apps Script 프로젝트로 배포:

1. [apps-script/compliance/README.md](apps-script/compliance/README.md) 참조
2. 배포 URL을 `COMPLIANCE_APPS_SCRIPT_URL` 에 설정
3. `GOOGLE_SHEETS_EXPORT_ENABLED=true` 설정

---

## 보안 주의사항

- `.env` 파일은 Git에 커밋하지 마세요 (`.gitignore` 확인)
- 로그에 API 키·토큰이 출력되지 않습니다 (`masked_log_line()` 사용)
- Apps Script URL은 도메인 제한(`access: DOMAIN`) 권장
- `credentials.json`, `token.json` 등 서비스 계정 파일은 저장소에 포함하지 마세요

---

## 데이터베이스

SQLite 파일: `compliance_briefing.db`

주요 테이블:
- `runs` — 실행 이력 (run_id, 시작/종료, 건수)
- `alerts` — 수집된 알림 (중복제거·점수화 완료)
- `alert_history` — 알림 변경 이력 (필드별 old/new 값)
- `source_health` — 소스별 수집 성공/실패 이력

```bash
# 최근 알림 확인
sqlite3 compliance_briefing.db "SELECT severity, title_ko, status FROM alerts ORDER BY first_seen DESC LIMIT 10;"
```
