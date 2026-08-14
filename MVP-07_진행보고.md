# Python Drive Organizer — MVP-07 진행보고

## 1. 완료 상태

- 챕터: MVP-07 Search & Report
- 상태: **COMPLETED**
- 완료일: 2026-08-11
- 1차 목표의 마지막 MVP
- DB: `data/drive_index.db`
- 검색 데이터: SQLite Drive Index, Parser, Grouping 결과

MVP-07은 현재 SQLite 데이터를 실용적으로 검색하고, 폴더 경로와 전체 Folder Tree를 파생 계산해 출력하는 읽기 전용 기능이다. 모든 검색·Tree 명령은 DB를 SQLite `mode=ro`로 열며 OAuth 및 Google Drive API를 호출하지 않는다.

## 2. 생성·수정 파일

### 생성

- `search_service.py`: SQLite 검색, folder path, 직접·재귀 목록, Tree 생성
- `test_search_service.py`: Search/Tree/CLI/read-only 자동 테스트
- `MVP-07_진행보고.md`: 현재 챕터 구현 및 검증 보고서

### 수정

- `main.py`: 검색 CLI, 옵션 충돌 검증, 출력 및 Tree 파일 저장
- `database.py`: 기존 연결 함수에 SQLite read-only 연결 지원 추가
- `README.md`: Search & Report와 Tree 실행 방법 추가

### 외부 패키지

- 새로 추가하거나 설치한 패키지 없음
- Python 표준 `argparse`, `sqlite3`, `pathlib` 등을 사용

## 3. 검색 구조

`main.py`는 CLI 해석과 사용자 출력을 담당하고, `search_service.py`는 SQLite 조회와 파생 계산만 담당한다.

검색 실행 흐름:

```text
CLI 옵션 검증
→ data/drive_index.db를 mode=ro로 연결
→ folders 전체를 한 번 읽어 메모리 index 생성
→ 검색별 SELECT 또는 기존 group/member JOIN 실행
→ path 및 Tree 파생 계산
→ Total matched / Showing 및 상세 결과 출력
```

읽기 전용 연결에 UPDATE를 시도한 별도 검증 결과:

```text
attempt to write a readonly database
```

검색에서는 schema 초기화, Parser backfill, Grouping 재구성도 실행하지 않는다.

## 4. Folder path 계산

검색 명령마다 `folders`를 한 번 읽어 다음 구조를 만든다.

- `folder_id → folder row`
- `parent_id → 정렬된 child folders`

파일 또는 폴더의 parent chain을 반복문으로 따라가며 `/A/B/C` 형태의 path를 만든다. DB에는 `full_path` 컬럼을 추가하지 않는다.

예외 표시:

- parent가 DB에 없음: `/[MISSING_PARENT:<id>]/...`
- cycle 발견: `/[CYCLE:<id>]/...`
- parent가 NULL인 항목: `/...`

folder identity는 이름이 아닌 `folder_id`다. 동일 이름을 가진 서로 다른 folder_id는 병합하지 않는다. 경로 계산과 재귀 순회 모두 visited set을 사용해 cycle과 self-parent의 무한 루프를 방지한다.

## 5. 지원 CLI

### 파일명 검색

```powershell
python main.py --search-name "검색어" [--limit 100]
```

대상:

- `files.name`
- `files.normalized_name`
- `files.base_name`

부분 문자열 검색이며 영문 대소문자를 검색 방해 요소로 사용하지 않는다. fuzzy matching이나 파일 내용 검색은 없다.

### Folder 검색과 목록

```powershell
python main.py --search-folder "검색어" [--limit 100]
python main.py --list-folder <folder_id> [--recursive] [--limit 100]
```

`--list-folder` 기본은 직접 자식 파일·폴더, `--recursive`는 전체 descendant다.

### Revision, Copy, auto-delete 분류

```powershell
python main.py --search-revisions [--min-revision N] [--limit 100]
python main.py --search-copies [--limit 100]
python main.py --search-auto-delete [--limit 100]
```

`--search-auto-delete` 상단에는 항상 다음 경고를 출력한다.

```text
AUTO-DELETE CLASSIFICATION ONLY
NO DRIVE ACTION EXECUTED
```

MVP-05의 기존 `auto_action=DELETE` 값만 조회하며 새 삭제 판단이나 실제 삭제는 없다.

### Group 검색

```powershell
python main.py --search-groups [--min-members N] [--limit 100]
```

그룹 통계와 member의 이름, member type, Revision/Copy 번호, auto action을 함께 출력한다.

### 최근 항목과 scan 변경 현재 행

```powershell
python main.py --recent [N]
python main.py --changed-in-scan <scan_id> [--limit 100]
```

`--recent`의 N 생략 시 20개다. `--changed-in-scan`은 현재 DB에 남아 있고 `scan_id`가 일치하는 파일·폴더만 출력한다. 과거 DELETE 상세 이력을 복원할 수 없다는 경고를 명시한다.

### Folder Tree

```powershell
python main.py --tree
python main.py --tree --root-folder <folder_id>
python main.py --tree --max-depth N
python main.py --tree --include-files
python main.py --tree --output <path>
```

Tree는 `├─`, `└─`, `│` 문자를 사용하고 파일은 `[FILE]`로 구분한다. `--output`은 콘솔과 동일한 내용을 UTF-8 text로 저장한다.

### 기존 명령

```powershell
python main.py
python main.py --parse-only
python main.py --group-only
```

기존 세 실행 경로도 그대로 유지된다.

## 6. CLI 안전장치

한 실행에서는 다음 모드 중 하나만 선택할 수 있다.

- `--parse-only`
- `--group-only`
- 각 Search 모드
- `--tree`

서로 다른 모드를 동시에 지정하면 `argparse` usage error와 종료 코드 2를 반환한다.

보조 옵션도 관련 모드에서만 허용한다.

- `--recursive`: `--list-folder` 전용
- `--min-revision`: `--search-revisions` 전용
- `--min-members`: `--search-groups` 전용
- `--root-folder`, `--max-depth`, `--include-files`, `--output`: `--tree` 전용
- `--limit`: 결과 목록 검색 전용

## 7. 실제 DB 기준 상태

MVP-04 회귀 스캔에서 Drive에 새로 생긴 파일 7개와 메타데이터 변경 1건을 반영한 후 Grouping을 재생성했다.

| 테이블 | 최종 행 수 |
|---|---:|
| `files` | 7,922 |
| `folders` | 1,141 |
| `scan_state` | 15 |
| `file_groups` | 7,896 |
| `file_group_members` | 7,922 |

검색 시작 시점의 DB 값이 source of truth다.

## 8. 실제 DB 검색 검증

### 일반 파일명 검색

```powershell
python main.py --search-name "AUTODIM" --limit 5
```

- Total matched: 1
- Showing: 1
- 결과: `AUTODIM.LSP`
- `file_id`, 계산된 path, extension, modified time, Parser 정보, group_id 출력 확인

### 한글 검색

```powershell
python main.py --search-name "송금확인증" --limit 5
```

- Total matched: 2
- Showing: 2
- `송금확인증 (1).pdf`, `송금확인증 (2).pdf` 검색 성공
- 두 파일이 같은 group_id에 속하고 `SINGLE_PAREN_COPY`, `auto_action=DELETE`인 기존 값 확인

동일 명령을 두 번 실행한 결과:

- 전체 출력 동일: `True`
- 출력 SHA-256: `7337c9f77398b729a8e34bfe09bae89b51cf0582ad70fbd0767e6d1bfa6031d8`

### Folder 검색

```powershell
python main.py --search-folder "안전서류" --limit 3
```

- Total matched: 1
- 실제 folder_id: `1jQlwAxTH3bTmtAshRKsfzevyzeEYWfKm`
- 이름: `3. HLB일렉 안전서류 (최초본)`
- parent chain을 이용한 전체 path 출력 확인

### 직접 및 재귀 Folder 목록

검증 folder_id:

```text
1jQlwAxTH3bTmtAshRKsfzevyzeEYWfKm
```

- 직접 자식: Total matched 29
  - 자식 folder 22개
  - 자식 file 7개
- `--recursive`: Total matched 565
- 결과 limit 적용 시 Total matched는 전체 수, Showing은 실제 출력 수로 분리
- 자식 folder/file 모두 계산된 path와 identity 출력

### Revision

```powershell
python main.py --search-revisions
```

- Total matched: 93
- `revision_type=REVISION` 또는 `revision_number IS NOT NULL` 조건
- `group_id`, 그룹의 `latest_revision_number` JOIN 확인
- 삭제나 구버전 판정 없음

### Copy

```powershell
python main.py --search-copies
```

- Total matched: 95
- `SINGLE_PAREN_COPY`, `KOREAN_COPY`, `ENGLISH_COPY`만 포함
- 기존 Copy 번호 및 auto action 출력 확인

### Auto-delete 분류

```powershell
python main.py --search-auto-delete
```

- Total matched: 87
- 실제 DB의 `files.auto_action=DELETE` 수와 일치
- 분류 경고문 출력
- 실제 Drive action 없음

### Multi-member group

```powershell
python main.py --search-groups --min-members 2
```

- Total matched: 25 groups
- group 통계와 member 목록 출력 확인
- 실제 3-member Revision 그룹에서 Revision 2, 3, 4와 `latest_revision_number=4` 확인

### Recent

```powershell
python main.py --recent 5
```

최신 순서 검증 결과:

1. `분전반설치도면.dwl`
2. `분전반설치도면.dwl2`
3. `작업일지 (도면 05.01~05.31) 30일 작업본.dwl`
4. `작업일지 (도면 05.01~05.31) 30일 작업본.dwl2`
5. `AUTODIM.LSP`

`modified_time DESC`와 결정론적 보조 정렬을 사용한다.

### 특정 scan의 현재 행

```powershell
python main.py --changed-in-scan SCAN-20260811-081617
```

- 현재 행 1개 조회
- `AUTODIM.LSP`의 현재 파일 정보 출력
- 삭제된 과거 파일 상세를 보존하거나 복원한다고 표현하지 않음

## 9. Folder Tree 검증

### 전체 Tree

```powershell
python main.py --tree
```

- 종료 코드: 0
- DB folders: 1,141
- Tree의 고유 folder_id: 1,141
- 누락: 0
- 동일 DB에서 두 번 생성한 Tree 텍스트 SHA-256:
  - 1회차: `be389772308f44362b89a75f49fee58126122749fb968591fac705fe5aab2fcd`
  - 2회차: 동일

### 특정 root, depth 및 파일 포함

다음 실제 folder_id로 검증했다.

```text
1jQlwAxTH3bTmtAshRKsfzevyzeEYWfKm
```

실행:

```powershell
python main.py --tree `
  --root-folder 1jQlwAxTH3bTmtAshRKsfzevyzeEYWfKm `
  --max-depth 1 `
  --include-files
```

- root folder 출력 성공
- 1단계 자식 folder 22개와 직접 자식 file 7개 출력
- `Folders shown: 23`
- `Files shown: 7`
- 한글 이름과 Unicode Tree 문자 정상 출력

전체 `--include-files` 결과의 고유 수:

- folders: 1,141
- files: 7,922

### Tree 예외 및 깊이

자동 테스트에서 다음을 확인했다.

- parent 누락 folder를 `[MISSING PARENT: ...]` 영역에 표시
- cycle 및 self-parent folder를 `[CYCLES / UNRESOLVED]` 영역에 표시
- 모든 folder_id를 최대 한 번만 출력
- 같은 이름의 다른 folder_id 두 개 모두 유지
- 1,100단계 인공 folder chain을 Python 재귀 호출 없이 출력
- 없는 `--root-folder`에 명확한 오류
- UTF-8 `--output` 파일과 콘솔 Tree 내용 일치

## 10. 읽기 전용 보존 검증

실제 DB에서 파일명, Folder, Revision, Copy, auto-delete, Group, Recent, scan, Tree 조회를 수행하기 전후 다음 5개 테이블 전체를 hash했다.

- `files`
- `folders`
- `scan_state`
- `file_groups`
- `file_group_members`

통합 SHA-256:

```text
3ce2add7f13b936ffc62b2e322e6815884fa215256a18dc5c7d0879ad3496967
```

MVP-04 회귀 스캔과 최신 Grouping을 완료한 최종 DB에서 측정했으며, 검색 전후 해시는 동일했다. 검색 명령 자체로 발생한 행 추가·수정·삭제는 0이다. 회귀 스캔이 Drive의 새 파일과 `scan_state`를 반영한 변경은 Search 명령의 변경과 분리해 검증했다.

## 11. 자동 테스트

명령:

```powershell
python -m unittest -v `
  test_name_parser.py `
  test_file_grouping.py `
  test_search_service.py
```

결과:

- MVP-05 Parser: 12개 통과
- MVP-06 Grouping: 12개 통과
- MVP-07 Search & Tree: 15개 통과
- 합계: 39개 전체 통과
- Python compile 검사: 통과
- `pip check`: `No broken requirements found.`
- `git diff --check`: 오류 없음

MVP-07 테스트 범위:

- 파일명 부분·대소문자·한글 검색
- limit 및 Total matched/Showing
- folder path, 누락 parent, cycle, self-parent
- folder 검색, 직접 자식, recursive 목록
- Revision, Copy, auto-delete, Group member 조회
- Recent 정렬 및 scan 현재 행
- CLI 모드 충돌 및 보조 옵션 오용 방어
- Search CLI Drive 인증 미호출
- Tree root/depth/files/output 및 결정성
- 동일 이름의 다른 folder identity
- 1,100단계 Tree
- 검색 전후 5개 핵심 테이블 무변경

## 12. MVP-04/05/06 회귀

### MVP-04 실제 Drive 동기화

첫 회귀 스캔 `SCAN-20260811-090504`:

- 상태: `COMPLETED`
- 파일: seen 7,922 / INSERT 7 / UPDATE 1 / SKIP 7,914 / DELETE 0
- 폴더: seen 1,141 / INSERT 0 / UPDATE 0 / SKIP 1,141 / DELETE 0

Drive에 새로 생긴 파일과 변경 메타데이터를 정상 반영했다.

즉시 재실행한 `SCAN-20260811-090540`:

- 상태: `COMPLETED`
- 파일: seen 7,922 / INSERT 0 / UPDATE 0 / SKIP 7,922 / DELETE 0
- 폴더: seen 1,141 / INSERT 0 / UPDATE 0 / SKIP 1,141 / DELETE 0

변경 없는 두 번째 실행에서 전체 항목이 SKIP됐다.

### MVP-05 Parser

```powershell
python main.py --parse-only
```

- Parser 버전: `MVP05-PARSER-1`
- backfill: 0행
- 새로 반영된 파일 7개를 포함해 Parser 버전 최신 상태
- Parser 단위 테스트 12개 통과

### MVP-06 Grouping

```powershell
python main.py --group-only
```

- files: 7,922
- groups: 7,896
- members: 7,922
- member_count 통계 불일치: 0
- Grouping 테스트 12개 통과

## 13. DB 및 Drive 안전성

- `PRAGMA integrity_check`: `ok`
- `PRAGMA foreign_key_check`: 위반 0
- Search/Tree SQLite read-only 연결 확인
- Search/Tree 실행 시 `authenticate()` 미호출 mock 검증 통과
- Search/Tree 모듈은 Google API 패키지를 import하지 않음
- Drive 클라이언트에는 `files().list()` 조회만 존재
- OAuth scope는 `drive.metadata.readonly` 하나뿐임
- `files().create/update/delete/copy`, permissions API, Drive 쓰기 scope 없음
- 실제 파일 삭제·이동·이름 변경·복사 없음
- Revision 구버전 삭제 판단 및 새로운 auto-delete 규칙 없음

## 14. 1차 목표 인계

MVP-01부터 MVP-07까지 다음 기반이 완성됐다.

- Google Drive read-only OAuth 및 메타데이터 수집
- SQLite 전체·증분 인덱스
- 결정론적 File Name Parser
- parent 및 extension 분리 File Grouping
- 읽기 전용 검색, 통계, 경로 및 전체 Folder Tree

다음 단계 결정 전에는 실제 Drive 정리 기능을 추가하지 않는다. 특히 `auto_action=DELETE`는 조회 가능한 기존 분류값일 뿐 실행 명령이 아니다.
