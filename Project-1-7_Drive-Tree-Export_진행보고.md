# Project 1-7 — Drive Tree Export 진행보고

- 작성일: 2026-08-18 (Asia/Seoul)
- 상태: 구현 및 서버 반영 완료
- 데이터 기준: `data/drive_index.db`의 마지막 SQLite snapshot
- Google Drive API 직접 호출: 없음
- Google Drive 쓰기: 없음

## 1. 목적

SQLite Drive Index에 저장된 전체 폴더·파일 구조를 누락 없이 deterministic Tree로 구성하고, 화면 조회용 pagination과 서버 생성형 TXT/DOCX/XLSX Export를 제공한다. 수천 개 항목을 GPT가 페이지별로 받아 문서를 직접 조립하지 않고 Python 서버가 한 SQLite snapshot 안에서 완성한다.

## 2. 최종 구조

```text
GPT / API Client
  └─ HTTPS + BearerAuth
      └─ Cloudflare Tunnel
          └─ FastAPI 127.0.0.1:8000
              ├─ POST /folders/tree/page
              ├─ POST /exports/drive-tree
              ├─ GET  /exports/{export_id}              (기존 raw binary)
              └─ POST /exports/{export_id}/openai-file  (GPT conversation file)
                   ├─ SQLite read-only: data/drive_index.db
                   └─ Local output: exports/
```

Tree 생성 계층은 `search_service.py`의 기존 탐색 로직을 `build_full_tree()`로 공통화해 CLI Tree, pagination, 문서 Export가 같은 순서와 결과를 사용한다. `tree_export_service.py`에는 snapshot 조회, cursor, 문서 생성, 다운로드 파일 확인, 최소 감사 로그만 분리했다.

## 3. 구현 내용

### 전체 Tree 및 정렬

- 폴더 우선, 파일 후순위
- 각 그룹은 대소문자 비의존 name 정렬 후 ID tie-break
- 동일 DB snapshot과 옵션이면 동일한 순서
- missing parent 및 cycle/unresolved 구조를 기존 Tree 규칙으로 보존
- 깊은 Tree도 Python 재귀 호출 없이 처리
- 외부 item: `node_type`, `name`, `level`, `path`, `parent_id`, `id`, `mime_type`, `modified_time`, `extension`

### Pagination

- Endpoint: `POST /folders/tree/page`
- 옵션: `root_folder`, `include_files`, `max_depth`, `page_size`, `cursor`
- `page_size` 기본 500, 허용 범위 1~1000
- response: `total_nodes`, `showing`, `next_cursor`, `has_more`, `items`, snapshot 정보
- cursor는 API key 기반 HMAC 서명 opaque 값이며 offset, 조회 옵션 hash, snapshot hash를 포함
- cursor 변조·다른 옵션 재사용은 400, snapshot 변경은 409로 거부
- 마지막 페이지는 `has_more=false`, `next_cursor=null`

### Export

- Endpoint: `POST /exports/drive-tree`
- 형식: UTF-8 TXT, Word DOCX, Excel XLSX
- 전체 Drive 또는 `root_folder` subtree 지원
- `include_files=false`와 `max_depth` 지원
- 파일명에 시각과 32자 URL-safe 난수 export ID를 사용해 충돌과 추측을 방지
- 각 문서 상단에 생성일, latest scan ID/status, scan finished time, indexed 폴더/파일 수, “실시간 Drive 조회가 아닌 SQLite snapshot” 문구 포함
- XLSX sheet: `Drive Tree`; AutoFilter `A10:H...`; Freeze Pane `A11`

### 인증 다운로드 및 GPT 파일 반환

- Endpoint: `GET /exports/{export_id}`
- 기존 API client와 브라우저용 raw binary 동작 유지
- GPT 전용 Endpoint: `POST /exports/{export_id}/openai-file`
- GPT operationId: `returnDriveTreeExport`
- 기존 Bearer 인증 필수
- API key를 URL query에 넣지 않음
- 서버 로컬 경로를 GPT에 노출하지 않고 exact `export_id` 사용
- GPT 전용 응답은 `openaiFileResponse` 배열의 `name`, `mime_type`, base64 `content` 구조
- 원본 bytes가 10,000,000 bytes를 초과하면 base64 변환 전에 413 `GPT_FILE_TOO_LARGE`
- 무인증 요청은 401
- Tree 조회·생성·GPT 반환 endpoint는 `x-openai-isConsequential: false`

### 로그와 보안

- `exports/`는 `.gitignore` 대상
- 생성 로그는 `export_id`, `format`, `node_count`, `status`, timestamp만 기록
- GPT 반환 로그는 `export_id`, `format`, `byte_size`, `status`, timestamp만 기록
- base64 및 파일 내용은 로그에 기록하지 않음
- 폴더명, 파일명, 전체 Tree 내용은 로그에 기록하지 않음
- API key, OAuth token, Cloudflare token 등 secret을 코드·schema·보고서에 기록하지 않음

## 4. 실제 SQLite 전체 Drive 검증

마지막 scan 정보:

- latest_scan_id: `SCAN-20260817-132427`
- status: `COMPLETED`
- finished_at: `2026-08-17T04:24:50Z`

전체 인덱스 및 Export 결과:

| 항목 | SQLite 수 | Tree/Export 수 | 누락 | 중복 |
|---|---:|---:|---:|---:|
| 폴더 | 2,394 | 2,394 | 0 | 0 |
| 파일 | 8,812 | 8,812 | 0 | 0 |
| 합계 | 11,206 | 11,206 | 0 | 0 |

Pagination 검증:

- page size 500
- 총 23페이지
- 수집 node 11,206
- unique ID 11,206
- 누락 0, 중복 0
- 원본 full tree와 페이지 결합 순서 완전 일치
- 마지막 cursor 처리 정상

## 5. 생성 문서 검증

실제 전체 Drive snapshot으로 다음 파일을 생성했다.

- TXT: `exports/drive_tree_20260818_073745_ipBIOYabhYRIMopkUY0pAQs9iNIZ1L9L.txt`
- DOCX: `exports/drive_tree_20260818_073745_1bzFldVQALgITFLqfCVxJAreVXGBoObZ.docx`
- XLSX: `exports/drive_tree_20260818_073748_ZEsSoH89AsaE1G2uQ9prolNOGxkZlaH-.xlsx`

검증 결과:

- TXT UTF-8 읽기 및 snapshot 표기 정상
- DOCX 11,206개 Tree 항목 생성, `python-docx` 재개방 정상
- Microsoft Word에서 읽기 전용 실제 개방 성공(문단 11,215)
- XLSX 11,206개 데이터 행, ID 중복 0, Tree 순서 일치
- Microsoft Excel에서 읽기 전용 실제 개방 성공(사용 영역 11,216행)
- artifact-tool 구조 검사·상단/하단 렌더링·수식 오류 검사 정상
- 별도 LibreOffice 렌더러는 PC에 `soffice`가 없어 실행하지 못했으나 Word 자체 개방과 구조 검증으로 대체

## 6. API 및 GPT Action 검증

- OpenAPI 3.1 표준 validation 통과
- YAML parse 통과
- operationId 21개, 중복 0
- HTTPS server: `https://drive-api.sungwony.pe.kr`
- BearerAuth 유지
- 새 operation:
  - `getDriveTreePage`
  - `exportDriveTree`
  - `returnDriveTreeExport`
- raw binary `downloadDriveTreeExport`는 GPT schema에서 제거하되 서버 GET endpoint는 유지
- `OpenAIFileResponse` 및 `OpenAIFileItem` schema의 `content`는 `format: byte`
- GPT Instructions 7,329자: GPT Builder 8,000자 제한 충족
- GPT는 화면 조회 시 exact `next_cursor`를 이어 사용하고, Export 요청 시 페이지를 조립하지 않고 서버 Export Action을 사용하도록 명시
- 문서 생성 흐름은 `exportDriveTree` → exact `export_id` → `returnDriveTreeExport`

실행 서비스 반영:

- FastAPI 자동 시작 작업 재시작 완료
- local OpenAPI version: `1.7-MVP01.1`
- Cloudflare public OpenAPI version: `1.7-MVP01.1`
- 공개 HTTPS 종단 결과:
  - pagination: 200
  - export 생성: 200
  - 기존 raw Bearer 다운로드: 200
  - TXT `openaiFileResponse`: 원본 bytes·파일명·MIME 일치
  - DOCX `openaiFileResponse`: 원본 bytes·파일명·MIME 일치
  - XLSX `openaiFileResponse`: 원본 bytes·파일명·MIME 일치
  - GPT 반환 endpoint 무인증: 401

GPT Builder에 갱신된 schema와 instructions를 반영한 뒤 실제 대화에서 Word Tree 문서가 conversation file로 표시되고 사용자가 정상 다운로드할 수 있음을 확인했다. 서버, 공개 Action, GPT Builder를 연결한 전체 종단 검증을 완료했다.

## 7. Read-only 및 회귀 검증

- Export 전후 `data/drive_index.db` SHA-256 동일
- Tree/Export 계층의 Google API client 참조 0
- Drive API 호출 0, Drive write 0
- 로컬 파일 생성은 `exports/`에만 수행
- 다른 working directory에서도 기본 DB/export 경로 정상
- `pip check`: 이상 없음
- Python compile 검사: 통과
- 전체 unit/integration regression: **198 tests, 모두 통과**
- 기존 indexing, Daily Refresh, Parser, Grouping, Search, GPT Actions, Enhanced Email, Contacts 테스트 통과

## 8. 생성·수정 파일

주요 생성:

- `tree_export_service.py`
- `test_tree_export_service.py`
- `Project-1-7_Drive-Tree-Export_진행보고.md`

주요 수정:

- `search_service.py`
- `api_server.py`
- `gpt_action_openapi.yaml`
- `GPTS_INSTRUCTIONS.md`
- `requirements.txt`
- `.gitignore`
- `README.md`
- `setup_windows.ps1`
- `verify_install.ps1`
- `test_api_server.py`
- `test_gpt_action_contract.py`
- `test_migration_setup.py`

추가 패키지:

- `python-docx`
- `openpyxl`

SQLite 자체는 Python 표준 `sqlite3`를 계속 사용한다.

## 9. 현재 제한 및 다음 확인

- 결과는 Google Drive 실시간 상태가 아니라 마지막 SQLite scan snapshot 기준이다.
- 서버가 생성한 export 파일의 보존기간·자동 정리는 이번 범위에 포함하지 않았다.
- 전체 Tree는 민감 정보이므로 공개 무인증 링크를 제공하지 않는다.
- GPT Actions 반환 파일은 개별 10MB 제한이며 초과 시 `GPT_FILE_TOO_LARGE`를 반환한다.
- GPT Builder conversation file 전달과 사용자 다운로드까지 실제 확인 완료했다.
- 이후 확장 시에도 pagination은 화면 조회용, Export는 서버 생성용 경계를 유지한다.

## 10. 완료 판정

Project 1-7 Drive Tree Export의 Python 구현, 실제 전체 DB 검증, 3종 문서 생성, raw 인증 다운로드 유지, GPT `openaiFileResponse`, Cloudflare 공개 API 반영, OpenAPI/GPT 계약, 전체 회귀 테스트를 완료했다. Google Drive 데이터나 permission은 변경하지 않았다.

## 11. GPT 파일 반환 호환성 수정

초기 구현의 raw binary GET은 일반 API client에서는 정상이나 GPT Actions conversation file 반환 계약에는 맞지 않았다. OpenAI 공식 문서의 반환 규격에 따라 GPT 전용 JSON endpoint를 추가했고, GPT schema에서는 raw GET operation을 새 `returnDriveTreeExport`로 교체했다.

- 공식 기준: [Sending and returning files with GPT Actions](https://developers.openai.com/api/docs/actions/sending-files)
- 기존 raw endpoint 제거 없음
- GPT 전용 반환은 정확히 1개 파일의 `openaiFileResponse` 배열
- TXT/DOCX/XLSX base64 decode 후 원본 bytes 완전 일치
- unknown export ID, traversal, 10MB 초과, 무인증, 로그 비노출 테스트 통과

## 12. 실제 GPT Builder 최종 결과

사용자가 GPT Builder에 최신 `gpt_action_openapi.yaml`과 `GPTS_INSTRUCTIONS.md`를 반영한 후 실제 GPT 대화에서 테스트했다.

- `exportDriveTree` 성공
- 서버 DOCX 생성 성공
- exact `export_id`가 `returnDriveTreeExport`에 전달됨
- `openaiFileResponse` 처리 성공
- GPT 대화창에 실제 DOCX 첨부파일 표시 성공
- 사용자 다운로드 성공

따라서 Project 1-7의 최종 사용자 시나리오인 “전체 드라이브 구조를 Word로 만들어줘”가 서버 생성부터 GPT 파일 반환 및 사용자 다운로드까지 정상 동작한다.
