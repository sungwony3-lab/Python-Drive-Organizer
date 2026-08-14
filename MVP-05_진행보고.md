# Python Drive Organizer — MVP-05 진행보고

## 1. 완료 상태

- 챕터: MVP-05 File Name Parser
- 상태: **COMPLETED**
- 완료일: 2026-08-11
- Parser 버전: `MVP05-PARSER-1`
- DB: `data/drive_index.db`
- OAuth scope: `https://www.googleapis.com/auth/drive.metadata.readonly`

MVP-04에서 SQLite에 저장한 `files.name`과 `files.extension`만 사용해 결정론적으로 파일명을 분석하도록 구현했다. Parser 전용 실행에서는 Google Drive API를 호출하지 않으며, 이번 MVP에서 Drive 파일을 생성·수정·이동·복사·삭제하지 않았다.

## 2. 생성·수정 파일

### 생성

- `name_parser.py`: 독립 파일명 Parser와 `MVP05-PARSER-1` 규칙
- `test_name_parser.py`: 표준 라이브러리 `unittest` 기반 고정 테스트
- `MVP-05_진행보고.md`: 현재 챕터 진행 및 검증 보고서

### 수정

- `database.py`: Parser 컬럼 migration, 조회·backfill·INSERT·UPDATE 저장 지원
- `scanner.py`: 신규 파일 INSERT 및 파일명 변경 UPDATE 시 Parser 연동
- `main.py`: 기존 행 backfill 및 `--parse-only` 실행 경로 추가
- `README.md`: MVP-05 설명과 Parser 전용 실행 방법 추가

### 외부 패키지

- 새로 추가하거나 설치한 패키지 없음
- SQLite는 기존과 동일하게 Python 표준 `sqlite3` 사용
- Parser와 테스트도 Python 표준 라이브러리만 사용

## 3. Schema migration

기존 `files` 테이블에 다음 8개 컬럼을 idempotent migration으로 추가했다.

| 컬럼 | 타입 및 기본값 |
|---|---|
| `normalized_name` | `TEXT` |
| `base_name` | `TEXT` |
| `revision_type` | `TEXT` |
| `revision_number` | `INTEGER` |
| `copy_type` | `TEXT` |
| `copy_number` | `INTEGER` |
| `auto_action` | `TEXT NOT NULL DEFAULT 'NONE'` |
| `parser_version` | `TEXT` |

검증 결과:

- 기존 MVP-04 형식 DB에 migration 성공
- migration을 연속 두 번 실행해도 오류 없음
- 실제 DB에서도 `--parse-only` 2회차 실행 시 schema 재초기화 오류 없음
- 기존 DB를 삭제하거나 새로 만들지 않음

## 4. Parser 규칙

### 정규화

- 앞뒤 공백 제거
- 연속 공백을 한 칸으로 축소
- 영문 대문자를 소문자로 변환
- 한글·숫자·기타 문장부호는 보존
- 원본 `files.name`은 변경하지 않음

### Revision

- 확장자 직전 suffix에 명시적인 `R` 또는 `REV`가 있을 때만 인식
- `R1`, `R01`, `R.1`, `R.01`, `REV1`, `REV01`, `REV.1`, `REV.01`, `REV 1`, `REV 01` 지원
- 대소문자를 구분하지 않음
- 공백, 괄호, 밑줄, 하이픈 형태의 suffix 지원
- `R2 변압기 설치도면.pdf`처럼 본문 앞쪽에 있는 표기는 suffix로 제거하지 않음
- `테스트1.pdf`, `분전반2.pdf`, `2026 보고서.pdf`처럼 숫자만 붙은 이름은 Revision으로 판단하지 않음

### Copy

- `복사본`, `copy` suffix는 Copy로 인식하되 `auto_action=NONE`
- 확장자 직전의 단일 ` (양의 정수)` suffix는 다음과 같이 기록
  - `copy_type=SINGLE_PAREN_COPY`
  - `copy_number=해당 정수`
  - `auto_action=DELETE`
- `(1)(2)`, `(1) (2)`, `(1)(1)`, `(1) (1)` 등 복수 괄호 suffix는 `SINGLE_PAREN_COPY` 및 `DELETE` 대상에서 제외
- `ABC R2 (1).pdf`는 Revision 2와 Copy 1을 모두 인식하고 `base_name=ABC`, `auto_action=DELETE`로 기록

`auto_action=DELETE`는 DB의 분류 값일 뿐, Google Drive 삭제 동작을 실행하지 않는다.

## 5. 기존 DB 전체 backfill 결과

최초 Parser 실행 전후 결과는 다음과 같다.

| 항목 | 실행 전 | 1회차 실행 후 | 2회차 실행 후 |
|---|---:|---:|---:|
| `files` 행 수 | 7,914 | 7,914 | 7,914 |
| Parser 처리 행 수 | - | 7,914 | 0 |
| 현재 Parser 버전 행 수 | 0 | 7,914 | 7,914 |
| `file_id` 중복 그룹 | 0 | 0 | 0 |

보존 검증:

- 정렬된 `file_id` SHA-256은 실행 전후 모두 `849a867fb22a7ee07c62f027a085845e0ff4b7614b2b281d9984c30985ff2a9e`
- `file_id`, `scan_id`, `last_seen_scan_id`, `indexed_at` 결합 SHA-256은 실행 전후 모두 `9cb87ee8354a59a75320f438b91937f785fe592fbe99a99fc0a7de570e17086d`
- 1회차와 2회차 Parser 결과 SHA-256은 모두 `d70fd910c36b9f7055ab46476a3927fbf412e14ab1679769649b23796658cba2`
- Parser backfill로 `scan_id`, `last_seen_scan_id`, `indexed_at`을 변경하지 않음
- Parser backfill로 기존 Drive 메타데이터 의미 필드를 변경하지 않음

## 6. 최종 DB Parser 통계

실제 Drive 읽기 회귀 테스트에서 신규 파일 1개가 추가되어 최종 DB는 파일 7,915개다.

| 항목 | 최종 수량 |
|---|---:|
| 전체 파일 | 7,915 |
| `parser_version=MVP05-PARSER-1` | 7,915 |
| `normalized_name` 또는 `base_name` NULL | 0 |
| Revision 인식 파일 | 93 |
| Copy 인식 파일 | 95 |
| `SINGLE_PAREN_COPY` | 87 |
| `KOREAN_COPY` | 8 |
| `auto_action=DELETE` | 87 |
| `auto_action=NONE` | 7,828 |
| 복수 숫자 괄호 suffix | 3 |
| 복수 숫자 괄호 suffix 중 DELETE | 0 |

실제 DB에는 `ENGLISH_COPY`가 0개였지만 `ABC copy.pdf` 고정 단위 테스트로 정상 인식을 확인했다.

## 7. 테스트 결과

### Parser 단위 테스트

명령:

```powershell
python -m unittest -v test_name_parser.py
```

결과: **12개 테스트 전체 통과**

확인한 핵심 사례:

- 기본 파일명과 확장자 없는 파일명
- 한글 파일명과 연속 공백 정규화
- Revision 대소문자 및 다양한 suffix 표현
- 숫자만 붙은 파일명의 Revision 오판 방지
- `복사본`과 `copy`
- 단일 숫자 괄호 `(1)`, `(2)`, `(12)`
- 복수 숫자 괄호 예외 4종
- Revision과 단일 Copy의 결합
- 동일 입력의 결정론적 결과

### Parser/DB 통합 테스트

- 기존 MVP-04 schema에서 migration 2회 성공
- Parser 전용 실행에서 Drive API를 호출하지 않도록 대체한 테스트 통과
- backfill 1회차 2행, 2회차 0행인 메모리 DB 테스트 통과
- backfill 전후 `scan_id`, `last_seen_scan_id`, `indexed_at` 보존
- 신규 INSERT 시 Parser 결과 생성
- 파일명 UPDATE 시 Parser 결과 재계산
- SKIP 시 기존 Parser 결과와 Drive 메타데이터 보존

### 실제 DB Parser 전용 실행

명령:

```powershell
python main.py --parse-only
```

- 1회차: 7,914행 처리
- 2회차: 0행 처리
- 두 실행의 결과 일관성 확인
- OAuth 인증이나 Drive 조회 없이 완료

### MVP-04 실제 Drive 회귀 테스트

첫 번째 읽기 전용 스캔 `SCAN-20260811-075403`:

- 상태: `COMPLETED`
- 파일: seen 7,915 / INSERT 1 / UPDATE 4 / SKIP 7,910 / DELETE 0
- 폴더: seen 1,141 / INSERT 0 / UPDATE 0 / SKIP 1,141 / DELETE 0
- 신규·갱신 행을 포함한 모든 파일의 Parser 버전 정상

동일 Drive 상태의 두 번째 스캔 `SCAN-20260811-075540`:

- 상태: `COMPLETED`
- 파일: seen 7,915 / INSERT 0 / UPDATE 0 / SKIP 7,915 / DELETE 0
- 폴더: seen 1,141 / INSERT 0 / UPDATE 0 / SKIP 1,141 / DELETE 0

따라서 MVP-04의 INSERT/UPDATE/SKIP/DELETE 증분 동기화 동작이 유지되며, 변경 없는 두 번째 실행에서 전체 항목이 SKIP됐다.

### 최종 무결성

- `files` 행: 7,915
- `folders` 행: 1,141
- `file_id` 중복 그룹: 0
- `folder_id` 중복 그룹: 0
- `PRAGMA integrity_check`: `ok`
- `PRAGMA foreign_key_check`: 위반 0
- `pip check`: `No broken requirements found.`
- Python compile 검사: 통과
- `git diff --check`: 오류 없음

## 8. Drive 안전성 확인

- 사용 scope는 계속 `drive.metadata.readonly` 하나뿐임
- Parser 전용 경로는 OAuth 및 Drive API를 호출하지 않음
- Drive 클라이언트에는 `files().list()` 조회만 존재
- `files().create/update/delete/copy`, permissions API, 쓰기 scope가 없음
- `auto_action=DELETE`를 실제 Drive 작업으로 연결하지 않음

## 9. 실행 방법

Parser migration 및 기존/구버전 행만 backfill:

```powershell
python main.py --parse-only
```

기존 MVP-04 Drive 읽기 동기화와 Parser 연동:

```powershell
python main.py
```

테스트:

```powershell
python -m unittest -v test_name_parser.py
```

## 10. 다음 단계 인계

MVP-05의 저장 결과는 MVP-06 File Grouping에서 사용할 준비가 됐다. 다음 단계에서는 `base_name`, Revision/Copy 정보 등을 입력으로 그룹 기준을 설계할 수 있다.

다음 단계로 넘어가기 전 확인할 사항:

1. 이 보고서와 Parser 규칙을 ChatGPT에 검토 요청
2. `auto_action=DELETE`가 실제 삭제가 아닌 분류 값이라는 점 유지
3. MVP-06에서만 `groupKey` 및 `file_groups`를 설계
4. 그룹 기준 승인 전에는 Drive 쓰기나 자동 정리 기능을 추가하지 않음

이번 MVP에서는 `groupKey`, `file_groups`, `full_path`, `_완료` 처리, 보고서 출력, GUI, AI 판단 기능을 구현하지 않았다.
