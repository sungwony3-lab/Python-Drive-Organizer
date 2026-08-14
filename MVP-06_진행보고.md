# Python Drive Organizer — MVP-06 진행보고

## 1. 완료 상태

- 챕터: MVP-06 File Grouping
- 상태: **COMPLETED**
- 완료일: 2026-08-11
- 입력 데이터: SQLite `files`와 `MVP05-PARSER-1` 결과
- DB: `data/drive_index.db`
- OAuth scope: `https://www.googleapis.com/auth/drive.metadata.readonly`

현재 SQLite 파일 데이터를 이용해 같은 파일 계열을 결정론적으로 그룹화하고 그룹 통계를 계산하도록 구현했다. Grouping 전용 실행에서는 OAuth, Google Drive API, Parser backfill을 호출하지 않으며 원본 `files` 행을 변경하지 않는다.

이번 MVP에서는 Revision 구버전 삭제 정책이나 실제 Drive 작업을 구현하지 않았다.

## 2. 생성·수정 파일

### 생성

- `file_grouping.py`: group key, 결정론적 `group_id`, member 분류, 통계 및 트랜잭션 재구성
- `test_file_grouping.py`: 고정 fixture 기반 단위·통합·롤백 테스트
- `MVP-06_진행보고.md`: 현재 챕터 진행 및 검증 보고서

### 수정

- `database.py`: `file_groups`, `file_group_members`와 최소 인덱스 추가
- `main.py`: Drive 미호출 `--group-only` 실행 경로 추가
- `README.md`: grouping 실행 방법과 안전 범위 추가

### 외부 패키지

- 새로 추가하거나 설치한 패키지 없음
- Python 표준 `hashlib`, `sqlite3` 등을 사용

## 3. DB schema

### `file_groups`

| 컬럼 | 정의 |
|---|---|
| `group_id` | `TEXT PRIMARY KEY` |
| `parent_id` | `TEXT` |
| `group_base_name` | `TEXT NOT NULL` |
| `extension` | `TEXT` |
| `member_count` | `INTEGER NOT NULL` |
| `revision_count` | `INTEGER NOT NULL` |
| `copy_count` | `INTEGER NOT NULL` |
| `auto_delete_count` | `INTEGER NOT NULL` |
| `latest_revision_number` | `INTEGER` |
| `created_at` | `TEXT NOT NULL` |
| `updated_at` | `TEXT NOT NULL` |

조회 인덱스:

- `idx_file_groups_lookup(parent_id, group_base_name, extension)`

### `file_group_members`

| 컬럼 | 정의 |
|---|---|
| `group_id` | `TEXT NOT NULL` |
| `file_id` | `TEXT NOT NULL` |
| `member_type` | `TEXT NOT NULL` |
| `revision_number` | `INTEGER` |
| `copy_number` | `INTEGER` |
| `auto_action` | `TEXT NOT NULL` |

제약 및 인덱스:

- `PRIMARY KEY(group_id, file_id)`
- `UNIQUE(file_id)`로 한 파일이 복수 그룹에 들어가는 것을 DB 수준에서 방어
- `idx_file_group_members_group_id`
- `idx_file_group_members_file_id` UNIQUE 인덱스

두 테이블과 인덱스는 `CREATE ... IF NOT EXISTS` 방식이며 schema 초기화를 반복 실행해도 오류가 없다.

## 4. Group key 및 group_id

기본 group key:

```text
parent_id + group_base_name + extension
```

`group_base_name`은 기존 `files.base_name`에 다음 최소 정규화만 적용한다.

- 앞뒤 공백 제거
- 연속 공백을 한 칸으로 축소
- 영문 소문자화
- 한글, 숫자, 의미 있는 문장부호 보존

`parent_id`와 `extension`은 key에 그대로 포함한다. 따라서 같은 `base_name`이어도 parent가 다르거나 extension이 다르면 별도 그룹이다.

`group_id`는 세 구성 요소를 다음과 같이 canonicalize한 UTF-8 문자열의 SHA-256이다.

- NULL: `N`
- 문자열: `S<문자 길이>:<값>`
- 각 구성 요소는 `|`로 연결

이 방식은 NULL과 빈 문자열을 구분하며, 값 안에 구분자가 포함되어도 구성 요소 경계가 모호해지지 않는다. 같은 입력은 항상 동일한 64자리 SHA-256 `group_id`를 생성한다.

## 5. Member 분류와 통계

`member_type` 우선순위:

1. `auto_action=DELETE` → `AUTO_DELETE_COPY`
2. `revision_type=REVISION` → `REVISION`
3. `copy_type`이 `SINGLE_PAREN_COPY`, `KOREAN_COPY`, `ENGLISH_COPY` 중 하나 → `COPY`
4. 그 외 → `NORMAL`

Revision과 Copy가 혼합되고 `auto_action=DELETE`인 파일은 `AUTO_DELETE_COPY`로 분류하면서 `revision_number`와 `copy_number`를 모두 보존한다.

그룹 통계:

- `member_count`: 전체 member 수
- `revision_count`: `revision_number`가 있거나 Revision 구조인 member 수
- `copy_count`: MVP-05 Copy 구조인 member 수
- `auto_delete_count`: `auto_action=DELETE` member 수
- `latest_revision_number`: 존재하는 `revision_number`의 최댓값, 없으면 NULL

MVP-05의 `base_name`, Copy 및 `auto_action` 판단을 그대로 사용하며 Grouping에서 파일명을 추가 추론하지 않는다.

## 6. 안전한 전체 재구성

- 현재 `files` 전체를 먼저 읽고 새 그룹 결과를 메모리에서 완성
- 기존 정상 그룹과 새 결과를 비교해 변경 없는 그룹의 `created_at`, `updated_at` 보존
- `BEGIN IMMEDIATE` 트랜잭션 안에서 group member와 group row만 전체 재생성
- 처리 중 오류가 발생하면 `rollback`하여 기존 정상 그룹 결과 보존
- 원본 `files`를 UPDATE 또는 DELETE하지 않음
- Parser 버전이 `MVP05-PARSER-1`이 아니거나 `base_name`이 없으면 원본을 수정하지 않고 `--parse-only` 선행 안내와 함께 중단

강제 오류 trigger를 사용한 테스트에서 member 삭제 이후 group 삭제 단계가 실패해도 이전 group/member 결과가 완전히 복원되는 것을 확인했다.

## 7. 실제 DB grouping 결과

| 항목 | 결과 |
|---|---:|
| 전체 `files` | 7,915 |
| 전체 group | 7,889 |
| 전체 member | 7,915 |
| member가 없는 group | 0 |
| 복수 group에 속한 `file_id` | 0 |
| 중복 `(group_id, file_id)` | 0 |

### member_count별 그룹

| member_count | group 수 |
|---:|---:|
| 1 | 7,864 |
| 2 | 24 |
| 3 | 1 |

분포 합계 검산:

```text
7,864 × 1 + 24 × 2 + 1 × 3 = 7,915 members
```

### 그룹 유형 통계

| 항목 | group 수 |
|---|---:|
| Revision 포함 group | 87 |
| Copy 포함 group | 93 |
| Auto-delete 분류 포함 group | 85 |

### member_type 통계

| member_type | member 수 |
|---|---:|
| `NORMAL` | 7,729 |
| `REVISION` | 91 |
| `COPY` | 8 |
| `AUTO_DELETE_COPY` | 87 |

Revision 구조를 가진 일부 파일이 `AUTO_DELETE_COPY` 우선순위로 기록되므로 `REVISION` member_type 수와 전체 Revision 구조 수는 같지 않을 수 있다. `revision_count` 계산에서는 해당 혼합 파일도 포함한다.

모든 group의 통계를 `files`와 members에서 별도로 재계산한 결과:

- `member_count`, `revision_count`, `copy_count`, `auto_delete_count` 불일치: 0
- `latest_revision_number` 불일치: 0

## 8. 핵심 사례 검증

### 필수 고정 fixture

같은 parent와 `.pdf` extension의 다음 5개 파일을 테스트했다.

```text
ABC.pdf
ABC R1.pdf
ABC R2.pdf
ABC (1).pdf
ABC (2).pdf
```

하나의 그룹으로 생성됐으며 결과는 다음과 같다.

- `member_count=5`
- `revision_count=2`
- `copy_count=2`
- `auto_delete_count=2`
- `latest_revision_number=2`

다른 parent의 `ABC R1.pdf`와 `ABC R2.pdf`, 그리고 다른 extension의 `.pdf`와 `.dwg`는 각각 별도 그룹으로 분리됐다.

### SINGLE_PAREN_COPY 실제 DB 예시

실제 DB의 `송금확인증` PDF 그룹:

- `송금확인증 (1).pdf`
- `송금확인증 (2).pdf`
- `member_count=2`
- `copy_count=2`
- `auto_delete_count=2`
- 두 member 모두 `AUTO_DELETE_COPY`

이는 DB 분류일 뿐 실제 파일 삭제는 실행하지 않았다.

### Revision + Copy

`ABC R2 (1).pdf` 고정 테스트 결과:

- `member_type=AUTO_DELETE_COPY`
- `revision_number=2` 보존
- `copy_number=1` 보존
- `auto_action=DELETE` 보존
- 그룹의 Revision/Copy/Auto-delete 통계에 모두 포함

### 복수 괄호 suffix 예외

고정 테스트의 `ABC.pdf`와 `ABC (1)(2).pdf`는 서로 다른 `base_name`을 사용하여 별도 그룹이 됐다.

실제 DB의 복수 괄호 suffix 파일 3개도 각각 다음 결과를 유지했다.

- Parser `base_name` 전체 보존
- 독립된 `group_base_name`
- 각 group의 `member_count=1`
- `member_type=NORMAL`
- `auto_action=NONE`
- 기본 이름 그룹으로 강제 병합된 항목 0

## 9. 결정성 및 원본 보존

### Grouping 2회 결과

동일 SQLite 상태에서 전체 grouping을 연속 두 번 수행했다.

- 1회차: files 7,915 / groups 7,889 / members 7,915
- 2회차: files 7,915 / groups 7,889 / members 7,915
- `file_groups` 전체 SHA-256: 두 실행 모두 `e6f45b4321927adb01987768cea3daef0a1131eff17430ea6513a35f4a34a238`
- `file_group_members` 전체 SHA-256: 두 실행 모두 `ede62ff3e4015fb893be4e6580cb23bb45734224c4215338e1978aa0a7703e85`

해시에는 group timestamp도 포함되어 있으며, 변경 없는 재실행에서 timestamp까지 동일하게 유지됐다.

### `files` 원본 보존

최종 grouping 실행 직전과 직후:

- 행 수: 7,915 → 7,915
- `files` 전체 컬럼 SHA-256: `aecc1ed437d72cb17c38e42c43c63dabae7765b55a4b8610165a192770be7dd4` → 동일
- MVP-05 Parser 컬럼 SHA-256: `ac903ecc7ff1e93489ebcf351d5992143daa7c16ee6a48798d52784c3a2d2096` → 동일

첫 실제 grouping 검증에서도 별도 기준 해시를 사용해 `files` 전체 및 주요 Drive metadata가 전후 동일함을 확인했다.

## 10. 테스트 및 회귀 결과

### 자동 테스트

```powershell
python -m unittest -v test_file_grouping.py test_name_parser.py
```

- MVP-06 grouping 테스트: 12개 통과
- MVP-05 Parser 테스트: 12개 통과
- 합계: 24개 전체 통과
- Python compile 검사: 통과
- `pip check`: `No broken requirements found.`
- `git diff --check`: 오류 없음

Grouping 테스트에는 schema 재실행, 고정 통계 fixture, parent/extension 분리, NULL-safe group ID, 복수 괄호 예외, Revision+Copy, Copy member type, 동일 결과 2회, files 보존, 복수 그룹 UNIQUE 방어, 실패 rollback, Drive 인증 미호출을 포함했다.

### MVP-05 회귀

```powershell
python main.py --parse-only
```

- Parser 버전: `MVP05-PARSER-1`
- 처리 행: 0
- 기존 Parser 값 변경 없음
- SINGLE_PAREN_COPY 및 복수 괄호 고정 테스트 통과

### MVP-04 실제 Drive 회귀

첫 읽기 전용 스캔 `SCAN-20260811-081617`:

- 상태: `COMPLETED`
- 파일: seen 7,915 / INSERT 0 / UPDATE 1 / SKIP 7,914 / DELETE 0
- 폴더: seen 1,141 / INSERT 0 / UPDATE 0 / SKIP 1,141 / DELETE 0

Drive에서 발생한 메타데이터 변경 1건을 정상 반영했다.

즉시 재실행한 `SCAN-20260811-081655`:

- 상태: `COMPLETED`
- 파일: seen 7,915 / INSERT 0 / UPDATE 0 / SKIP 7,915 / DELETE 0
- 폴더: seen 1,141 / INSERT 0 / UPDATE 0 / SKIP 1,141 / DELETE 0

따라서 MVP-04의 INSERT/UPDATE/SKIP/DELETE 비교 동작과 변경 없는 재실행의 전체 SKIP가 유지됐다. 회귀 스캔 후 현재 DB 기준으로 grouping을 다시 생성했다.

### DB 무결성

- `PRAGMA integrity_check`: `ok`
- `PRAGMA foreign_key_check`: 위반 0
- `file_id` 복수 group 소속: 0
- group member 중복: 0

## 11. Drive 안전성

- `--group-only`는 SQLite만 사용하고 OAuth 및 Drive API를 호출하지 않음
- 테스트에서 `authenticate()`가 호출되면 실패하도록 설정한 상태로 `--group-only` 성공
- Drive 클라이언트에는 `files().list()` 읽기 조회만 존재
- OAuth scope는 `drive.metadata.readonly` 하나뿐임
- `files().create/update/delete/copy`, permissions API, Drive 쓰기 scope 없음
- `auto_action=DELETE`를 실제 Drive 작업으로 연결하지 않음

## 12. 실행 방법

현재 SQLite 상태로 group만 전체 재구성:

```powershell
python main.py --group-only
```

Parser backfill:

```powershell
python main.py --parse-only
```

기존 Drive 읽기 증분 동기화:

```powershell
python main.py
```

Drive 동기화에서 INSERT, 이름 변경 UPDATE 또는 DELETE가 발생한 뒤에는 최신 `files` 상태를 반영하도록 `--group-only`를 다시 실행한다.

## 13. 다음 단계 인계

MVP-06은 그룹 생성과 통계까지만 완료했다. 다음 단계에서 정책을 설계할 때 사용할 수 있는 기반은 다음과 같다.

- 결정론적 `group_id`
- parent와 extension이 분리된 정확한 그룹
- member별 NORMAL/REVISION/COPY/AUTO_DELETE_COPY 구분
- Revision, Copy, Auto-delete 수 및 최신 Revision 번호
- 원본 files를 건드리지 않는 안전한 파생 데이터 재구성

다음 단계 승인 전에는 다음 기능을 추가하지 않는다.

- Revision 최신본/구버전 삭제 판단
- 실제 Google Drive 삭제·이동·수정
- fuzzy matching 또는 AI 유사도 판단
- `full_path`, `_완료` 폴더 처리
- Excel/CSV 및 GUI
