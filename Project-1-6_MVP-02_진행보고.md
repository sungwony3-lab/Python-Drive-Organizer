# Project 1-6 — Contact Directory MVP-02 진행보고

## 1. 완료 상태

- 단계: Project 1-6 — Contact Directory
- 챕터: MVP-02 Google Sheets → SQLite 동기화 구현
- 상태: **완료**
- 완료일: 2026-08-17 (Asia/Seoul)

이번 MVP에서는 Google Drive의 `이메일` 폴더에서 확정한 Google Spreadsheet `주소록`을 source of truth로 사용하고, Google Sheets API 읽기 전용 OAuth로 데이터를 읽어 기존 SQLite 데이터베이스의 연락처 current-state로 동기화했다.

FastAPI Contacts endpoint, GPT Action/Instructions, 이메일 발송 연동은 이번 범위에 포함하지 않았다.

## 2. 생성·수정 파일

### 생성

- `contacts_sheet_client.py`: Google Sheets 전용 읽기 클라이언트와 OAuth 처리
- `contacts_sync.py`: Sheet 검증, 정규화, 충돌 판정, SQLite 동기화 실행 진입점
- `test_contacts_sync.py`: 연락처 동기화 및 안전성 테스트
- `Project-1-6_MVP-02_진행보고.md`: 본 완료 보고서

### 수정

- `database.py`: 연락처 및 동기화 상태 테이블 스키마 추가
- `.gitignore`: `contacts_sheet_token.json` 제외 규칙 추가
- `.env`: 고정 Spreadsheet ID와 `PDO_CONTACTS_SHEET_NAME=주소록` 설정

`.env`의 실제 Spreadsheet ID와 모든 secret 값은 본 보고서에 기록하지 않았다.

## 3. Google Sheets 연결 구조

- Spreadsheet 선택: 사전에 확정한 고정 Spreadsheet ID 사용
- 대상 탭: 정확히 `주소록`
- 첫 번째 탭 자동 fallback: 사용하지 않음
- 값 범위: `'주소록'!A2:E`
- 반환 형식: `FORMATTED_VALUE`
- 실행 명령: `python contacts_sync.py`

동기화 전에 Spreadsheet metadata를 먼저 읽어 대상 Spreadsheet와 `주소록` 탭의 존재 및 `sheetId`를 확인한다. 대상 탭이 없으면 `CONTACTS_TAB_NOT_FOUND`로 실패하며 다른 탭을 임의로 읽지 않는다.

## 4. OAuth

- Scope: `https://www.googleapis.com/auth/spreadsheets.readonly`
- 전용 token 파일: `contacts_sheet_token.json`
- OAuth client 설정: 기존 `credentials.json` 재사용
- 기존 Drive/Gmail/Drive Share token 및 scope: 변경 없음

`contacts_sheet_token.json`은 `.gitignore` 대상이며 Git 추적 파일이 아님을 확인했다. Token 값은 콘솔, 테스트 결과, 보고서에 출력하지 않았다.

## 5. Sheet 검증 결과

- 고정 Spreadsheet 접근: 성공
- 정확한 `주소록` 탭 확인: 성공
- 확인된 헤더 순서: `소속 / 성명 / 직급 / 이메일 / 전화번호`
- 헤더 정확 일치: 성공
- 전체 행 읽기: 성공
- 데이터 행: 16개
- 빈 행 제외: 적용
- Google Sheet 변경: 0건

초기 실제 검증 중 Sheets API 비활성화와 기본 탭 이름 `시트1`을 확인했다. 사용자가 Sheets API를 활성화하고 탭을 `주소록`으로 변경한 뒤, 동일한 읽기 전용 절차로 최종 검증에 성공했다.

## 6. 정규화와 유효성 정책

- 모든 셀: 문자열 변환 후 앞뒤 공백 제거
- 빈 행: 5개 필드가 모두 비어 있으면 제외
- 성명·소속·직급: NFKC, 연속 공백 축약, casefold 정규화
- 이메일: trim 및 case-insensitive 정규화 후 기존 발송용 이메일 검증 함수 재사용
- 전화번호: `TEXT`로 보관하여 앞자리 `0` 유지
- 성명 누락: 저장하지 않고 issue 기록
- 이메일 누락/형식 오류: 연락처 행은 보존하되 `email_usable=false`
- 중복 이메일: 관련 행을 모두 보존하고 `DUPLICATE_EMAIL`, `email_usable=false`
- 완전히 동일한 중복 행: 관련 행을 모두 보존하고 `DUPLICATE_ROW`, `email_usable=false`
- 동명이인: 이메일이 다르면 별도 연락처로 유지

Issue 메시지와 운영 로그에는 이름, 이메일, 전화번호 원문을 넣지 않는다.

## 7. SQLite 구조

DB 위치: `data/drive_index.db`

### `contacts`

주요 필드:

- 식별: `contact_id`
- 원본 값: `organization`, `name`, `title`, `email`, `phone`
- 정규화 값: `normalized_organization`, `normalized_name`, `normalized_title`, `normalized_email`
- 발송 안전성: `email_usable`, `conflict_code`
- 원본 추적: `source_spreadsheet_id`, `source_sheet_id`, `source_sheet_name`, `source_row`, `source_fingerprint`
- 동기화 추적: `last_seen_sync_id`, `synced_at`, `created_at`, `updated_at`

인덱스는 정규화 이름·이메일, 소속, 직급, 최근 동기화 ID에 구성했다. Source 좌표의 중복도 제약한다.

### `contacts_sync_state`

- 식별/상태: `sync_id`, `status`
- 시간: `started_at`, `finished_at`, `created_at`
- 원본: `source_spreadsheet_id`, `source_sheet_id`, `source_sheet_name`
- 통계: `rows_seen`, `valid_rows`, `inserted`, `updated`, `deleted`, `unchanged`, `invalid`, `conflicts`
- 오류 코드: `message`

상태는 `RUNNING`, `COMPLETED`, `COMPLETED_WITH_WARNINGS`, `FAILED`를 지원한다.

### `contacts_sync_issues`

- `sync_id`, `source_row`, `contact_id`
- `issue_code`, `severity`, `message`, `created_at`

개인정보 원문 없이 행 번호와 오류 코드 중심으로 문제를 기록한다.

## 8. contact_id 및 current-state 정책

- ID 형식: `CONTACT-<UUID>`
- 이름이나 Sheet 행 번호를 ID로 사용하지 않음
- Snapshot과 기존 DB에서 각각 유일하고 유효한 `normalized_email`이 일치하면 기존 ID 유지
- 이메일을 사용할 수 없는 경우 정규화된 전체 내용 fingerprint가 유일하게 일치할 때만 기존 ID 유지
- 행 정렬·이동 시 이메일이 같으면 ID를 유지하고 `source_row`만 갱신
- 이메일 변경 시 잘못된 신원 재사용을 막기 위해 새 ID 발급
- 모호한 일치는 자동 병합하지 않고 새 ID 발급

Sheet는 current-state의 source of truth다. 완전한 metadata 조회, 탭 확인, 헤더 검증, 전체 행 읽기와 staging 검증이 모두 성공한 경우에만 하나의 SQLite transaction에서 현재 상태를 반영하고 사라진 연락처를 삭제한다.

조회나 검증이 실패하면 기존 `contacts`는 보존한다. Transaction 실패 시 전체 연락처 변경을 rollback한다.

## 9. 실제 동기화 결과

### 1차 성공 실행

- 상태: `COMPLETED`
- `rows_seen`: 16
- `valid_rows`: 16
- `inserted`: 16
- `updated`: 0
- `deleted`: 0
- `unchanged`: 0
- `invalid`: 0
- `conflicts`: 0

### 동일 Sheet 2차 연속 실행

- 상태: `COMPLETED`
- `rows_seen`: 16
- `valid_rows`: 16
- `inserted`: 0
- `updated`: 0
- `deleted`: 0
- `unchanged`: 16
- `invalid`: 0
- `conflicts`: 0

### 안정성 확인

- 최종 `contacts` 수: 16
- 2회 실행 전후 행 수: 16 → 16
- 2회 실행 전후 contact ID 집합 해시: 동일
- 중복 생성: 0건
- 실제 snapshot issue: 0건

따라서 동일 원본의 연속 실행에서 연락처 ID와 행 수가 안정적으로 유지됨을 확인했다.

## 10. 테스트 결과

연락처 전용 테스트에는 다음 항목을 포함했다.

- 정확한 헤더 성공 및 불일치 실패
- 명시된 탭만 사용하고 첫 탭 fallback 금지
- metadata 확인 후 제한된 범위 읽기
- 빈 행 무시
- 한글·공백·casefold 정규화
- 이메일 대소문자 비구분
- 전화번호 앞자리 `0` 보존
- 행 이동 후 contact ID 유지
- 이메일 변경 시 새 identity
- 중복 이메일 및 중복 행 conflict
- 동명이인 허용
- 이메일 누락 및 invalid 이메일 보존·발송 불가 처리
- 완전 snapshot에서 삭제
- 실패 snapshot에서 기존 연락처 보존
- transaction rollback
- sync 통계 및 PII 비포함 issue/log
- 다른 working directory에서도 동일한 절대 DB/token 경로 사용
- Sheets API 비활성화 오류의 안전한 분류

최종 검증:

- 전체 unit test: **137개 통과**
- Python 문법 검사: **통과**
- 기존 Drive indexing, Daily Refresh, Parser, Grouping, Search, FastAPI, GPT Actions, Enhanced Email 회귀 테스트: **통과**

테스트 실행 명령:

```powershell
python -m unittest discover -v
python -m py_compile contacts_sheet_client.py contacts_sync.py database.py test_contacts_sync.py
```

## 11. 쓰기·보안 경계 확인

- Google Sheets API: 읽기 전용 scope만 사용
- Google Sheet write: 0건
- Google Drive 파일·폴더 write: 0건
- Gmail send: 0건
- Drive permission create/update/delete: 0건
- 허용된 변경: 로컬 SQLite의 연락처 current-state와 sync 상태 기록만 수행
- Spreadsheet ID, OAuth token, API key, 연락처 원문: 보고서에 미포함
- `.env`, token, `data/` DB: Git 제외 확인

## 12. 현재 제한사항과 다음 단계 경계

이번 MVP는 수동 실행하는 Google Sheets → SQLite 동기화 기반까지만 완료했다.

아직 구현하지 않은 항목:

- Contacts FastAPI 조회 endpoint
- GPT Action schema와 GPT Instructions의 연락처 검색 기능
- 자연어 이름 검색 및 후보 선택
- 연락처를 이메일 To/CC로 연결하는 기능
- Daily Refresh와 연락처 동기화의 자동 통합
- Google Sheet 편집 기능

후속 MVP에서는 현재 `contacts` 테이블을 읽기 전용으로 노출할 수 있으나, 이름 검색 결과가 여러 개면 임의 선택하지 않고 사용자에게 후보를 제시해야 한다. `email_usable=false` 또는 conflict가 있는 연락처는 자동 발송 대상으로 사용하지 않아야 한다.
