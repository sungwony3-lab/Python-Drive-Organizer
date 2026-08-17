# Project 1-6 MVP-04A Plain Email 진행보고

## 1. 작업 목적

주소록에서 찾은 수신자 또는 사용자가 직접 입력한 이메일 주소로 첨부파일과 Drive Link가 없는 일반 plain-text Gmail을 보낼 수 있도록 기존 이메일 기능의 누락을 보완했다.

기존 `sendEmailWithAttachment`, `previewEmailWithFiles`, `sendEmailWithFiles`의 파일 필수 계약은 변경하지 않고 별도 Plain Email endpoint를 추가했다.

## 2. 구현 상태

- Plain Email 코드·API·GPT Action·Instructions 구현: 완료
- 자동 테스트 및 OpenAPI 검증: 완료
- 실제 주소록 → exact contact → 로컬 HTTP application Preview 검증: 완료
- 공개 HTTPS Preview: 사용자 승인 후 완료
- 실제 Gmail 1건 발송: 사용자 최종 승인 후 1회 시도, `GMAIL_DELIVERY_UNCERTAIN`

## 3. 추가 endpoint

### POST `/email/send-text/preview`

- operationId: `previewTextEmail`
- `x-openai-isConsequential=false`
- To 1명, CC 최대 5명, 한 줄 제목, plain-text 본문 검증
- Gmail 호출 없음
- Drive API 및 Drive permission 호출 없음
- Google Sheets 호출 없음
- 10분 유효한 `preview_id` 반환

### POST `/email/send-text`

- operationId: `sendTextEmail`
- `x-openai-isConsequential=true`
- 직전 성공 Preview의 exact `preview_id` 요구
- 변경 없는 To/CC/제목/본문 요구
- `confirmed=true`와 `idempotency_key` 요구
- Gmail `users.messages.send`만 외부 변경으로 실행

## 4. 지원 범위

- To: 정확히 1명
- CC: 선택, 최대 5명
- BCC: 미지원
- Subject: 필수 한 줄
- Body: plain text
- Attachment: 없음
- Drive Link: 없음
- Drive permission 변경: 없음

## 5. Gmail 및 OAuth

- 기존 `gmail_client.py`의 `send_message` 재사용
- 기존 `gmail_send_token.json` 재사용
- OAuth scope: `https://www.googleapis.com/auth/gmail.send`
- 새 OAuth scope 추가 없음
- MIME은 `To`, 선택적 `Cc`, `Subject`, `set_content(body)`만 사용
- `add_attachment` 호출 없음

## 6. Preview 및 상태 저장

상태 DB:

`data/plain_email_state.db`

테이블:

- `plain_email_previews`
- `plain_email_sends`

Preview 상태에는 payload hash, 수신자 canonical 값, CC canonical 값, 만료시각만 저장한다. 제목과 본문 원문은 상태 DB에 저장하지 않는다.

감사 로그:

`logs/plain_email_send.log`

로그에는 상태, Preview ID, 마스킹된 To, CC 개수, 오류 코드만 기록하며 제목·본문·OAuth token·API key를 기록하지 않는다.

## 7. Preview ID 및 idempotency

- `previewTextEmail`이 반환한 exact `preview_id`를 문자 단위 그대로 `sendTextEmail`에 사용
- Preview ID 추측·재생성·요약·대체 금지
- payload 변경 시 `PREVIEW_STALE`
- 존재하지 않는 ID는 `PREVIEW_NOT_FOUND`
- 만료된 Preview는 `PREVIEW_EXPIRED`
- 동일 성공 요청의 동일 key 재호출은 Gmail을 재발송하지 않고 기존 결과 반환
- 다른 payload에 같은 key 사용 시 `IDEMPOTENCY_CONFLICT`
- 자동 재시도 금지

## 8. 주소록 연결

GPT Instructions에 다음 흐름을 추가했다.

`searchContacts` → 후보 확정 → `getContact` exact 재조회 → `previewTextEmail` → 사용자 승인 → `sendTextEmail`

- 동명이인 자동 선택 금지
- `email_usable=false` 또는 `conflict_code` 존재 시 발송 차단
- 직접 입력 이메일은 Contacts 검색 생략 가능
- 주소록 수신자와 직접 입력 CC 혼합 가능
- 파일 없는 일반 메일에 가짜 또는 빈 `file_id` 생성 금지

## 9. 기존 기능 보존

- Drive 파일 1개 이상: 기존 `previewEmailWithFiles` → `sendEmailWithFiles`
- 기존 단일 attachment 호환: `sendEmailWithAttachment`
- Enhanced ATTACHMENT/AUTO/LINK 계약 변경 없음
- Anyone-with-link 공유 정책 변경 없음
- 기존 Drive 파일 쓰기 금지 정책 변경 없음

## 10. OpenAPI 결과

- YAML parse: 통과
- OpenAPI 3.1 validation: 통과
- 전체 operationId: 18개
- duplicate operationId: 0개
- `previewTextEmail`: consequential false
- `sendTextEmail`: consequential true
- HTTPS server 유지: `https://drive-api.sungwony.pe.kr`
- BearerAuth 유지
- operation parameter `$ref` 미사용 유지
- operation description 최대 300자 제한 통과
- FastAPI 예약 작업 재시작 후 localhost health: `ok`
- 공개 HTTPS health: `ok`
- localhost/공개 OpenAPI version: `1.6-MVP04A`
- localhost/공개 OpenAPI에서 두 Plain Email path 노출 확인

## 11. GPT Instructions 결과

- Plain Email Action 선택 규칙 추가
- 주소록 To/CC exact 재조회 규칙 유지
- Preview 전체 확인 및 명시적 승인 규칙 추가
- exact Preview ID와 unchanged payload 규칙 추가
- 첨부 없음·Drive Link 없음 표시 규칙 추가
- 최종 길이: 6,150자
- GPT Builder 8,000자 제한 자동 테스트 통과

## 12. 테스트 결과

추가 검증:

- To 1명 Preview
- CC 포함 메일과 실제 MIME 헤더
- 주소록 이메일과 직접 입력 이메일 혼합
- invalid To/CC
- `confirmed=false` 및 문자열 confirmation 차단
- stale Preview
- wrong Preview ID
- expired Preview
- idempotency replay에서 Gmail 1회만 호출
- Gmail 확정 실패 처리
- MIME attachment 0개
- Preview/Send 중 Drive download factory 0회
- Drive share factory 0회
- Preview 중 Gmail factory 0회
- 별도 HTTP client turn 간 Preview → Send 상태 유지
- 기존 Enhanced Email 회귀

최종 자동 테스트:

- Python 문법 검사: 통과
- Plain Email/GPT Action/API 집중 테스트: 통과
- 전체 회귀 테스트: 178개 통과

## 13. 실제 Preview 종단 검증

실제 SQLite 주소록에서 예시 수신자를 이름·직급으로 검색한 결과 후보가 1명임을 확인했다. exact `contact_id`로 재조회하여 `email_usable=true`, `conflict_code=null`을 확인한 뒤 로컬 HTTP application 경로에서 Plain Email Preview를 생성했다.

- To: 주소록 exact contact 1명
- CC: 없음
- 제목·본문: 사용자 승인용 예시 문구
- 첨부: 없음
- Drive Link: 없음
- Gmail 발송: 실행하지 않음
- Drive API 호출: 0건
- Drive permission 변경: 0건

사용자의 명시적 승인 후 공개 HTTPS에서도 주소록 검색 → exact contact 재조회 → Plain Email Preview를 완료했다. Preview는 Gmail 발송, Drive API 호출, Drive permission 변경을 수행하지 않았다.

실제 발송은 유효한 Preview를 사용자에게 표시하고 명시적 승인을 받은 뒤 1건만 시도했다. Gmail API 호출 결과가 `GMAIL_DELIVERY_UNCERTAIN`이어서 `message_id`가 확인되지 않았으며 자동 재시도하지 않았다. 수신함 또는 Gmail 보낸편지함에서 실제 전달 여부를 사람이 확인해야 한다.

## 14. 생성·수정 파일

생성:

- `plain_email_service.py`
- `test_plain_email_service.py`
- `Project-1-6_MVP-04A_Plain-Email_진행보고.md`

수정:

- `api_server.py`
- `gpt_action_openapi.yaml`
- `GPTS_INSTRUCTIONS.md`
- `test_api_server.py`
- `test_gpt_action_contract.py`

## 15. 보안 확인

- 실제 API key 기록: 0건
- OAuth token 기록: 0건
- Cloudflare token 기록: 0건
- 본문 원문 DB·일반 로그 기록: 0건
- Preview 단계 Gmail 발송: 0건
- Plain Email의 Drive/Sheets 호출: 0건
- 실제 Gmail 발송 호출: 사용자 승인 후 1회
- 확정 성공 응답: 0건 (`message_id` 없음)
- 자동 재시도: 0건
