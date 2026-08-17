# Project 1-6 — Contact Directory MVP-03 진행보고

## 1. 완료 상태

- 단계: Project 1-6 — Contact Directory
- 챕터: MVP-03 Contacts Search API
- 상태: **완료**
- 완료일: 2026-08-17 (Asia/Seoul)
- FastAPI version: `1.6-MVP03`

SQLite `contacts` current-state를 기존 FastAPI에서 읽기 전용으로 검색하고, exact contact ID와 최근 Contacts sync 상태를 조회할 수 있도록 구현했다.

이번 MVP에서는 GPT Action schema, GPT Instructions, 이메일 To/CC 자동 연결, 주소록 refresh Action을 변경하거나 추가하지 않았다.

## 2. 생성·수정 파일

### 생성

- `contacts_service.py`: Contacts 정규화, 검색 ranking, exact 조회, sync 상태 조회
- `test_contacts_service.py`: Contacts service/API 전용 테스트
- `Project-1-6_MVP-03_진행보고.md`: 본 보고서

### 수정

- `api_server.py`: Contacts request/response model과 endpoint 3개 추가
- `test_api_server.py`: OpenAPI, Bearer 보호, read-only 회귀 범위에 Contacts 추가

수정하지 않은 주요 파일:

- `gpt_action_openapi.yaml`
- `GPTS_INSTRUCTIONS.md`
- Google Sheets 동기화 및 이메일 발송 로직

## 3. 추가 endpoint

| Method | Path | 역할 | 인증 |
|---|---|---|---|
| POST | `/contacts/search` | SQLite contacts 검색 | Bearer 필수 |
| GET | `/contacts/status` | 최신 Contacts sync 상태 | Bearer 필수 |
| GET | `/contacts/{contact_id}` | exact opaque ID 조회 | Bearer 필수 |

기존 `/health`만 public으로 유지했다.

## 4. POST `/contacts/search`

개인정보 검색값이 URL query string이나 access log URL에 남지 않도록 POST JSON body를 사용한다.

### Request schema

```json
{
  "q": null,
  "organization": null,
  "name": null,
  "title": null,
  "email": null,
  "limit": 20
}
```

규칙:

- `q` 또는 하나 이상의 exact filter 필수
- 모든 조건이 null 또는 공백이면 HTTP 400
- `q`와 exact filter는 `AND`
- `limit` 기본 20, 최소 1, 최대 100
- limit 형식·범위 오류는 HTTP 422

### Response schema

```json
{
  "total": 1,
  "showing": 1,
  "items": [
    {
      "contact_id": "CONTACT-...",
      "organization": "...",
      "name": "...",
      "title": "...",
      "email": "...",
      "phone": "...",
      "email_usable": true,
      "conflict_code": null
    }
  ]
}
```

실제 연락처 값은 예시와 보고서에 기록하지 않았다.

## 5. 검색 정규화와 ranking

`q`는 다음 SQLite normalized 필드의 contains 검색에 사용한다.

- `normalized_name`
- `normalized_organization`
- `normalized_title`
- `normalized_email`

일반 검색 입력은 NFKC, 연속 공백 축약, casefold를 적용한다. Email exact filter는 trim과 casefold를 적용해 기존 `normalized_email` 계약과 맞췄다.

검색 우선순위:

1. `normalized_email` exact
2. `normalized_name` exact
3. `normalized_name` prefix
4. 나머지 field contains

동일 ranking에서는 다음 순서로 deterministic 정렬한다.

1. name
2. organization
3. title
4. contact_id

한글 이름 exact/prefix/contains, 이메일 대소문자 무시, 소속·직급 exact, `q + filter` AND 조합을 자동 테스트로 검증했다.

## 6. 동명이인과 충돌 연락처

- 동명이인은 모두 반환한다.
- API가 첫 번째 결과를 자동 선택하지 않는다.
- 각 결과에 opaque `contact_id`를 포함한다.
- `email_usable=false` 연락처도 검색과 상세 조회에서 숨기지 않는다.
- `conflict_code`를 그대로 반환해 향후 발송 가능 여부를 판단할 수 있게 한다.
- 이름을 contact ID로 간주하는 fallback은 없다.

실제 이메일 발송이나 수신자 선택은 이번 MVP에서 수행하지 않는다.

## 7. GET `/contacts/{contact_id}`

`contact_id`의 exact 일치만 조회한다.

- 존재: HTTP 200과 `ContactItem`
- 미존재: HTTP 404 `CONTACT_NOT_FOUND`
- 이름 검색 fallback: 없음
- 유사 ID 자동 선택: 없음

향후 이메일 발송 전 선택된 연락처를 exact ID로 재확인할 수 있는 계약이다.

## 8. GET `/contacts/status`

가장 최근 `contacts_sync_state`의 상태와 통계를 반환한다.

```json
{
  "latest_sync_id": "CONTACTS-...",
  "latest_sync_status": "COMPLETED",
  "last_success_at": "...",
  "rows_seen": 16,
  "valid_rows": 16,
  "inserted": 0,
  "updated": 0,
  "deleted": 0,
  "unchanged": 16,
  "invalid": 0,
  "conflicts": 0
}
```

동작:

- 최신 상태가 `COMPLETED_WITH_WARNINGS`여도 성공 시각으로 인정
- 최신 상태가 `FAILED`이면 latest 상태는 FAILED로 표시
- FAILED 이후에도 `last_success_at`은 마지막 성공 완료 시각 유지
- 성공 기록이 없으면 `last_success_at=null`
- sync 기록이 전혀 없으면 ID/status/time은 null, 통계는 0

## 9. Route 충돌 방지

정적 route인 `/contacts/status`를 동적 `/contacts/{contact_id}`보다 먼저 정의했다.

OpenAPI와 실제 HTTP 호출에서 `/contacts/status`가 `contact_id="status"`로 처리되지 않고 `ContactsStatusResponse`를 반환함을 확인했다.

## 10. 외부 응답 제외 필드

다음 내부 필드는 검색과 상세 응답에 포함하지 않는다.

- `source_spreadsheet_id`
- `source_sheet_id`
- `source_sheet_name`
- `source_row`
- `source_fingerprint`
- 모든 `normalized_*`
- `last_seen_sync_id`
- `synced_at`, `created_at`, `updated_at`

자동 테스트와 실제 localhost 응답에서 공개 필드만 반환됨을 확인했다.

## 11. Bearer 인증

세 endpoint 모두 기존 `protected_router`에 등록해 `PDO_API_KEY` Bearer 인증을 그대로 사용한다.

검증 결과:

- Authorization 없음 → 401
- 잘못된 Bearer 값 → 401
- 정상 Bearer → 200
- API key 원문 응답·로그·보고서 노출 없음

## 12. SQLite read-only

Contacts endpoint는 기존 API의 공통 `get_connection()`을 사용한다.

연결 방식:

```text
SQLite URI mode=ro
```

`contacts_service.py`에는 `SELECT`만 존재하며 다음 SQL은 없다.

- INSERT
- UPDATE
- DELETE
- REPLACE
- ALTER
- DROP
- CREATE

Contacts 3개 endpoint 호출 전후 `contacts`, `contacts_sync_state`, `contacts_sync_issues`의 전체 DB hash를 비교했고 동일함을 확인했다.

## 13. Google Sheets 및 외부 서비스 격리

Contacts endpoint의 데이터 원본은 SQLite snapshot뿐이다.

- Google Sheets client 호출: 0건
- Contacts OAuth 호출: 0건
- Google Sheet read/write: 0건
- Gmail send: 0건
- Drive API 호출: 0건
- Drive permission 변경: 0건

테스트에서는 Sheets service factory가 호출되면 즉시 실패하도록 설정한 상태에서 세 endpoint가 모두 정상 동작함을 확인했다.

## 14. 개인정보 로그 정책

검색값은 POST body에만 존재하며 route URL에 포함되지 않는다.

일반 로그에 기록하지 않는 값:

- q
- name
- organization
- email
- phone
- 검색 결과 연락처 원문

예상하지 못한 SQL 오류는 기존 전역 SQLite handler가 HTTP 503의 일반 메시지로 변환하며 SQL문, DB 경로, 개인정보를 응답에 노출하지 않는다.

PII를 포함한 검색 payload를 사용한 테스트에서 해당 값이 Python log에 기록되지 않음을 확인했다.

## 15. 오류 정책

| HTTP | 상황 |
|---:|---|
| 400 | 검색 조건이 모두 비어 있음 |
| 401 | Bearer 인증 없음 또는 불일치 |
| 404 | exact contact ID 없음 |
| 422 | request schema 또는 limit 검증 실패 |
| 503 | SQLite unavailable 또는 읽기 실패 |

오류 응답에는 API key, 내부 SQL, DB 절대 경로, 연락처 원문을 포함하지 않는다.

## 16. 실제 localhost 검증

실제 `data/drive_index.db`의 contacts 16개를 대상으로 별도 검증 포트와 운영 localhost API에서 모두 HTTP 호출을 수행했다.

최종 운영 localhost 결과:

- `/health`: 200
- 미인증 `/contacts/status`: 401
- `POST /contacts/search`: 200
- 검색 결과 1개 이상 확인: 성공
- 결과 공개 필드 제한: 성공
- `GET /contacts/{contact_id}`: 200
- exact ID 일치: 성공
- unknown contact ID: 404
- `GET /contacts/status`: 200
- 최신 sync status: `COMPLETED`
- last success 존재: 확인
- 실제 contacts count: 16
- API 호출 전후 SQLite hash: 동일

검증에 사용한 검색어, contact ID, 이름, 이메일, 전화번호는 출력하거나 보고서에 기록하지 않았다.

## 17. 운영 API 반영

기본 포트의 기존 API 프로세스가 이전 코드를 유지하고 있어 처음에는 새 route가 404였다.

다음 안전 절차로 반영했다.

1. 포트 8000 listener가 프로젝트 `.venv`의 `uvicorn api_server:app`인지 확인
2. 확인된 해당 프로세스만 종료
3. 기존 `Python Drive Organizer API` 예약 작업으로 재시작
4. 새 listener 확인
5. Contacts 3개 endpoint 재검증

예약 작업 정의와 Cloudflare/GPT 설정은 변경하지 않았다. 운영 localhost API가 새 Contacts route를 제공하는 상태다.

## 18. 테스트 결과

Contacts 전용 테스트:

- 이름 exact/contains/prefix ranking
- 소속·직급·이메일 exact
- 이메일 case-insensitive
- `q + filters` AND
- 결과 0 및 limit
- deterministic ordering
- 동명이인 복수 반환
- `email_usable=false`와 conflict 표시
- exact ID 및 unknown ID 404
- 최신 COMPLETED / COMPLETED_WITH_WARNINGS
- 최근 FAILED 후 last success 유지
- 성공 기록 없음과 통계 정확성
- static `/contacts/status` route 우선
- 세 endpoint Bearer 인증
- SQLite unavailable 503 sanitization
- Sheets/OAuth 호출 0건
- SQLite write 0건
- 개인정보 로그 비포함

최종 결과:

- 전체 unit test: **160개 통과**
- Python 문법 검사: **통과**
- 실제 localhost HTTP 검증: **통과**
- 기존 Drive indexing, Daily Refresh, Parser, Grouping, Search, FastAPI, GPT Actions, Enhanced Email, Contacts Sync 회귀: **통과**

테스트 중 표시되는 Starlette/httpx deprecation warning은 기존 테스트 클라이언트 조합의 안내이며 테스트 실패가 아니다.

## 19. 보안 및 범위 확인

- Google Sheet write: 0건
- SQLite contacts/sync write: 0건
- Gmail send: 0건
- Drive 파일 write: 0건
- Drive permission 변경: 0건
- GPT Action schema 수정: 0건
- GPT Instructions 수정: 0건
- Spreadsheet ID, API key, OAuth token, 연락처 원문 보고서 포함: 0건

## 20. 완료 기준

- [x] SQLite contacts FastAPI 검색
- [x] exact contact ID 조회
- [x] Contacts sync status 조회
- [x] 모든 Contacts endpoint Bearer 보호
- [x] PII 로그 최소화
- [x] Google Sheets 호출 0건
- [x] SQLite write 0건
- [x] 전체 160개 회귀 테스트 통과
- [x] 운영 localhost API 반영 및 실제 호출 성공

Project 1-6 MVP-03 Contacts Search API 완료 조건을 모두 충족했다.
