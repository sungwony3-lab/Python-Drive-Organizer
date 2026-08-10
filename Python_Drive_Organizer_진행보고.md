# Python Drive Organizer 진행 보고서

- 작성일: 2026-08-10 (Asia/Seoul)
- 프로젝트 위치: `C:\Users\HLB\Documents\Python-Drive-Organizer`
- 현재 단계: **1차 목표 / MVP-03 SQLite Drive Index 완료 및 검증 완료**
- 다음 예정 단계: **MVP-04 증분 동기화**
- 문서 목적: ChatGPT가 MVP-01부터 MVP-03까지의 구현과 검증 결과를 확인하고, 다음 MVP의 범위와 설계를 결정할 수 있도록 현재 상태를 전달한다.

---

## 1. 프로젝트 목표와 현재 원칙

Python과 Google Drive API를 이용해 Google Drive의 파일·폴더 메타데이터를 수집하고, SQLite에 현재 상태를 인덱싱하는 프로젝트다.

현재까지 유지한 핵심 원칙:

- Google Drive API v3 사용
- OAuth scope는 메타데이터 읽기 전용만 사용
- Drive 파일 내용은 읽지 않음
- Google Drive 생성·수정·이동·복사·삭제 API를 사용하지 않음
- SQLite는 Python 표준 `sqlite3` 모듈 사용
- 검증되지 않은 기능을 미리 확장하지 않음
- `credentials.json`, `token.json`, `data/`는 Git에 포함하지 않음

사용 중인 OAuth scope:

```text
https://www.googleapis.com/auth/drive.metadata.readonly
```

---

## 2. 개발 환경

- 운영 환경: Windows / PowerShell
- Python: `3.14.7`
- 가상환경: `.venv`
- Git 저장소 초기 커밋: `3675ed8 MVP-01 initial Python project setup`
- 실행 진입점: `python main.py`

설치된 직접 의존성:

```text
google-api-python-client==2.198.0
google-auth-httplib2==0.4.1
google-auth-oauthlib==1.4.0
```

SQLite용 외부 패키지는 설치하지 않았다.

---

## 3. MVP별 진행 결과

### MVP-01 — Python 실행 환경

완료 내용:

- 프로젝트 폴더와 Git 저장소 구성
- Python 가상환경 `.venv` 구성
- `main.py`, `requirements.txt`, `README.md`, `.gitignore` 구성
- PowerShell에서 Python 실행 확인
- Git 초기 커밋 완료

완료 판단: **완료**

### MVP-02 — Google Drive OAuth + 읽기 연결

구현 내용:

- 데스크톱 앱용 `credentials.json` 사용
- 최초 실행 시 브라우저 OAuth 로그인과 사용자 승인
- 승인 후 프로젝트 루트에 `token.json` 저장
- refresh token을 이용해 다음 실행부터 재승인 최소화
- Google Drive API v3 연결
- `trashed = false` 조건으로 파일과 폴더 조회
- 최대 20개 항목의 다음 필드 출력 테스트
  - `id`
  - `name`
  - `mimeType`
  - `parents`
  - `modifiedTime`
- Windows CP949에서 일부 파일명의 유니코드 문자가 출력되지 않는 문제를 발견하고, 콘솔 출력을 UTF-8로 설정

실제 검증 결과:

- OAuth 승인 성공
- `token.json` 생성 성공
- 저장된 token의 scope가 `drive.metadata.readonly` 하나뿐임을 확인
- refresh token 존재 확인
- 저장된 token 재사용 성공
- 재승인 없이 두 번째 실행 성공
- 파일과 폴더가 포함된 20개 메타데이터 출력 성공
- 종료 코드 `0`
- Drive 쓰기 동작 없음

완료 판단: **완료**

### MVP-03 — SQLite Drive Index

구현 내용:

- `database.py`에서 SQLite 연결, 데이터 디렉터리 생성, 스키마 생성, UPSERT, 스캔 상태 전환 담당
- `scanner.py`에서 Drive 메타데이터를 DB 레코드 형식으로 정규화하고 파일과 폴더를 분리 저장
- `drive_client.py`에서 Drive 전체 목록을 `pageSize=1000`으로 페이지 순회
- 프로그램 시작 시 `SCAN-YYYYMMDD-HHMMSS` 형식의 `scan_id` 생성
- 시작 직후 `scan_state`에 `RUNNING` 기록 및 커밋
- 성공 시 파일·폴더 UPSERT와 `COMPLETED` 상태를 커밋
- 오류 시 진행 중인 DB 변경을 롤백하고 가능한 경우 `FAILED`와 오류 메시지를 별도 기록
- 동일 `file_id`와 `folder_id`는 기본키 충돌 시 현재 값으로 UPSERT
- `parent_id`는 `parents` 배열의 첫 번째 값만 저장
- `extension`은 파일명의 마지막 suffix를 소문자로 저장하고, 없으면 `NULL`
- Google native 파일처럼 제공되지 않는 `size`, `md5Checksum` 등은 `NULL`

Drive API에서 읽는 필드:

```text
id
name
mimeType
parents
size
createdTime
modifiedTime
md5Checksum
trashed
ownedByMe
```

폴더 MIME type:

```text
application/vnd.google-apps.folder
```

완료 판단: **완료**

---

## 4. 현재 프로젝트 파일 구조

```text
Python-Drive-Organizer/
├─ .gitignore
├─ README.md
├─ main.py
├─ drive_client.py
├─ database.py
├─ scanner.py
├─ requirements.txt
├─ credentials.json                 # Git 제외
├─ token.json                       # Git 제외
├─ data/
│  └─ drive_index.db                # Git 제외
├─ Python_Drive_Organizer_개발지침.md
└─ Python_Drive_Organizer_진행보고.md
```

파일별 역할:

| 파일 | 역할 |
|---|---|
| `main.py` | 실행 진입점, scan 생성과 상태 전환, 오류 출력 |
| `drive_client.py` | 읽기 전용 OAuth와 Drive API v3 페이지 조회 |
| `database.py` | SQLite 연결, 스키마, UPSERT, scan 상태 변경 |
| `scanner.py` | Drive 항목 정규화, 파일·폴더 분리 저장 |
| `requirements.txt` | Google 공식 Python 클라이언트 의존성 |
| `README.md` | 준비와 실행 방법 |

---

## 5. SQLite 데이터베이스 현황

DB 위치:

```text
C:\Users\HLB\Documents\Python-Drive-Organizer\data\drive_index.db
```

- DB 크기: `3,833,856 bytes`
- SQLite `PRAGMA integrity_check`: `ok`

### files

| 필드 | 타입/제약 |
|---|---|
| `file_id` | `TEXT PRIMARY KEY` |
| `name` | `TEXT NOT NULL` |
| `mime_type` | `TEXT NOT NULL` |
| `extension` | `TEXT` |
| `size_bytes` | `INTEGER` |
| `created_time` | `TEXT` |
| `modified_time` | `TEXT` |
| `parent_id` | `TEXT` |
| `md5_checksum` | `TEXT` |
| `trashed` | `INTEGER NOT NULL DEFAULT 0` |
| `owned_by_me` | `INTEGER` |
| `scan_id` | `TEXT` |
| `indexed_at` | `TEXT NOT NULL` |

인덱스:

- `idx_files_parent_id(parent_id)`
- `idx_files_modified_time(modified_time)`
- 기본키 고유 인덱스 `file_id`

### folders

| 필드 | 타입/제약 |
|---|---|
| `folder_id` | `TEXT PRIMARY KEY` |
| `name` | `TEXT NOT NULL` |
| `parent_id` | `TEXT` |
| `scan_id` | `TEXT` |
| `indexed_at` | `TEXT NOT NULL` |

인덱스:

- `idx_folders_parent_id(parent_id)`
- 기본키 고유 인덱스 `folder_id`

### scan_state

| 필드 | 타입/제약 |
|---|---|
| `scan_id` | `TEXT PRIMARY KEY` |
| `status` | `TEXT NOT NULL` |
| `started_at` | `TEXT NOT NULL` |
| `finished_at` | `TEXT` |
| `files_seen` | `INTEGER NOT NULL DEFAULT 0` |
| `folders_seen` | `INTEGER NOT NULL DEFAULT 0` |
| `scope_type` | `TEXT` |
| `scope_id` | `TEXT` |
| `message` | `TEXT` |
| `created_at` | `TEXT NOT NULL` |

사용 상태 값:

```text
RUNNING
COMPLETED
FAILED
```

---

## 6. 실제 스캔 및 데이터 검증 결과

현재 저장 행 수:

| 대상 | 행 수 |
|---|---:|
| `files` | 7,914 |
| `folders` | 1,141 |

ID 중복 검사:

| 검사 | 결과 |
|---|---:|
| 중복된 `file_id` 그룹 | 0 |
| 중복된 `folder_id` 그룹 | 0 |

연속 실행 결과:

| 항목 | 1회차 | 2회차 | 변화 |
|---|---:|---:|---:|
| `files` 행 수 | 7,914 | 7,914 | 0 |
| `folders` 행 수 | 1,141 | 1,141 | 0 |
| `scan_state` 행 수 | 1 | 2 | +1 |

저장된 scan 이력:

| scan_id | status | started_at (UTC) | finished_at (UTC) | files_seen | folders_seen | scope_type | message |
|---|---|---|---|---:|---:|---|---|
| `SCAN-20260810-182031` | `COMPLETED` | `2026-08-10T09:20:31Z` | `2026-08-10T09:20:49Z` | 7,914 | 1,141 | `USER_DRIVE` | `NULL` |
| `SCAN-20260810-182104` | `COMPLETED` | `2026-08-10T09:21:04Z` | `2026-08-10T09:21:22Z` | 7,914 | 1,141 | `USER_DRIVE` | `NULL` |

추가 검증:

- Python 컴파일 성공
- 패키지 의존성 충돌 없음
- 메모리 SQLite에서 스키마와 인덱스 생성 성공
- 파일과 폴더 분리 저장 성공
- 같은 ID를 두 번 저장해도 단일 행으로 UPSERT되는 것 확인
- 첫 번째 parent만 저장되는 것 확인
- 확장자 추출과 숫자·불리언 변환 확인
- `RUNNING → COMPLETED` 상태 전환 확인
- 별도 메모리 DB에서 `FAILED`와 오류 메시지 기록 확인
- 실제 DB 무결성 검사 성공
- 코드에서 Drive `create`, `update`, `delete`, `copy` 호출이 없음을 확인

---

## 7. 현재 Git 상태

현재 MVP-02와 MVP-03 작업은 아직 새 커밋으로 만들지 않은 상태다.

추적 중이며 수정된 파일:

```text
README.md
main.py
requirements.txt
```

새로 생성되어 아직 추적되지 않은 구현 파일:

```text
drive_client.py
database.py
scanner.py
```

기존 `Python_Drive_Organizer_개발지침.md`도 현재 미추적 상태이며 이번 구현에서 내용을 변경하지 않았다. 이 진행 보고서 역시 생성 직후에는 미추적 상태다.

민감 정보와 생성 데이터는 `.gitignore` 대상이다.

```text
.venv/
credentials.json
token.json
data/
```

---

## 8. 이번 단계에서 의도적으로 구현하지 않은 기능

다음 기능은 MVP-03 범위에서 제외했으며 현재 코드에 없다.

- 증분 동기화 판단
- INSERT / UPDATE / SKIP 분류와 통계
- Drive에서 사라진 항목의 삭제 감지
- DB hard delete 또는 상태 기반 soft delete
- Revision Parser
- Copy Parser
- `groupKey`
- `file_groups`
- `full_path` 계산
- `_완료` 폴더 처리
- Google Drive 쓰기
- Excel/CSV 보고
- GUI
- AI 기능

중요: 현재는 매 스캔마다 조회된 항목을 UPSERT한다. 삭제 감지가 없으므로 앞으로 Drive에서 사라지거나 조회 범위에서 제외된 항목은 MVP-04 로직이 추가되기 전까지 DB에 남을 수 있다.

---

## 9. 다음 단계 MVP-04 전에 결정해야 할 사항

개발지침상 다음 단계는 **증분 동기화**다. 구현을 시작하기 전에 ChatGPT가 아래 정책을 명시적으로 검토하고 결정해야 한다.

### 9.1 변경 판정 필드

어떤 필드의 차이를 UPDATE로 판단할지 확정해야 한다.

후보:

- `name`
- `mime_type`
- `extension`
- `size_bytes`
- `created_time`
- `modified_time`
- `parent_id`
- `md5_checksum`
- `trashed`
- `owned_by_me`

### 9.2 SKIP과 scan_id 처리

변경이 없는 행을 완전히 UPDATE하지 않고 SKIP할 경우 기존 행의 `scan_id`가 이전 스캔 값으로 남는다. 이 상태에서 최신 `scan_id`가 아닌 행을 삭제 대상으로 판단하면 정상 항목이 삭제 대상으로 오인될 수 있다.

다음 중 하나를 결정해야 한다.

1. 데이터 필드는 SKIP하되 `scan_id`와 `indexed_at`은 갱신한다.
2. `scan_id`와 별개로 이번 스캔에서 확인한 ID를 임시 테이블이나 메모리 집합으로 관리한다.
3. 별도의 `last_seen_scan_id` 필드를 추가한다.

현재 스키마를 최소 변경하려면 1번이 가장 단순하지만, 이는 ChatGPT의 설계 확인 후 결정해야 한다.

### 9.3 삭제 감지 정책

Drive에서 이번 정상 스캔에 나타나지 않은 기존 DB 행을 어떻게 처리할지 결정해야 한다.

- DB에서 실제 `DELETE`
- `is_deleted`, `missing_since` 같은 상태 필드를 추가하는 soft delete
- 별도 변경 이력 테이블 사용

불완전하거나 실패한 스캔에서는 삭제 감지를 실행하면 안 된다. 삭제 판단은 전체 스캔이 정상적으로 끝난 경우에만 수행해야 한다.

### 9.4 증분 결과 통계

MVP-04에서 필요한 카운터를 확정해야 한다.

후보:

- files_inserted
- files_updated
- files_skipped
- files_deleted 또는 files_missing
- folders_inserted
- folders_updated
- folders_skipped
- folders_deleted 또는 folders_missing

기존 `scan_state`를 확장할지, 별도 통계 테이블을 만들지 결정해야 한다.

### 9.5 트랜잭션 원칙

현재 동작은 다음과 같다.

```text
scan_state RUNNING 기록 및 커밋
→ 전체 Drive 조회 및 files/folders 변경
→ 성공 시 변경과 COMPLETED를 커밋
→ 실패 시 files/folders 변경 롤백
→ FAILED와 message 기록 및 커밋
```

MVP-04에서도 이 원칙을 유지할지 확인해야 한다.

---

## 10. ChatGPT 검토 요청

이 문서를 기준으로 다음 내용을 검토해 달라.

1. MVP-01, MVP-02, MVP-03이 각각 완료 조건을 충족했는지 확인
2. 현재 SQLite 스키마와 저장 방식에 MVP-04 진행을 막는 문제가 있는지 확인
3. MVP-04의 정확한 범위와 성공 조건 확정
4. 변경 판정 필드 확정
5. SKIP 시 `scan_id` 처리 정책 확정
6. 삭제 감지 및 DB 반영 정책 확정
7. `scan_state` 확장 여부 확정
8. 위 결정 이후 Codex에 전달할 MVP-04 구현 지침 작성

권장 진행 순서:

```text
ChatGPT 문서 검토
→ MVP-03 완료 승인
→ MVP-04 정책 결정
→ MVP-04 구현 지침 작성
→ Codex 구현
→ 실제 Drive 스캔 2회 이상 검증
```

이 검토가 끝나기 전에는 Revision Parser, 그룹화, 경로 계산 또는 Drive 쓰기 기능으로 넘어가지 않는다.
