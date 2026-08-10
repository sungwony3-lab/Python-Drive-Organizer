# Python Drive Organizer — MVP-04 증분 동기화 진행 보고서

- 작성일: 2026-08-10 (Asia/Seoul)
- 기준 문서: `Python_Drive_Organizer_개발지침.md`, `Python_Drive_Organizer_진행보고.md`
- 이전 완료 단계: MVP-01 ~ MVP-03
- 이번 단계: **MVP-04 증분 동기화**
- 완료 상태: **구현 및 실제 Google Drive 종단 검증 완료**
- 다음 예정 단계: MVP-05 File Name Parser

---

## 1. MVP-04 목표

기존 SQLite 인덱스와 현재 Google Drive 전체 상태를 Drive ID 기준으로 비교해 다음 판정을 정확하게 수행한다.

```text
INSERT
UPDATE
SKIP
DELETE
```

핵심 안전 조건:

- Google Drive API v3 읽기 전용 유지
- OAuth scope `drive.metadata.readonly` 유지
- 전체 Drive pagination이 정상 완료된 경우에만 DELETE 감지
- 실패 시 files/folders 변경 전체 rollback
- 실패한 scan은 `FAILED`와 오류 메시지만 별도 commit
- 기존 DB를 삭제하거나 새로 만들지 않고 migration 적용

---

## 2. 생성·수정 파일

### 수정

| 파일 | MVP-04 변경 내용 |
|---|---|
| `database.py` | idempotent migration, 기존 행 메모리 로드, batch INSERT/UPDATE/SKIP, unseen DELETE, scan 통계 저장 |
| `scanner.py` | 메모리 dict 비교, NULL-safe 동등성 판정, 통계 모델, 전체 조회 후 일괄 DB 반영 |
| `drive_client.py` | `incompleteSearch` 요청 및 불완전 검색 실패 처리 |
| `main.py` | 증분 통계 출력, 새 통계 구조 연결, Drive 스캔 오류 메시지 |
| `README.md` | MVP-04 증분 동기화와 rollback/DELETE 안전 조건 설명 |

### 생성

| 파일 | 역할 |
|---|---|
| `MVP-04_진행보고.md` | 이번 MVP의 구현·검증 결과와 다음 단계 검토 정보 |

### 변경하지 않음

- `requirements.txt`: MVP-04에서 새 패키지 추가 없음
- OAuth credentials/token 형식 변경 없음
- 기존 종합 보고서는 MVP-03까지의 기준 문서로 유지

---

## 3. DB schema migration

기존 `data/drive_index.db`를 보존한 상태로 `ALTER TABLE ... ADD COLUMN` migration을 적용했다.

### files 추가 컬럼

```sql
last_seen_scan_id TEXT
```

### folders 추가 컬럼

```sql
last_seen_scan_id TEXT
```

### scan_state 추가 컬럼

```sql
files_inserted INTEGER NOT NULL DEFAULT 0
files_updated INTEGER NOT NULL DEFAULT 0
files_skipped INTEGER NOT NULL DEFAULT 0
files_deleted INTEGER NOT NULL DEFAULT 0

folders_inserted INTEGER NOT NULL DEFAULT 0
folders_updated INTEGER NOT NULL DEFAULT 0
folders_skipped INTEGER NOT NULL DEFAULT 0
folders_deleted INTEGER NOT NULL DEFAULT 0
```

Migration 동작:

1. `PRAGMA table_info(table)`로 현재 컬럼 확인
2. 없는 컬럼만 추가
3. 이미 존재하면 아무 작업도 하지 않음
4. migration 완료 후 commit

실제 DB 검증:

- migration 1회 성공
- 같은 migration 즉시 재실행 성공
- 재실행 오류 없음
- migration 전후 기존 행 수 동일
  - files: 7,914 → 7,914
  - folders: 1,141 → 1,141
  - scan_state: 2 → 2
- 기존 DB 삭제 및 재생성 없음
- migration 직후 `PRAGMA integrity_check`: `ok`

---

## 4. 필드 의미 구현

### scan_id

해당 행의 실제 비교 대상 데이터가 마지막으로 INSERT 또는 UPDATE된 scan ID다.

### last_seen_scan_id

해당 file/folder가 Drive에 존재하는 것을 마지막으로 확인한 scan ID다.

### indexed_at

실제 INSERT 또는 UPDATE를 수행한 UTC 시간이다.

### SKIP 동작

변경이 없는 행에서는 다음 SQL만 실행한다.

```sql
UPDATE files
SET last_seen_scan_id = ?
WHERE file_id = ?;
```

folders도 같은 방식이다.

SKIP 시 유지되는 값:

- 비교 대상 데이터 전체
- `scan_id`
- `indexed_at`

SKIP 시 변경되는 값:

- `last_seen_scan_id`

실제 전체 SKIP 스캔에서 현재 scan ID를 `scan_id`로 가진 files/folders가 0행이고, 모든 행의 `last_seen_scan_id`만 현재 scan으로 변경된 것을 확인했다.

---

## 5. 변경 판정 구현

### files 비교 필드

다음 10개 필드를 NULL-safe Python 동등성 비교한다.

```text
name
mime_type
extension
size_bytes
created_time
modified_time
parent_id
md5_checksum
trashed
owned_by_me
```

비교 제외:

```text
file_id
scan_id
last_seen_scan_id
indexed_at
```

### folders 비교 필드

```text
name
parent_id
```

비교 제외:

```text
folder_id
scan_id
last_seen_scan_id
indexed_at
```

### NULL 처리

DB row와 현재 Drive 레코드를 Python 값으로 정규화한 뒤 `None == None` 방식으로 비교한다. 누락된 `size`, `md5Checksum`, `parent_id`, `ownedByMe`가 정상적으로 NULL 비교되는 것을 로컬 테스트로 확인했다.

---

## 6. 성능 구조

항목마다 SQLite SELECT를 반복하지 않는다.

스캔 시작 시 필요한 기존 데이터를 한 번에 로드한다.

```text
file_id   → 기존 files row
folder_id → 기존 folders row
```

Drive 전체 pagination 결과도 현재 ID 기준 dict에 모은다.

```text
file_id   → 현재 Drive file metadata
folder_id → 현재 Drive folder metadata
```

그 후 메모리에서 INSERT / UPDATE / SKIP을 분류하고 SQLite에는 `executemany`로 batch 반영한다.

이 구조의 추가 안전 효과:

- Drive pagination 중에는 files/folders 테이블에 쓰지 않음
- 마지막 페이지까지 성공해야 DB 변경을 시작함
- 중간 페이지 오류 시 DELETE를 포함한 어떠한 files/folders 변경도 시작하지 않음
- API 응답에 같은 ID가 중복되어도 현재 dict에서 단일 ID로 정규화됨

---

## 7. DELETE 안전 조건

Drive 조회 조건:

```text
q = "trashed = false"
spaces = "drive"
pageSize = 1000
```

요청 응답 필드에 다음을 추가했다.

```text
nextPageToken
incompleteSearch
```

다음 조건을 모두 충족한 후에만 DELETE를 실행한다.

1. OAuth 성공
2. 모든 API 요청 성공
3. 모든 `nextPageToken` 페이지 처리 완료
4. 어떤 페이지에서도 `incompleteSearch = true`가 아님
5. 현재 모든 file/folder의 `last_seen_scan_id` 갱신 완료

DELETE 조건:

```sql
WHERE last_seen_scan_id IS NULL
   OR last_seen_scan_id <> :current_scan_id
```

pagination 또는 불완전 검색 오류가 발생하면 scanner가 예외를 발생시키며, main은 transaction rollback 후 scan_state만 FAILED로 기록한다.

---

## 8. 트랜잭션 구현

```text
1. schema 확인/migration
2. scan_state RUNNING INSERT
3. COMMIT
4. Drive 전체 페이지 읽기
5. 메모리에서 INSERT/UPDATE/SKIP 분류
6. batch INSERT/UPDATE/SKIP
7. unseen files/folders DELETE
8. scan_state COMPLETED와 전체 통계 UPDATE
9. files/folders 변경과 COMPLETED를 동일 transaction으로 COMMIT
```

실패 시:

```text
1. files/folders transaction ROLLBACK
2. scan_state를 FAILED로 UPDATE
3. message와 부분 files_seen/folders_seen 기록
4. COMMIT
```

로컬 실패 테스트에서 pagination 오류를 의도적으로 발생시켜 다음을 확인했다.

- 기존 files 변경 없음
- 기존 folders 변경 없음
- unseen DELETE 미실행
- 부분 변경 없음
- scan_state `FAILED`
- 오류 message 기록
- 실패 전까지 확인한 `files_seen` 기록
- INSERT/UPDATE/SKIP/DELETE 통계는 0 유지

---

## 9. 자동·로컬 테스트 결과

MVP-03 구형 schema를 메모리 SQLite에 재현해 테스트했다.

| 테스트 | 결과 |
|---|---|
| 기존 DB schema migration | 성공 |
| migration 2회 연속 실행 | 성공 |
| file INSERT 판정 | 성공 |
| file UPDATE 판정 | 성공 |
| file SKIP 판정 | 성공 |
| file DELETE 판정 | 성공 |
| folder INSERT 판정 | 성공 |
| folder UPDATE 판정 | 성공 |
| folder SKIP 판정 | 성공 |
| folder DELETE 판정 | 성공 |
| NULL 필드 비교 | 성공 |
| SKIP 시 scan_id 유지 | 성공 |
| SKIP 시 indexed_at 유지 | 성공 |
| SKIP 시 last_seen_scan_id만 변경 | 성공 |
| 실패 스캔 시 DELETE 미실행 | 성공 |
| 실패 스캔 시 변경 rollback | 성공 |
| scan_state FAILED 및 message | 성공 |
| file_id 중복 | 0 |
| folder_id 중복 | 0 |
| incompleteSearch 방어 | 성공 |
| Python 컴파일 | 성공 |
| 패키지 의존성 검사 | 성공 |

---

## 10. 실제 Google Drive 검증

프로그램은 Drive 쓰기를 수행하지 않았다. 테스트용 Drive 변경은 사용자가 Google Drive UI에서 직접 수행했고, 프로그램은 읽기 전용 API로 결과만 확인했다.

### 10.1 초기 무변경 검증

| scan_id | files | file I/U/S/D | folders | folder I/U/S/D |
|---|---:|---|---:|---|
| `SCAN-20260810-183637` | 7,914 | 0 / 0 / 7,914 / 0 | 1,141 | 0 / 0 / 1,141 / 0 |

결과:

- 전체 SKIP 성공
- 모든 files/folders의 `last_seen_scan_id`가 현재 scan으로 갱신
- `scan_id`와 `indexed_at`은 변경되지 않음

### 10.2 외부 변경으로 인한 실제 DELETE 관찰

첫 스캔과 다음 스캔 사이에 Drive 조회에서 파일 2개가 사라졌다.

| scan_id | files | file I/U/S/D |
|---|---:|---|
| `SCAN-20260810-183721` | 7,912 | 0 / 0 / 7,912 / 2 |

이후 방어 강화를 위해 `incompleteSearch`를 명시적으로 검사하도록 보완했다.

### 10.3 안정 상태 재확인

| scan_id | files | file I/U/S/D | folders | folder I/U/S/D |
|---|---:|---|---:|---|
| `SCAN-20260810-183833` | 7,912 | 0 / 0 / 7,912 / 0 | 1,141 | 0 / 0 / 1,141 / 0 |

### 10.4 사용자 파일 업로드 → INSERT

사용자가 파일 3개를 Google Drive에 업로드했다. 그중 하나는 `레이어변경.png`였다.

| scan_id | files | file I/U/S/D |
|---|---:|---|
| `SCAN-20260810-184716` | 7,915 | 3 / 0 / 7,912 / 0 |

검증:

- 3개 모두 INSERT
- `레이어변경.png` 단일 행 확인
- `scan_id = current scan`
- `last_seen_scan_id = current scan`
- `indexed_at` 현재 UTC
- file_id 중복 0

### 10.5 동일 파일 이름 변경 + 폴더 이동 → UPDATE

사용자가 `레이어변경.png`를 다음과 같이 변경했다.

```text
이름: 레이어변경.png → 레이어변경_MVP04.png
폴더: 기존 parent → 다른 parent
```

| scan_id | files | file I/U/S/D |
|---|---:|---|
| `SCAN-20260810-185403` | 7,915 | 0 / 2 / 7,913 / 0 |

대상 파일 검증:

- INSERT 때와 동일한 file_id 유지
- name 변경 확인
- parent_id 변경 확인
- `scan_id` 현재 scan으로 변경
- `last_seen_scan_id` 현재 scan으로 변경
- `indexed_at` 변경
- 중복 행 생성 없음

전체 UPDATE가 2건인 이유:

- 테스트 대상 `레이어변경_MVP04.png` 1건
- 같은 시점에 별도 파일 `정확선택 (smp).lsp`의 Drive 메타데이터 변경 1건

### 10.6 테스트 파일 휴지통 이동 → DB DELETE

사용자가 `레이어변경_MVP04.png`를 Google Drive 휴지통으로 이동했다.

| scan_id | files | file I/U/S/D |
|---|---:|---|
| `SCAN-20260810-185627` | 7,914 | 0 / 1 / 7,913 / 1 |

대상 파일 검증:

- 스캔 전 DB에 대상 file_id 존재
- `trashed = false` 전체 스캔 완료
- 스캔 후 같은 file_id 행 수 0
- `files_deleted = 1`
- file_id 중복 0

동시에 별도 파일 메타데이터 변경 1건이 있어 `files_updated = 1`로 기록됐다.

### 10.7 마지막 무변경 스캔

| scan_id | files | file I/U/S/D | folders | folder I/U/S/D |
|---|---:|---|---:|---|
| `SCAN-20260810-185707` | 7,914 | 0 / 0 / 7,914 / 0 | 1,141 | 0 / 0 / 1,141 / 0 |

요구 조건 충족:

```text
inserted = 0
updated = 0
deleted = 0
files_skipped = 전체 files 수
folders_skipped = 전체 folders 수
```

---

## 11. 최종 DB 상태

DB 위치:

```text
C:\Users\HLB\Documents\Python-Drive-Organizer\data\drive_index.db
```

| 검사 | 최종 결과 |
|---|---:|
| files 행 수 | 7,914 |
| folders 행 수 | 1,141 |
| 중복 file_id 그룹 | 0 |
| 중복 folder_id 그룹 | 0 |
| 최신 scan의 last_seen files | 7,914 |
| 최신 scan의 last_seen folders | 1,141 |
| PRAGMA integrity_check | `ok` |

DB 크기:

```text
4,091,904 bytes
```

---

## 12. PowerShell 출력

정상 완료 시 다음 형식으로 출력한다.

```text
Scan ID: SCAN-YYYYMMDD-HHMMSS
Status: COMPLETED

Files seen: ...
  inserted: ...
  updated: ...
  skipped: ...
  deleted: ...

Folders seen: ...
  inserted: ...
  updated: ...
  skipped: ...
  deleted: ...
```

실행 방법:

```powershell
python main.py
```

---

## 13. Drive 읽기 전용 검증

현재 Drive API 호출은 `service.files().list(...)`뿐이다.

코드 검색 결과:

```text
NO_DRIVE_WRITE_CALLS_FOUND
```

사용하지 않는 Drive 동작:

- create
- update
- delete
- copy
- move
- trash
- permissions 변경
- revisions 변경

사용 scope:

```text
https://www.googleapis.com/auth/drive.metadata.readonly
```

---

## 14. 이번 MVP에서 구현하지 않은 기능

- Revision Parser
- Copy Parser
- groupKey
- file_groups
- full_path
- `_완료` 폴더 처리
- Google Drive 쓰기
- Excel/CSV 보고
- GUI
- AI 기능

---

## 15. 현재 Git 상태

MVP-02~04 구현은 아직 별도 커밋으로 정리되지 않았다.

수정된 추적 파일:

```text
README.md
main.py
requirements.txt
```

미추적 구현/문서 파일:

```text
database.py
drive_client.py
scanner.py
Python_Drive_Organizer_개발지침.md
Python_Drive_Organizer_진행보고.md
MVP-04_진행보고.md
```

민감 정보와 DB는 `.gitignore` 대상이다.

```text
credentials.json
token.json
data/
.venv/
```

---

## 16. ChatGPT 검토 요청

이 문서를 기준으로 다음을 확인해 달라.

1. MVP-04 INSERT / UPDATE / SKIP / DELETE 성공 조건 충족 여부
2. migration 방식과 재실행 안전성 승인 여부
3. `scan_id`, `last_seen_scan_id`, `indexed_at` 의미 구현 승인 여부
4. 전체 pagination 성공 후에만 DELETE하는 구조 승인 여부
5. `incompleteSearch` 방어와 실패 rollback 구조 승인 여부
6. 실제 Drive 종단 테스트 결과 승인 여부
7. MVP-04 완료 승인
8. 다음 단계 MVP-05 File Name Parser의 정확한 범위와 성공 조건 작성

다음 단계에서는 이 문서가 승인되기 전까지 Revision Parser 또는 Copy Parser를 구현하지 않는다.
