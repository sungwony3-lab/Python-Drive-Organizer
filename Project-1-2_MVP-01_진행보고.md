# Python Drive Organizer — Project 1-2 MVP-01 진행보고

## 1. 완료 상태

- 단계: 프로젝트 1-2
- 챕터: MVP-01 Local Read-Only API
- 상태: **COMPLETED**
- 완료일: 2026-08-11
- API 버전: `1.2-MVP01`
- 로컬 주소: `http://127.0.0.1:8000`
- DB: `data/drive_index.db`

기존 MVP-07 `search_service.py`와 SQLite Drive Index를 재사용해 localhost 전용 FastAPI JSON API를 구현했다. API 요청은 기존 DB를 SQLite `mode=ro`로 열며 Google Drive API를 호출하지 않는다.

이번 MVP에서는 Cloudflare Tunnel, 외부 HTTPS 공개, GPTs Actions, 사용자 인증, Drive 동기화 API 및 Drive 쓰기 기능을 구현하지 않았다.

## 2. 생성·수정 파일

### 생성

- `api_server.py`: FastAPI app, response model, read-only DB dependency 및 11개 GET endpoint
- `test_api_server.py`: TestClient 기반 endpoint·오류·read-only·Drive 미호출 테스트
- `Project-1-2_MVP-01_진행보고.md`: 현재 챕터 구현 및 검증 보고서

### 수정

- `requirements.txt`: FastAPI 실행 및 TestClient에 필요한 최소 dependency 추가
- `database.py`: 기존 연결 함수의 SQLite read-only mode 재사용
- `README.md`: localhost API 실행법, 문서 URL, endpoint 목록 및 안전 범위 추가
- `search_service.py`: 전체 Tree의 `max_depth` 제한 시 잘린 정상 descendant를 cycle로 오인하지 않도록 구조적 root 판정 보강
- `test_search_service.py`: 전체 Tree depth 회귀 테스트 추가

## 3. 추가 dependency

`requirements.txt` 추가 항목:

- `fastapi`
- `uvicorn`
- `httpx` — TestClient의 HTTP client dependency

가상환경 설치 버전:

| 패키지 | 버전 |
|---|---:|
| FastAPI | 0.141.1 |
| Uvicorn | 0.52.1 |
| HTTPX | 0.28.1 |

별도 웹 프레임워크나 외부 DB 패키지는 추가하지 않았다. `pip check` 결과는 `No broken requirements found.`이다.

## 4. API 구조

요청 처리 흐름:

```text
FastAPI endpoint
→ data/drive_index.db를 SQLite mode=ro로 연결
→ 기존 SearchService 생성
→ 기존 검색·경로·그룹·Tree 로직 실행
→ Pydantic response model로 JSON 반환
→ 요청 종료 시 DB 연결 닫기
```

검색 SQL이나 파일명 규칙을 `api_server.py`에 복제하지 않았다. 파일·폴더 검색, children, Revision, Copy, Group, Recent, Tree는 모두 기존 `SearchService` 메서드를 호출한다.

`/status`만 현재 개수와 최신 scan 상태를 위한 최소 집계 SELECT를 직접 실행한다.

## 5. Endpoint 목록

모든 사용자 기능 endpoint는 GET 방식이다.

| Endpoint | 기능 | 주요 parameter |
|---|---|---|
| `GET /health` | 프로세스 상태 | 없음 |
| `GET /status` | DB 개수 및 최신 scan | 없음 |
| `GET /files/search` | 파일명 부분 검색 | `q`, `limit` |
| `GET /folders/search` | 폴더명 부분 검색 | `q`, `limit` |
| `GET /folders/{folder_id}/children` | 직접/재귀 자식 | `recursive`, `limit` |
| `GET /folders/tree` | Folder Tree | `root_folder`, `max_depth`, `include_files` |
| `GET /revisions` | 기존 Revision 분류 | `min_revision`, `limit` |
| `GET /copies` | 기존 Copy 분류 | `limit` |
| `GET /auto-delete` | 기존 DELETE 분류 조회 | `limit` |
| `GET /groups` | 그룹 및 members | `min_members`, `limit` |
| `GET /recent` | 최근 수정 파일 | `limit` |

`limit`은 1~1,000 범위로 검증한다. 검색어가 공백뿐이거나 숫자 parameter 범위를 벗어나면 HTTP 422 JSON 응답을 반환한다.

## 6. 실행 및 로컬 URL

프로젝트 루트에서:

```powershell
python -m uvicorn api_server:app --host 127.0.0.1 --port 8000
```

주소:

- API root: `http://127.0.0.1:8000`
- Swagger UI: `http://127.0.0.1:8000/docs`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`
- ReDoc: `http://127.0.0.1:8000/redoc`

검증 서버는 `127.0.0.1:8000`에만 bind했고 검증 후 해당 Uvicorn 프로세스를 종료했다. 현재 외부 네트워크에는 공개되지 않는다.

## 7. 오류 응답

- 존재하지 않는 folder_id: HTTP 404와 간단한 JSON `detail`
- 존재하지 않는 Tree root: HTTP 404
- 잘못된 limit/query parameter: HTTP 422 JSON
- DB 파일 없음: HTTP 503, `SQLite index is unavailable.`
- DB schema 읽기 실패: HTTP 503, `SQLite index could not be read.`

DB 경로나 Python traceback이 포함된 HTML 500 페이지를 반환하지 않도록 처리했다. `/health`는 DB가 없어도 사용할 수 있다.

## 8. OpenAPI 및 문서

FastAPI 기본 OpenAPI를 유지했다.

실제 Uvicorn 검증:

- `/docs`: HTTP 200
- `/openapi.json`: HTTP 200
- OpenAPI paths: 11개
- 각 endpoint에 간결한 summary와 description 추가
- query parameter에 설명과 숫자 제약 추가
- JSON response model을 endpoint별로 정의

향후 GPTs Actions schema 설계 시 이 OpenAPI를 기반으로 사용할 수 있다. 이번 MVP에서는 GPTs Actions 자체를 생성하지 않았다.

## 9. 실제 DB `/status`

실제 응답 기준:

```json
{
  "files_count": 7924,
  "folders_count": 1141,
  "groups_count": 7898,
  "auto_delete_count": 87,
  "latest_scan_id": "SCAN-20260811-114701",
  "latest_scan_status": "COMPLETED"
}
```

현재 SQLite 값을 source of truth로 사용했다.

## 10. 실제 endpoint 검증

Uvicorn을 실제로 실행한 뒤 localhost HTTP 요청으로 검증했다.

| 요청 | HTTP | 핵심 결과 |
|---|---:|---|
| `/health` | 200 | `ok`, `python-drive-organizer` |
| `/status` | 200 | files 7,924 / folders 1,141 / groups 7,898 |
| `/files/search?q=송금확인증&limit=20` | 200 | total 2 / showing 2 |
| `/folders/search?q=안전서류&limit=20` | 200 | total 1 / showing 1 |
| `/folders/1jQ.../children?recursive=false` | 200 | total 29 / showing 29 |
| `/revisions?limit=100` | 200 | total 93 / showing 93 |
| `/copies?limit=100` | 200 | total 95 / showing 95 |
| `/auto-delete?limit=100` | 200 | total 87 / showing 87 |
| `/groups?min_members=2&limit=100` | 200 | total 25 / showing 25 |
| `/recent?limit=5` | 200 | total 7,924 / showing 5 |
| `/folders/tree?max_depth=2` | 200 | folders 50 / files 0 |
| `/folders/tree` | 200 | folders 1,141 / files 0 |
| `/openapi.json` | 200 | 11 paths |
| `/docs` | 200 | Swagger UI HTML |

Folder children 실제 검증 ID:

```text
1jQlwAxTH3bTmtAshRKsfzevyzeEYWfKm
```

Tree depth 수정 후 실제 DB에서 다음을 재확인했다.

- `max_depth=1`: 8 folders
- `max_depth=2`: 50 folders
- depth 제한 없음: 1,141 folders
- 정상 descendant가 cycle 영역으로 재출력되지 않음

## 11. Auto-delete 안전 응답

`GET /auto-delete`는 기존 `files.auto_action='DELETE'`만 조회한다.

응답에 반드시 포함:

```json
{
  "classification_only": true,
  "drive_action_executed": false
}
```

자동 테스트와 실제 DB HTTP 응답 모두 위 값을 확인했다. API에는 삭제 endpoint가 없으며 새로운 auto-delete 분류 규칙도 없다.

## 12. SQLite read-only 검증

API 요청 전후 다음 핵심 테이블 전체를 hash했다.

- `files`
- `folders`
- `scan_state`
- `file_groups`
- `file_group_members`

최종 코드와 실제 DB에서 13개 URL을 호출한 전후 통합 SHA-256:

```text
before: e8d26e7665f018f7b53935a9bb9e2bb403b77cacc076b744da55d6fa3f3f91a9
after:  e8d26e7665f018f7b53935a9bb9e2bb403b77cacc076b744da55d6fa3f3f91a9
```

API로 인한 핵심 테이블 행 변경은 0이다.

read-only connection에 강제로 UPDATE를 시도한 검증 결과:

```text
attempt to write a readonly database
```

## 13. 자동 테스트

API 테스트:

```powershell
python -m unittest -v test_api_server.py
```

- Project 1-2 MVP-01 API 테스트: 10개 통과

전체 회귀:

```powershell
python -m unittest -v `
  test_name_parser.py `
  test_file_grouping.py `
  test_search_service.py `
  test_api_server.py
```

- MVP-05 Parser: 12개 통과
- MVP-06 Grouping: 12개 통과
- MVP-07 Search/Tree: 15개 통과
- Local API: 10개 통과
- 합계: **49개 전체 통과**
- Python compile 검사: 통과
- `pip check`: 정상

API 자동 테스트 범위:

- 모든 요구 endpoint 정상 응답
- health/status 응답 필드
- 파일·폴더 검색 및 limit
- 직접·재귀 folder children
- 없는 folder 404
- Tree root/depth/files
- Revision, Copy, auto-delete, Group, Recent
- auto-delete 안전 boolean
- 잘못된 limit 및 빈 query 422
- 없는 DB와 읽을 수 없는 schema 503 JSON
- `/docs`, `/openapi.json`
- API 실행 전후 핵심 테이블 hash 동일
- Drive 인증 미호출

## 14. 기존 기능 회귀

### 일반 Drive 읽기 동기화

```powershell
python main.py
```

회귀 scan `SCAN-20260811-114701`:

- 상태: `COMPLETED`
- 파일: seen 7,924 / INSERT 0 / UPDATE 3 / SKIP 7,921 / DELETE 0
- 폴더: seen 1,141 / INSERT 0 / UPDATE 0 / SKIP 1,141 / DELETE 0

Drive의 최신 메타데이터 변경 3건을 정상 반영했다.

### Parser

```powershell
python main.py --parse-only
```

- Parser version: `MVP05-PARSER-1`
- Rows parsed: 0

### Grouping

```powershell
python main.py --group-only
```

- Files: 7,924
- Groups: 7,898
- Members: 7,924

### Search 및 Tree CLI

- `--search-name AUTODIM --limit 1`: 정상, total 1
- `--tree --max-depth 1`: 정상, folders 8
- 전체 Tree: folders 1,141개 유지
- 기존 Parser, Grouping, Search/Tree 테스트 전체 통과

## 15. Drive 안전성

- API server는 `authenticate()`를 import하거나 호출하지 않음
- API server는 Google Drive service를 생성하지 않음
- API server는 `files().list()`를 호출하지 않음
- API server는 SQLite SELECT 및 기존 read-only SearchService만 사용
- OAuth scope 변경 없음
- 기존 scope: `https://www.googleapis.com/auth/drive.metadata.readonly`
- Drive write API 호출 없음
- Drive 생성·삭제·이동·이름 변경·복사 없음
- Drive 동기화 API endpoint 없음

## 16. 다음 단계 인계

프로젝트 1-2 MVP-01은 localhost read-only API까지만 완료했다. 다음 단계 승인 전에는 다음을 진행하지 않는다.

- Cloudflare Tunnel
- 외부 HTTPS 공개
- GPTs Actions
- 사용자 로그인/JWT
- API를 통한 DB 변경 또는 Drive 동기화
- 실제 Drive 정리 실행

로컬 서버를 사용할 때는 계속 다음 bind를 유지한다.

```text
127.0.0.1:8000
```

`0.0.0.0` bind 및 외부 공개는 이번 MVP 범위 밖이다.
