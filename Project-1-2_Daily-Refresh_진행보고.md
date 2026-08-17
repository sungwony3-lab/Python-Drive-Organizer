# Python Drive Organizer — Project 1-2 Daily Refresh 진행보고

## 1. 구현 상태

- 기능: Google Drive Index Daily Refresh
- 상태: **IMPLEMENTED / MANUAL TASK RUN VERIFIED**
- 구현·검증일: 2026-08-14
- 예약 실행: 매일 오전 08:00 KST
- 다음 자연 예약 실행: 2026-08-15 08:00 KST
- Task 이름: `Python Drive Organizer Daily Refresh`
- 로그: `logs/daily_refresh.log`

Drive metadata sync, filename Parser, File Grouping을 하나의 순차 실행 단위로 구성하고 별도 Windows 예약 작업으로 등록했다. 진입점 직접 실행과 Task Scheduler 수동 Run을 모두 검증했다.

첫 자연 예약 실행 시각은 아직 도래하지 않았으므로 다음날 `Get-ScheduledTaskInfo`와 API `/status`로 확인해야 한다. 등록·수동 Run·중복 방지·API 동시 접근 검증은 완료했다.

## 2. 생성·수정 파일

### 생성

- `daily_refresh.py`
  - Daily Refresh 단일 진입점
  - 순차 실패 차단
  - scan 상태 기록
  - UTF-8 운영 로그
  - secret 형태 redaction
- `test_daily_refresh.py`
  - 실행 순서, 실패 차단, scan 실패 상태, 로그 redaction 테스트
- `Project-1-2_Daily-Refresh_진행보고.md`

### 수정

- `README.md`
  - 자동 갱신 구조, 수동 실행, 로그 및 예약 작업 확인 방법 추가

### 변경하지 않음

- `main.py` 기존 CLI 흐름
- `api_server.py`
- FastAPI 자동 시작 작업 `Python Drive Organizer API`
- GPT Actions schema와 Instructions
- Cloudflare Tunnel 구성
- Google Drive OAuth scope

## 3. 기존 명령 코드 흐름 확인

### `python main.py`

현재 코드 흐름:

1. 기존 파일 중 Parser version이 오래된 행 backfill
2. `scan_state` RUNNING 생성
3. Google Drive metadata 전체 조회
4. INSERT/UPDATE 파일에 `parse_filename()` 결과 함께 저장
5. 전체 조회가 끝난 경우에만 unseen 파일·폴더 DB 행 삭제
6. scan 통계와 함께 COMPLETED 기록

따라서 기본 scan은 기존 stale Parser 행과 신규·변경 파일의 Parser 결과를 최신화한다. File Grouping은 실행하지 않는다.

### `python main.py --parse-only`

- Google Drive API 호출 없음
- 현재 SQLite files 중 Parser version이 오래된 행만 backfill
- Grouping 실행 없음

### `python main.py --group-only`

- Google Drive API 및 OAuth 호출 없음
- 최신 Parser 결과를 전제로 `file_groups`, `file_group_members` 재구성
- SQLite transaction 안에서 파생 grouping 데이터 갱신

Daily Refresh에서는 기본 `main.py`를 subprocess로 연속 호출하지 않고 기존 함수들을 재사용해 정확한 순서와 실패 상태를 제어한다.

## 4. 자동 갱신 구조

```text
Task Scheduler
→ daily_refresh.py
→ scan_state RUNNING
→ Drive metadata sync
→ filename Parser backfill
→ File Grouping rebuild
→ scan_state COMPLETED
```

성공 순서:

```text
Drive sync 성공
→ Parser 성공
→ Grouping 성공
→ COMPLETED
```

실패 순서:

```text
어느 단계든 실패
→ 이후 단계 중단
→ 가능한 경우 scan_state FAILED
→ 안전한 오류 로그
→ exit code 1
```

Parser 또는 Grouping이 실패하면 해당 scan을 COMPLETED로 표시하지 않는다.

## 5. 실행 명령

수동 실행 및 Task action 공통 진입점:

```powershell
C:\Users\HLB\Documents\Python-Drive-Organizer\.venv\Scripts\python.exe daily_refresh.py
```

Working directory:

```text
C:\Users\HLB\Documents\Python-Drive-Organizer
```

PowerShell activation script는 필요하지 않다.

## 6. Windows Task Scheduler 설정

| 항목 | 설정 |
|---|---|
| Task name | `Python Drive Organizer Daily Refresh` |
| Trigger | Daily |
| 실행 시각 | 오전 08:00 Windows local time (KST) |
| 첫 자연 실행 | 2026-08-15 08:00 KST |
| Days interval | 1 |
| Program | `.venv\Scripts\python.exe` 절대 경로 |
| Arguments | `daily_refresh.py` |
| Start in | 프로젝트 루트 절대 경로 |
| StartWhenAvailable | `True` |
| MultipleInstances | `IgnoreNew` |
| ExecutionTimeLimit | `PT0S` |
| Logon type | Interactive |

PC가 오전 08:00에 꺼져 있거나 사용 불가능하면 Task Scheduler가 다음 사용 가능 시점에 누락 실행을 한 번 시작한다. 이전 Daily Refresh가 계속 실행 중이면 새 인스턴스를 만들지 않는다.

## 7. 실행 로그

로그 파일:

```text
logs/daily_refresh.log
```

기록 항목:

- KST 시작 시각
- `scan_id`
- Drive sync 단계 시작
- files/folders seen
- inserted/updated/skipped/deleted
- Parser version과 rows updated
- Grouping files/groups/members
- COMPLETED 또는 FAILED
- 종료 시각과 elapsed seconds

로그는 UTF-8 append 방식이다. traceback 전체나 인증 header를 기록하지 않으며 알려진 API key/token 형태를 `[REDACTED]`로 치환한다.

## 8. 실패 및 데이터 안전성

- Drive 전체 page 조회가 완료되기 전에 오류가 발생하면 `index_drive()`의 DB 반영·missing item 삭제 단계로 진입하지 않음
- Drive sync 실패 시 Parser와 Grouping을 실행하지 않음
- Parser 실패 시 Grouping을 실행하지 않음
- Grouping 실패 시 scan을 COMPLETED로 표시하지 않음
- 실패 시 무한 재시작 없음
- 다음 정규 schedule 또는 수동 Run 전까지 실패 로그와 FAILED scan 상태 유지

## 9. 수동 진입점 검증 결과

실행 시각: 2026-08-14 09:58 KST

```text
scan_id = SCAN-20260814-095846
status = COMPLETED
elapsed_seconds = 22.478
```

Drive sync:

| 구분 | seen | inserted | updated | skipped | deleted |
|---|---:|---:|---:|---:|---:|
| Files | 7,844 | 35 | 4 | 7,805 | 115 |
| Folders | 1,143 | 2 | 0 | 1,141 | 0 |

후속 단계:

```text
Parser version = MVP05-PARSER-1
Parser rows updated = 0
Grouping files = 7844
Grouping groups = 7831
Grouping members = 7844
```

검증:

- 새 scan_id 생성: 통과
- latest scan status COMPLETED: 통과
- Parser stale rows 0: 통과
- Grouping members와 files 수 일치: 통과
- SQLite integrity check: `ok`
- foreign key violations: 0
- 공개 `/status`에 동일 scan_id 표시: 통과
- 로그 actual secret 포함: 0

## 10. FastAPI 동시 접근 검증

첫 수동 Daily Refresh가 실행되는 동안 5회 반복 조회했다.

| Endpoint | 5회 결과 |
|---|---|
| localhost `/health` | 전부 HTTP 200 |
| localhost `/status` 정상 Bearer | 전부 HTTP 200 |
| HTTPS `/health` | 전부 HTTP 200 |
| HTTPS `/status` 정상 Bearer | 전부 HTTP 200 |

FastAPI는 별도 read-only SQLite connection을 사용하며 Daily Refresh의 짧은 SQLite transaction 중에도 정상 응답했다. 별도 lock manager, retry loop 또는 journal mode 변경은 필요하지 않았다.

## 11. Task Scheduler 수동 Run 결과

Task 수동 실행 시각: 2026-08-14 10:01 KST

```text
LastRunTime = 2026-08-14 10:01:26 KST
LastTaskResult = 0
NextRunTime = 2026-08-15 08:00:00 KST
scan_id = SCAN-20260814-100125
status = COMPLETED
elapsed_seconds = 17.488
```

Drive sync:

| 구분 | seen | inserted | updated | skipped | deleted |
|---|---:|---:|---:|---:|---:|
| Files | 7,844 | 0 | 0 | 7,844 | 0 |
| Folders | 1,143 | 0 | 0 | 1,143 | 0 |

후속 단계:

```text
Parser rows updated = 0
Grouping files = 7844
Grouping groups = 7831
Grouping members = 7844
```

최종 확인:

- Task state: `Ready`
- 종료 후 Daily Refresh Python process: 0
- 공개 `/status` no-key: HTTP 401
- 공개 `/status` valid Bearer: HTTP 200
- API latest scan: `SCAN-20260814-100125 / COMPLETED`
- DB latest scan: `SCAN-20260814-100125 / COMPLETED`
- SQLite integrity: `ok`
- foreign key violations: 0

## 12. 중복 실행 방지 검증

첫 수동 Task Run이 `Running`인 상태에서 `Start-ScheduledTask`를 다시 호출했다.

- `MultipleInstances=IgnoreNew` 확인
- 두 번째 호출 전후 Daily Refresh Python PID 집합 동일
- Windows venv launcher와 실제 interpreter의 부모·자식 PID 쌍만 존재
- 추가 Daily Refresh 인스턴스 생성 없음
- 로그 start/completed record는 직접 수동 실행 1회 + Task 실행 1회로 각각 2건

SQLite에 두 개의 refresh pipeline이 동시에 쓰지 않음을 확인했다.

## 13. 기존 API 영향 없음

- 기존 Task `Python Drive Organizer API`: 변경 없음
- API 상태: 계속 `Running`
- Uvicorn bind: 계속 `127.0.0.1:8000`
- Cloudflare public hostname: 변경 없음
- GPT Actions operation/schema: 변경 없음
- Daily Refresh 중 API local/HTTPS 응답 정상
- API read-only mode 유지

## 14. Google Drive read-only 유지

유지한 OAuth scope:

```text
https://www.googleapis.com/auth/drive.metadata.readonly
```

실행하지 않는 Drive 작업:

- create
- update
- rename
- move
- copy
- trash
- delete

`inserted`, `updated`, `deleted` 통계는 SQLite 인덱스 행 변화이며 Google Drive 파일을 변경했다는 뜻이 아니다.

## 15. Secret 비포함 확인

다음 값은 코드, 보고서 및 `logs/daily_refresh.log`에 기록하지 않았다.

- 실제 `PDO_API_KEY`
- OAuth access token
- OAuth refresh token
- OAuth client secret
- Cloudflare Tunnel token
- `credentials.json` 내용
- `token.json` 내용

실제 `.env` 및 `token.json` 값과 로그를 메모리에서 비교한 결과 secret 일치 0건이었다. `logs/`는 계속 Git ignore 대상이다.

## 16. 자동 테스트

Daily Refresh 테스트 6개:

- 성공 시 Drive sync → Parser → Grouping → COMPLETED 순서
- Drive 실패 시 Parser/Grouping 차단
- Parser 실패 시 Grouping 차단 및 FAILED
- Grouping 실패 시 COMPLETED 금지 및 FAILED
- secret 형태 로그 redaction
- UTF-8 로그 파일 기록

전체 결과:

```text
Daily Refresh 6 + 기존 51 = 총 57 tests
전체 통과
```

추가 검사:

- Python compile: 통과
- `pip check`: 통과
- `git diff --check`: 통과

## 17. 다음날 오전 08:00 확인 방법

첫 자연 예약 실행 후 PowerShell:

```powershell
Get-ScheduledTaskInfo -TaskName "Python Drive Organizer Daily Refresh"
Get-Content .\logs\daily_refresh.log -Tail 20
```

확인 항목:

- `LastRunTime`이 당일 오전 08:00 이후인지
- `LastTaskResult=0`인지
- 로그에 새 `scan_id`와 `Daily Refresh COMPLETED`가 있는지
- `/status`의 `latest_scan_id`가 같은지
- `latest_scan_status=COMPLETED`인지

공개 API 확인:

```text
GET https://drive-api.sungwony.pe.kr/status
Authorization: Bearer <configured-api-key>
```

실제 key 값은 명령 기록이나 보고서에 넣지 않는다.
