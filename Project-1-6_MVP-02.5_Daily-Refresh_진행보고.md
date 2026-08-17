# Project 1-6 — Contact Directory MVP-02.5 진행보고

## 1. 완료 상태

- 단계: Project 1-6 — Contact Directory
- 챕터: MVP-02.5 Daily Refresh 자동 통합
- 상태: **완료**
- 완료일: 2026-08-17 (Asia/Seoul)

기존 `daily_refresh.py` 한 번의 실행으로 Drive pipeline과 Contacts pipeline이 각각 독립적으로 실행되도록 통합했다. 기존 Windows 예약 작업은 변경하거나 추가하지 않았으며, 매일 오전 08:00에 동일한 `daily_refresh.py`를 실행하는 구조를 유지했다.

## 2. 생성·수정 파일

### 수정

- `daily_refresh.py`
  - Drive/Contacts 독립 pipeline 구성
  - pipeline별 시작·완료·실패 로그
  - 통합 종료 코드 및 요약 출력
- `contacts_sync.py`
  - CLI와 Daily Refresh가 공유하는 실행 결과 함수 추가
  - Contacts sync 전용 비차단 application lock 추가
- `test_daily_refresh.py`
  - 독립 실행, 실패 격리, 종료 코드, warning, OAuth 오류, 개인정보 로그, 경로 테스트 확장
- `test_contacts_sync.py`
  - 중복 Contacts 실행 잠금과 선행 차단 테스트 추가

### 생성

- `Project-1-6_MVP-02.5_Daily-Refresh_진행보고.md`

FastAPI, GPT schema, GPT Instructions, 이메일 발송 로직은 수정하지 않았다.

## 3. 최종 실행 구조

```text
daily_refresh.py
├─ DRIVE_PIPELINE_START
│  └─ run_drive_pipeline()
│     ├─ Drive metadata sync
│     ├─ filename Parser
│     └─ File Grouping
└─ CONTACTS_PIPELINE_START
   └─ run_contacts_pipeline()
      └─ contacts_sync.execute_contacts_sync()
         ├─ Google Sheets read-only 조회
         ├─ snapshot 검증 및 reconciliation
         └─ SQLite contacts current-state 반영
```

두 pipeline은 `run_daily_refresh()` 안의 별도 `try/except` 경계에서 실행된다. Drive가 실패해도 Contacts를 실행하며, Contacts가 실패해도 이미 완료된 Drive 결과를 되돌리지 않는다.

## 4. 기존 Drive 실패 체인 유지

Drive pipeline 내부 순서는 변경하지 않았다.

1. Drive metadata sync
2. filename Parser
3. File Grouping

다음 기존 정책도 유지된다.

- Drive metadata sync 실패 → Parser 및 Grouping 실행 안 함
- Parser 실패 → Grouping 실행 안 함
- Grouping까지 성공한 경우에만 Drive `scan_state=COMPLETED`
- Drive 실패만 Drive `scan_state=FAILED`로 기록

Contacts 실패는 Drive `scan_state`를 변경하지 않는다.

## 5. Contacts 코드 재사용

Contacts 동기화 로직을 `daily_refresh.py`에 복사하지 않았다.

- 공통 결과 함수: `execute_contacts_sync()`
- 기존 수동 CLI 호환 함수: `run_contacts_sync()`
- 수동 실행: `python contacts_sync.py`
- 통합 실행: `python daily_refresh.py`

공통 실행 결과에는 다음 정보가 포함된다.

- `exit_code`
- `sync_id`
- `status`
- `rows_seen`, `inserted`, `updated`, `deleted`, `unchanged`, `invalid`, `conflicts`
- 실패 시 안전한 `error_code`

Contacts 상태는 기존 `contacts_sync_state`에만 기록하며 별도 중복 상태 테이블을 만들지 않았다.

## 6. 실패 격리와 종료 코드

| Drive | Contacts | 전체 종료 코드 |
|---|---|---:|
| COMPLETED | COMPLETED | 0 |
| COMPLETED | COMPLETED_WITH_WARNINGS | 0 |
| FAILED | COMPLETED | 1 |
| COMPLETED | FAILED | 1 |
| FAILED | FAILED | 1 |

`COMPLETED_WITH_WARNINGS`는 결과 요약에 그대로 표시하되 성공 종료로 취급한다. 어느 한 pipeline이라도 실패하면 Windows Task Scheduler가 확인할 수 있도록 non-zero를 반환한다.

한 pipeline의 실패는 다른 pipeline의 SQLite transaction을 rollback하지 않는다.

## 7. OAuth 동작

Contacts pipeline은 MVP-02에서 생성한 다음 전용 인증을 그대로 사용한다.

- Scope: `https://www.googleapis.com/auth/spreadsheets.readonly`
- Token 파일: `contacts_sheet_token.json`
- 기존 `credentials.json` 재사용

실제 통합 실행에서는 기존 정상 token을 사용하여 브라우저 OAuth 창이 열리지 않았다. Refresh token으로 자동 갱신할 수 없는 상황이나 사용자 상호작용이 필요한 상황에서는 Contacts pipeline만 실패하고 Drive 결과는 유지한다.

기존 Drive, Gmail, Drive Download, Drive Share token과 scope는 변경하지 않았다.

## 8. Contacts 동시 실행 잠금

`data/contacts_sync.lock`을 사용하는 Contacts 전용 OS file lock을 추가했다.

- 잠금 범위: Sheet 설정 읽기 전부터 SQLite contacts 반영 완료까지
- 잠금 방식: 비차단 exclusive lock
- 이미 실행 중인 경우: 즉시 `CONTACTS_SYNC_ALREADY_RUNNING`
- 자동 대기 및 무한 재시도: 없음
- 잠금 충돌 시: 설정, DB, OAuth/Sheets client 접근 전에 중단
- 프로세스 종료 시: OS가 잠금을 해제

따라서 오전 08:00 Daily Refresh와 사용자의 `contacts_sync.py` 수동 실행이 겹쳐도 두 프로세스가 동시에 Contacts current-state를 갱신하지 않는다.

## 9. 로그

기존 `logs/daily_refresh.log`를 사용한다.

구현된 주요 marker:

- `DAILY_REFRESH_START`
- `DRIVE_PIPELINE_START`
- `DRIVE_PIPELINE_COMPLETED` / `DRIVE_PIPELINE_FAILED`
- `CONTACTS_PIPELINE_START`
- `CONTACTS_PIPELINE_COMPLETED` / `CONTACTS_PIPELINE_FAILED`
- `DAILY_REFRESH_COMPLETED` / `DAILY_REFRESH_COMPLETED_WITH_ERRORS`

종료 시 다음 요약을 출력한다.

```text
Daily Refresh Summary
Drive: COMPLETED / FAILED
Contacts: COMPLETED / COMPLETED_WITH_WARNINGS / FAILED
Exit: 0 / 1
```

Contacts 성공 로그에는 sync ID, 상태와 개수 통계만 기록한다. 예상하지 못한 Contacts 예외 메시지는 원문을 기록하지 않고 `CONTACTS_UNEXPECTED_ERROR`로 대체한다.

실제 예약 실행의 최신 구간을 검사한 결과:

- 필수 Drive/Contacts/Daily 완료 marker: 모두 존재
- 이메일 주소 형태: 검출 0건
- 휴대전화 번호 형태: 검출 0건

## 10. 실제 직접 실행 결과

실행 명령:

```powershell
.\.venv\Scripts\python.exe .\daily_refresh.py
```

결과:

- 전체 종료 코드: `0`
- Drive: `COMPLETED`
- Contacts: `COMPLETED`
- Drive files seen: 8,812
- Drive folders seen: 2,394
- Contacts rows seen: 16
- Contacts inserted: 0
- Contacts updated: 0
- Contacts deleted: 0
- Contacts unchanged: 16
- Contacts invalid: 0
- Contacts conflicts: 0

Drive metadata → Parser → Grouping 이후 Contacts Sheet sync가 순서대로 실행됐고, 두 subsystem 모두 각자의 상태 테이블에 성공 결과를 기록했다.

## 11. Windows Task Scheduler 확인

기존 작업을 읽기 전용으로 확인했으며 설정 변경이나 새 작업 생성은 하지 않았다.

| 항목 | 확인 결과 |
|---|---|
| Task name | `Python Drive Organizer Daily Refresh` |
| 상태 | Ready |
| 실행 파일 | 프로젝트 `.venv\Scripts\python.exe` |
| Arguments | `daily_refresh.py` |
| Working directory | 프로젝트 루트 |
| Trigger | 매일 Windows local time 08:00 |
| Enabled | True |
| StartWhenAvailable | True |
| MultipleInstances | IgnoreNew |

확인 시점의 다음 예약 실행은 2026-08-18 오전 08:00이었다.

### 실제 예약 작업 수동 시작 결과

기존 작업을 `Start-ScheduledTask`로 한 번 수동 시작했다.

- 작업 시작 상태: Running
- 작업 완료 상태: Ready
- `LastTaskResult`: 0
- 최신 Drive `scan_state`: COMPLETED
- 최신 Contacts `contacts_sync_state`: COMPLETED
- Contacts count: 16
- Contacts 결과: inserted 0 / updated 0 / deleted 0 / unchanged 16

Task Scheduler의 실제 실행 파일, 인수, working directory를 통해서도 통합 갱신이 정상 완료됨을 확인했다.

## 12. 자동 테스트

추가·보강한 주요 테스트:

- Drive와 Contacts 모두 성공 → exit 0
- Drive 실패 → Parser/Grouping 중단, Contacts 계속 실행
- Parser/Grouping 실패 → Drive 완료 금지, Contacts 계속 실행
- Contacts 실패 → Drive 성공 결과 유지, exit 1
- Contacts warning → 요약에 warning 표시, exit 0
- Contacts OAuth 실패 → Drive 성공 결과 유지
- Contacts 예상 밖 예외의 개인정보 로그 차단
- Contacts lock 중복 획득 즉시 실패
- 잠금 충돌 시 설정/DB/OAuth 접근 전 중단
- 다른 working directory에서 절대 경로 유지
- 기존 Task Scheduler 명령 `daily_refresh.py` 호환
- 기존 Drive 내부 실행 순서 유지

최종 결과:

- 전체 unit test: **144개 통과**
- Python 문법 검사: **통과**
- 기존 Drive indexing, Parser, Grouping, Search, FastAPI, GPT Actions, Enhanced Email, Contacts Sync 회귀: **통과**

## 13. 보안 및 쓰기 경계

이번 MVP에서 실행한 외부 서비스 작업:

- Google Drive metadata read
- Google Sheets read

로컬 작업:

- SQLite Drive index 갱신
- SQLite contacts current-state 및 sync 상태 갱신
- UTF-8 Daily Refresh 로그 기록

수행하지 않은 작업:

- Google Sheet write: 0건
- Gmail send: 0건
- Drive permission create/update/delete: 0건
- Drive 파일 생성·수정·이동·복사·삭제·휴지통 이동: 0건
- GPT schema/Instructions 변경: 0건
- 새 Windows 예약 작업 생성: 0건
- 기존 예약 작업 설정 변경: 0건

Spreadsheet ID, OAuth token, API key, Cloudflare token, 연락처 이름·이메일·전화번호는 본 보고서에 포함하지 않았다.

## 14. 완료 기준 확인

- [x] `daily_refresh.py` 한 번 실행으로 Drive와 Contacts 모두 갱신
- [x] 한 pipeline 실패 시에도 다른 pipeline 실행
- [x] 기존 오전 08:00 Scheduled Task 하나만 유지
- [x] `contacts_sync.py` 단독 수동 실행 유지
- [x] Google Sheets read-only 유지
- [x] Contacts 동시 실행 방지
- [x] 실제 직접 통합 실행 성공
- [x] 실제 Task Scheduler 실행 성공
- [x] 전체 144개 회귀 테스트 통과

Project 1-6 MVP-02.5 Daily Refresh 자동 통합 완료 조건을 모두 충족했다.
