# Python Drive Organizer — Project 1-2 MVP-04 준비보고

## 1. 준비 상태

- 단계: Project 1-2
- 챕터: MVP-04 GPTs Actions
- 상태: **PREPARED**
- 준비 완료일: 2026-08-14
- OpenAPI server: `https://drive-api.sungwony.pe.kr`
- API 성격: SQLite Google Drive metadata index read-only 조회

GPT Builder에 등록할 Action schema, Instructions 초안 및 자연어 테스트 시나리오를 준비하고 공개 HTTPS API와의 계약을 검증했다.

이번 단계의 지시에 따라 GPT Builder에 schema를 직접 등록하거나 GPTs Action을 실행하지 않았다. 따라서 `PREPARED`는 로컬 산출물과 공개 API 사전 검증이 끝났다는 뜻이며, ChatGPT 화면에서의 종단 테스트는 사용자 등록 후 별도 진행해야 한다.

## 2. 생성·수정 파일

### 생성

- `gpt_action_openapi.yaml`
  - GPT Actions 전용 OpenAPI 3.1 schema
  - 공개 HTTPS server 및 HTTP Bearer 인증 정의
  - 보호된 10개 read-only GET operation 정의
- `GPTS_INSTRUCTIONS.md`
  - Custom GPT Instructions에 사용할 초안
- `GPTS_ACTION_TEST_SCENARIOS.md`
  - 자연어 요청에서 예상 Action으로 이어지는 수동 종단 테스트 문서
- `Project-1-2_MVP-04_준비보고.md`
  - 현재 준비 결과 보고서

### 수정

- `README.md`
  - GPTs Actions 준비 파일과 read-only 범위 안내 추가

### 수정하지 않음

- `api_server.py`
- 기존 SQLite/Parser/Grouping/Search 구현
- Google Drive API 및 OAuth 코드
- Cloudflare Tunnel과 Windows 자동 시작 설정
- `requirements.txt`

현재 FastAPI의 endpoint, parameter 및 JSON response가 GPT Action 요구를 충족해 API 코드 변경은 필요하지 않았다.

## 3. OpenAPI 기본 설정

```yaml
openapi: 3.1.0
servers:
  - url: https://drive-api.sungwony.pe.kr
security:
  - BearerAuth: []
```

인증 schema:

```yaml
BearerAuth:
  type: http
  scheme: bearer
  bearerFormat: API key
```

실제 `PDO_API_KEY` 값은 schema에 포함하지 않았다. GPT Builder의 Authentication 화면에서 별도로 설정하는 구조다.

## 4. operationId 및 endpoint

| operationId | Method | Endpoint |
|---|---|---|
| `getDriveStatus` | GET | `/status` |
| `searchFiles` | GET | `/files/search` |
| `searchFolders` | GET | `/folders/search` |
| `listFolderChildren` | GET | `/folders/{folder_id}/children` |
| `getFolderTree` | GET | `/folders/tree` |
| `listRevisions` | GET | `/revisions` |
| `listCopies` | GET | `/copies` |
| `listAutoDeleteFiles` | GET | `/auto-delete` |
| `listFileGroups` | GET | `/groups` |
| `listRecentFiles` | GET | `/recent` |

- operation 수: 10
- operationId 중복: 0
- 모든 operation: GET
- POST/PUT/PATCH/DELETE: 0
- `/health`: 인증 없는 운영 확인용 endpoint이므로 Action schema에서는 제외

## 5. Parameter 계약

공개 FastAPI `/openapi.json`과 다음 항목을 자동 비교했다.

- parameter 이름
- path/query 위치
- required 여부
- 타입
- 기본값
- minimum/maximum

주요 계약:

| Parameter | 계약 |
|---|---|
| `q` | string, 검색 endpoint에서 required |
| `limit` | integer, 1~1000 |
| `folder_id` | string path parameter, required |
| `recursive` | boolean, default `false` |
| `root_folder` | optional string |
| `max_depth` | optional integer, minimum 0 |
| `include_files` | boolean, default `false` |
| `min_revision` | optional integer, minimum 0 |
| `min_members` | integer, minimum 1, default 1 |

`limit` 기본값은 `/recent`에서 20이고 나머지 목록 endpoint에서 100이다. 실제 API에 없는 parameter, offset 또는 page token은 추가하지 않았다.

## 6. Response schema

현재 Pydantic/FastAPI 응답과 맞춰 구체적인 schema를 정의했다.

- `StatusResponse`
- `FileSearchResponse` / `FileItem`
- `FolderSearchResponse` / `FolderItem`
- `FolderChildrenResponse` / `ChildItem`
- `TreeResponse`
- `RevisionResponse` / `RevisionItem`
- `CopyResponse` / `CopyItem`
- `AutoDeleteResponse`
- `GroupResponse` / `GroupItem` / `GroupMember`
- `RecentResponse` / `RecentItem`
- 오류 응답 schema

다음 중요 필드를 실제 응답 기준으로 포함했다.

- `total`, `showing`, `items`
- `classification_only`, `drive_action_executed`
- `folder_count`, `file_count`, `tree_text`
- 파일·폴더·그룹 ID와 path
- Parser의 Revision/Copy 분류값
- Grouping의 멤버 및 집계값

nullable 필드와 Pydantic에서 optional default가 있는 `ChildItem` 필드의 required 여부도 실제 공개 OpenAPI에 맞췄다.

## 7. endpoint 설명 원칙

각 operation 설명에는 다음을 명시했다.

- SQLite Google Drive metadata index를 조회함
- API 요청 중 Google Drive를 live 조회하지 않음
- 파일 내용을 검색하거나 읽지 않음
- Revision/Copy/Group은 이미 저장된 Parser/Grouping 결과임
- `auto_action=DELETE`는 분류값일 뿐 실제 삭제가 아님
- Drive create/update/move/copy/trash/delete를 실행하지 않음

GPT가 현재 Drive 내용을 직접 읽는 API로 오해하지 않도록 설명을 작성했다.

## 8. OpenAPI 구조 검증

프로젝트 의존성을 늘리지 않기 위해 PyYAML 6.0.2와 `openapi-spec-validator` 0.7.2를 작업공간 임시 폴더에만 설치해 검증하고 해당 폴더를 제거했다. `requirements.txt`는 변경하지 않았다.

검증 결과:

```text
YAML_PARSE=PASS
OPENAPI_3_1_VALIDATION=PASS
OPERATIONS=10
GET_ONLY=PASS
HTTPS_SERVER=PASS
HTTP_BEARER=PASS
SECRET_PATTERN_CHECK=PASS
```

- valid YAML: 통과
- valid OpenAPI 3.1: 통과
- duplicate operationId: 없음
- HTTPS server: 정확히 일치
- HTTP Bearer security: 통과
- GET-only: 통과
- secret 형식 또는 실제 API key 포함: 없음

## 9. 공개 API 실제 일치 검증

정상 Bearer 인증을 `.env`에서 메모리로만 읽어 공개 API를 호출했다. key 값은 명령 출력이나 보고서에 기록하지 않았다.

검증 결과:

```text
LIVE_PATH_SET_MATCH=PASS
LIVE_PARAMETER_CONTRACT_MATCH=PASS
LIVE_RESPONSE_SCHEMA_VALIDATION=PASS
VALIDATED_RESPONSES=10
PUBLIC_HEALTH=HTTP200
PUBLIC_STATUS_NO_KEY=HTTP401
ACTUAL_SECRET_ABSENT_FROM_ARTIFACTS=PASS
```

10개 Action endpoint 결과:

| Endpoint | 정상 Bearer | Response schema |
|---|---:|---|
| `/status` | 200 | 통과 |
| `/files/search` | 200 | 통과 |
| `/folders/search` | 200 | 통과 |
| `/folders/{folder_id}/children` | 200 | 통과 |
| `/folders/tree` | 200 | 통과 |
| `/revisions` | 200 | 통과 |
| `/copies` | 200 | 통과 |
| `/auto-delete` | 200 | 통과 |
| `/groups` | 200 | 통과 |
| `/recent` | 200 | 통과 |

인증 없는 `/status`는 HTTP 401, 공개 `/health`는 HTTP 200을 반환했다.

## 10. SQLite read-only 확인

실제 공개 API 10개 endpoint 호출 전후 `data/drive_index.db` SHA-256:

```text
before: 857dfc31d511fb3a9de16e9f84beaec60dda776ee42c0ae594c1623feac0cc21
after:  857dfc31d511fb3a9de16e9f84beaec60dda776ee42c0ae594c1623feac0cc21
```

해시가 동일하므로 Action schema 검증 요청으로 인한 DB 변경은 0건이다. 기존 API는 계속 SQLite `mode=ro`를 사용한다.

## 11. GPT Instructions 요약

`GPTS_INSTRUCTIONS.md`에는 다음 원칙을 담았다.

- 기억이나 추측 대신 Action 결과 사용
- SQLite Drive Index/API를 source of truth로 사용
- 응답에 없는 이름·ID·path·개수 생성 금지
- 결과 0건을 그대로 보고
- `total`과 `showing`이 다르면 일부 표시임을 명시
- “전부” 요청 시 최대 1000 범위에서 limit 조정
- offset/page token이 없어 1000건 초과 전체 조회가 불가능함을 정직하게 안내
- `auto_action=DELETE`는 분류이며 실제 삭제가 아님
- Revision/Copy/Group 결과 재해석 금지
- 파일 내용 읽기 불가
- Folder Tree는 SQLite metadata 기반
- Drive 쓰기 작업 불가
- 401/404/422/503 처리 원칙

## 12. 자연어 Action 테스트 시나리오

`GPTS_ACTION_TEST_SCENARIOS.md`에 다음 시나리오를 작성했다.

1. 인덱스 상태 조회
2. 파일명 부분 검색
3. 폴더명 부분 검색
4. 이전 결과의 folder_id를 이용한 recursive children 조회
5. 전체 폴더 Tree
6. 특정 폴더의 depth 제한 및 파일 포함 Tree
7. DELETE 분류 조회
8. 최소 Revision 번호 조회
9. Copy 분류 조회
10. 최근 변경 파일 N개
11. 최소 멤버 수가 있는 파일 그룹
12. `total > showing` 일부 표시
13. 검색 결과 0건
14. 금지된 Drive 쓰기 요청 거절
15. Bearer 인증 실패

각 시나리오에 자연어 입력, 예상 operation/parameter 및 성공 기준을 기록했다.

## 13. 기존 API 회귀 결과

```powershell
.\.venv\Scripts\python.exe -m unittest -v `
  test_name_parser.py `
  test_file_grouping.py `
  test_search_service.py `
  test_api_server.py
```

| 영역 | 테스트 수 | 결과 |
|---|---:|---|
| MVP-05 Parser | 12 | 통과 |
| MVP-06 Grouping | 12 | 통과 |
| MVP-07 Search/Tree | 15 | 통과 |
| Project 1-2 API/Auth | 12 | 통과 |
| 합계 | **51** | **전체 통과** |

추가 검사:

- Python `py_compile`: 통과
- `pip check`: `No broken requirements found.`
- `git diff --check`: 오류 없음
- 기존 FastAPI 코드 변경: 없음

테스트 중 확인된 `StarletteDeprecationWarning`은 기존 TestClient 관련 경고이며 실패가 아니다. 이번 문서 중심 MVP의 범위 밖이므로 의존성 변경은 하지 않았다.

## 14. Drive write 금지 확인

Action schema에 다음 HTTP method가 없다.

- POST
- PUT
- PATCH
- DELETE

다음 Drive mutation operation도 없다.

- trash
- rename
- move
- copy 실행
- create
- update
- delete

유지한 OAuth scope:

```text
https://www.googleapis.com/auth/drive.metadata.readonly
```

이번 검증에서 Google Drive 파일이나 폴더를 생성, 수정, 이동, 복사, 이름 변경, 삭제 또는 휴지통 이동하지 않았다.

## 15. 사용자 후속 단계

다음 단계는 사용자가 ChatGPT 화면에서 직접 수행해야 한다.

1. GPT Builder에 `gpt_action_openapi.yaml` 등록
2. Authentication을 HTTP Bearer/API key 방식으로 설정
3. 실제 key는 Builder의 Authentication에만 입력
4. `GPTS_INSTRUCTIONS.md` 내용을 GPT Instructions에 반영
5. `GPTS_ACTION_TEST_SCENARIOS.md`를 따라 종단 테스트

사용자 등록과 실제 GPT Action 종단 테스트가 끝나기 전에는 GPT 연결 완료로 판단하지 않는다.
