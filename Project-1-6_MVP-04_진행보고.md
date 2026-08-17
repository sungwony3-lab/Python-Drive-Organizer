# Project 1-6 — Contact Directory MVP-04 진행보고

## 1. 완료 상태

- 단계: Project 1-6 — Contact Directory
- 챕터: MVP-04 GPT Action + Email Recipient Integration
- 상태: **완료**
- 완료일: 2026-08-17 (Asia/Seoul)
- GPT Action schema version: `1.6-MVP04`

사용자가 사람 이름·소속·직급으로 이메일 To/CC를 지정하면 GPT가 SQLite Contacts Action으로 후보를 검색하고, 선택한 exact `contact_id`를 Email Preview 직전에 재조회한 최신 이메일을 기존 Enhanced Email 흐름에 사용하도록 schema와 Instructions를 완성했다.

기존 `previewEmailWithFiles` → 사용자 명시적 승인 → exact `preview_id` → `sendEmailWithFiles` 구조와 발송·공유 정책은 변경하지 않았다.

## 2. 생성·수정 파일

### 수정

- `gpt_action_openapi.yaml`
  - Contacts Action 3개와 request/response schema 추가
  - schema version 및 설명 갱신
- `GPTS_INSTRUCTIONS.md`
  - Contact Directory 검색, 선택, exact 재조회 및 Email Preview 연결 규칙 추가

### 생성

- `test_gpt_action_contract.py`
  - operationId, consequential, server/Bearer, Contacts schema, Instructions 계약 테스트
- `Project-1-6_MVP-04_진행보고.md`
  - 본 완료 보고서

FastAPI, SQLite schema, Contacts sync, Enhanced Email Python 로직은 수정하지 않았다.

## 3. OpenAI GPT Action 기준

[OpenAI의 GPT Actions 공식 안내](https://help.openai.com/en/articles/9442513)에 따라 Action은 API 인증 설정과 JSON/YAML OpenAPI schema로 구성되며, schema의 endpoint와 operationId를 GPT가 사용할 Action 계약으로 제공한다.

이번 schema는 기존 GPT Builder 호환 정책을 그대로 적용했다.

- OpenAPI `3.1.0`
- YAML 형식
- HTTPS server 고정
- 기존 BearerAuth 사용
- operation parameter는 inline 정의
- `components.parameters` 미사용
- parameter `$ref` 미사용
- request/response object는 `components.schemas` `$ref` 유지
- operation description 300자 이하

실제 GPT Builder 웹 UI의 schema 붙여넣기와 Instructions 교체는 이번 Codex 작업에서 수행하지 않았다.

## 4. 추가 Contacts Action

| Method | Path | operationId | Consequential |
|---|---|---|---|
| POST | `/contacts/search` | `searchContacts` | false |
| GET | `/contacts/{contact_id}` | `getContact` | false |
| GET | `/contacts/status` | `getContactsStatus` | false |

세 Action은 모두 SQLite read-only 조회이며 Google Sheets refresh나 외부 변경을 수행하지 않는다.

## 5. 전체 operation 결과

- 전체 operation 수: **16**
- operationId 중복: **0**
- 기존 operationId 변경: **0**
- Contacts operation: 3개
- parameter `$ref`: 0개

기존 주요 이메일 operation 정책:

- `previewEmailWithFiles`: `x-openai-isConsequential=false`
- `sendEmailWithFiles`: `x-openai-isConsequential=true`
- `sendEmailWithAttachment`: `x-openai-isConsequential=true`

## 6. `searchContacts`

Request body:

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

계약:

- `q` 또는 exact filter 하나 이상 사용
- `q + filter`는 AND
- limit 기본 20
- limit 최소 1, 최대 100
- 결과에 `total`, `showing`, `items` 반환

GPT Instructions에서는 이름·소속·직급으로 사람을 지정한 경우 사람마다 이 Action을 독립 호출하도록 규정했다.

## 7. Contacts 응답 schema

`ContactItem` 공개 필드:

- `contact_id`
- `organization`
- `name`
- `title`
- `email`
- `phone`
- `email_usable`
- `conflict_code`

내부 source 좌표, fingerprint, normalized 필드와 sync 내부 값은 GPT Action schema에 노출하지 않는다.

GPT Action schema와 실제 FastAPI OpenAPI를 자동 비교한 결과:

- `ContactSearchRequest`: 6개 property 일치
- `ContactItem`: 8개 property 일치
- `ContactSearchResponse`: 3개 property 일치
- `ContactsStatusResponse`: 11개 property 일치
- Contacts search limit 계약: 일치

## 8. 이름 기반 To 흐름

사용자가 “홍길동 부장에게 보내줘”처럼 요청하면 다음 순서를 따른다.

1. `searchContacts`로 이름·직급 검색
2. 검색 결과 0/1/복수 처리
3. 후보가 확정되면 반환된 exact `contact_id` 보존
4. Email Preview 직전에 `getContact(contact_id)` 재조회
5. 최신 `email`, `email_usable`, `conflict_code` 확인
6. 최신 email을 Enhanced Email의 To로 사용
7. 파일·수신자·제목·본문·전송 방식 전체 Preview 표시
8. 사용자 명시적 승인 후에만 send

사람 이름 요청 자체는 발송 최종 승인으로 간주하지 않는다.

## 9. 이름 기반 CC 흐름

To와 각 CC를 서로 독립적으로 처리한다.

예를 들어 To 한 명과 CC 세 명이 이름으로 지정되면 네 사람 각각에 대해 다음을 수행한다.

```text
searchContacts
→ 후보 확정
→ exact contact_id 보존
→ Preview 직전 getContact
```

한 사람의 검색 결과나 contact ID를 다른 사람에게 재사용하지 않는다. 한 명이라도 동명이인 선택이 끝나지 않았거나 발송 불가 상태면 Preview를 진행하지 않는다.

기존 CC 최대 5명 정책은 유지했다.

## 10. 검색 결과 처리

### 0명

“주소록에서 해당 연락처를 찾지 못했습니다.”라고 보고한다.

- 유사 인물 추측 금지
- 비슷한 이름 자동 선택 금지
- 사용자가 직접 이메일 주소를 제공할 수 있다고 안내 가능

### 1명

다음 조건을 모두 만족할 때만 후보로 확정한다.

- `email_usable=true`
- `conflict_code=null`
- email 존재

단, Preview 직전에 exact `getContact` 재조회가 필수다.

### 2명 이상

첫 번째 후보 자동 선택을 금지했다. 사용자에게 필요한 최소 정보로 다음을 표시한다.

- 번호
- 성명
- 소속
- 직급
- 이메일

사용자가 “1번” 등으로 선택하면 해당 후보가 반환한 exact `contact_id`를 내부적으로 보존한다. contact ID를 생성·추측·변경하지 않는다.

## 11. 발송 차단 조건

다음 연락처는 자동 발송 대상으로 사용하지 않는다.

- `email_usable=false`
- email 없음
- `conflict_code`가 null이 아님
- Preview 직전 `getContact` 404
- 재조회 후 발송 가능 상태가 변경됨

삭제된 contact ID에 대해 과거 이메일을 재사용하지 않는다. 다시 검색하고 사용자가 후보를 확정해야 한다.

검색 이후 이메일이 변경됐다면 `getContact`의 최신 이메일을 기준으로 설명한다. 이미 사용자에게 수신자를 보여준 Preview와 달라지면 이전 승인은 무효이며 새 Preview와 새 승인이 필요하다.

## 12. 직접 입력 이메일과 혼합

사용자가 이메일 주소를 직접 제공하면 Contacts 검색을 강제하지 않는다.

- 직접 입력 주소: 기존 이메일 validation 후 사용
- 주소록 연락처: `searchContacts`와 `getContact` 필수
- 주소록 + 직접 입력 혼합: 허용

Preview에서는 출처를 구분한다.

- 받는 사람/참조 `(주소록)`: 성명, 소속, 직급, 이메일
- 받는 사람/참조 `(직접 입력)`: 이메일

전화번호는 조회·표시용일 뿐이며 이메일이 없다고 SMS, 전화 또는 메신저 발송으로 대체하지 않는다.

## 13. Contacts 최신성

“주소록 최신이야?” 또는 마지막 갱신 시각 질문에는 `getContactsStatus`를 호출한다.

- `latest_sync_status`
- `last_success_at`
- 필요 시 row 통계

주소록이 오래됐거나 사용자가 방금 Sheet를 수정했다고 말해도 GPT가 직접 refresh했다고 표현하지 않는다.

- 자동 갱신: 매일 오전 08:00
- 즉시 반영: 사용자가 로컬에서 `python contacts_sync.py` 실행 가능
- GPT refresh Action: 이번 MVP에 없음

## 14. 기존 Enhanced Email Preview 연동

주소록으로 수신자를 찾았더라도 기존 Enhanced Email 계약을 그대로 따른다.

- To 정확히 1명
- CC 최대 5명
- 파일 1~5개
- mode: auto / attachment / link
- Preview는 read-only
- 사용자 명시적 최종 승인 필수
- 승인된 동일 payload만 send
- idempotency 정책 유지

Preview에는 연락처 출처와 최신 수신자 정보 외에도 기존 정보를 전부 표시한다.

- 제목과 plain-text 본문
- 파일명, exact file ID, 순서, 개수
- 요청 mode와 `delivery_mode`
- 알려진 총 크기와 만료 시각
- LINK sharing mode와 파일별 permission 계획
- 정확한 Preview ID

## 15. Preview ID 규칙 유지

기존 `PREVIEW_NOT_FOUND` 방지 규칙을 변경하지 않았다.

- 직전 성공한 `previewEmailWithFiles`의 exact `preview_id` 보존
- send payload에 문자 단위로 그대로 사용
- ID 생성·추측·요약·재입력·일부 생략·변경 금지
- 확인할 수 없으면 새 Preview 생성 및 재승인
- To/CC 또는 연락처 이메일 변경 시 기존 승인 무효

## 16. LINK 보안 경고 유지

주소록 연락처를 사용해도 LINK mode 정책은 변경되지 않는다.

- 공유 방식: Anyone with the link / Viewer
- permission: `type=anyone`, `role=reader`, `allowFileDiscovery=false`
- 수신 이메일은 Drive permission 대상이 아님
- 사용자 명시적 승인 전 permission 생성 금지

다음 경고를 의미가 바뀌지 않게 유지한다.

> 이 파일은 링크를 가진 누구나 열람할 수 있도록 Google Drive 공유 설정이 변경됩니다. 메일을 받은 사람이 링크를 다른 사람에게 전달하면 그 사람도 파일을 열 수 있습니다.

## 17. Instructions 시나리오 반영

- 이름 기반 To → search → exact get → preview
- 동명이인 2명 이상 → 후보 표시 → 자동 선택 금지
- 이름 기반 To와 이름 기반 CC → 사람별 독립 search/get
- `email_usable=false` → 발송 금지
- conflict 존재 → 발송 금지
- 직접 이메일 → Contacts 검색 생략 가능
- 주소록 + 직접 입력 혼합 → 출처 구분
- contact 삭제 후 exact get 404 → 과거 이메일 금지
- contact 이메일 변경 → 최신 값 설명, 새 Preview/승인
- 주소록 상태 문의 → `getContactsStatus`

## 18. OpenAPI 검증 결과

- YAML parse: 통과
- OpenAPI 3.1 validation: 통과
- operation 수: 16
- duplicate operationId: 0
- Contacts operationId: 3개 확인
- Contacts response schema: 확인
- 실제 FastAPI schema property: 일치
- server URL: `https://drive-api.sungwony.pe.kr` 유지
- BearerAuth: 유지
- parameter `$ref`: 0개
- `components.parameters`: 없음
- 최대 operation description 길이: 275자
- Contacts consequential: 모두 false
- Preview consequential: false
- Enhanced/legacy send consequential: true

검증용 PyYAML/OpenAPI validator는 임시 격리 폴더에만 설치했으며 프로젝트 runtime requirements에는 추가하지 않았다.

## 19. 전체 회귀 테스트

추가한 GPT 계약 테스트:

- operationId 총 16개 및 중복 없음
- Contacts path/operationId 존재
- Contacts consequential false
- HTTPS server와 BearerAuth 유지
- parameter `$ref` 미사용
- Contacts 공개 schema와 limit
- 이름 검색·동명이인·exact 재조회 지침
- email_usable/conflict 차단 지침
- 직접 입력 및 혼합 지침
- Preview ID, LINK 경고, To/CC/file 제한 유지

최종 결과:

- 전체 unit test: **167개 통과**
- Python 문법 검사: **통과**
- 기존 Drive indexing, Daily Refresh, Parser, Grouping, Search, FastAPI, Enhanced Email, Contacts Sync, Contacts API 회귀: **통과**

## 20. 보안 및 실행 경계

- 실제 이메일 발송: 0건
- 실제 Drive permission 생성: 0건
- Google Drive 파일 write: 0건
- Google Sheet read/write: 0건
- SQLite write: 0건
- API key, OAuth token, Cloudflare token, Spreadsheet ID 기록: 0건
- 연락처 개인정보 보고서 기록: 0건
- GPT Builder 웹 UI 변경: 0건

## 21. 수동 반영 산출물

GPT Builder에는 다음 두 파일의 최종 내용을 수동 반영해야 한다.

1. `gpt_action_openapi.yaml` → Actions schema
2. `GPTS_INSTRUCTIONS.md` → GPT Instructions

실제 API key는 schema나 Instructions에 넣지 않고 GPT Builder의 별도 인증 설정에만 구성한다.

## 22. 완료 기준

- [x] GPT Action schema에 Contacts 조회 3개 추가
- [x] 이름 기반 연락처 검색 규칙 완성
- [x] exact contact ID 재조회
- [x] 동명이인 자동 선택 금지
- [x] email_usable/conflict 발송 차단
- [x] 기존 Enhanced Email Preview와 연결
- [x] 직접 입력 이메일 및 혼합 지원
- [x] OpenAPI validation 통과
- [x] 전체 167개 regression test 통과

Project 1-6 MVP-04 GPT Action + Email Recipient Integration 완료 조건을 모두 충족했다.

## 23. GPT Instructions 8,000자 제한 대응

GPT Builder의 Instructions 입력 제한에 맞춰 `GPTS_INSTRUCTIONS.md`를 핵심 계약 중심으로 압축했다.

- 기존 길이: 11,263자
- 조정 후 길이: 5,280자
- 보존 범위: Drive 조회, Contacts 검색·동명이인 선택·exact 재조회, Preview → 승인 → Send, exact Preview ID, ATTACHMENT/AUTO/LINK, Anyone-with-link 보안 경고, 오류·재시도 금지
- 향후 보호: `test_gpt_action_contract.py`에서 Instructions가 8,000자를 초과하면 실패하도록 자동 검증 추가
- 이번 조정에서 OpenAPI schema와 서버 코드는 변경하지 않음
