# Python Drive Organizer — Project 1-3 MVP-03 진행보고

- 완료일: 2026-08-14 (Asia/Seoul)
- 단계: Project 1-3 — Email Sending
- MVP: MVP-03 FastAPI Email Send Endpoint + GPT Action
- 상태: **COMPLETED**

## 1. 목표와 완료 범위

MVP-02에서 완성한 Google Drive 파일 다운로드 및 Gmail 단일 첨부 발송 서비스를 FastAPI와 GPT Action에 연결했다.

완료 범위는 다음과 같다.

- Bearer 인증이 적용된 `POST /email/send-file` 추가
- 정확한 Drive `file_id`의 일반 파일 1개를 수신자 1명에게 첨부 발송
- 사용자 최종 확인값 `confirmed=true` 강제
- idempotency key 기반 중복 발송 방지
- 서비스 오류를 의미 있는 HTTP 상태와 오류 코드로 변환
- 기존 SQLite 조회용 GET Action 10개 보존
- GPT Builder에서 이메일 Action을 consequential 작업으로 등록
- PC 로컬, Cloudflare 공개 HTTPS 및 GPT Builder 연결 검증
- 실제 이메일 종단 테스트 1회 성공

이번 MVP에서도 Google Drive 항목의 생성, 수정, 이름 변경, 이동, 복사, 휴지통 이동 및 삭제는 구현하지 않았다.

## 2. 최종 구조

```text
ChatGPT GPT
  ├─ 10개 GET Action ──> FastAPI ──> SQLite Drive Index (조회 전용)
  └─ sendEmailWithAttachment
       └─ POST /email/send-file
            ├─ Bearer PDO_API_KEY 검증
            ├─ confirmed=true 검증
            ├─ SQLite에서 정확한 file_id 확인
            ├─ Google Drive API로 파일 bytes 다운로드
            ├─ idempotency 상태 확인/기록
            └─ Gmail API로 단일 첨부 메일 발송

Public HTTPS: Cloudflare Tunnel
https://drive-api.sungwony.pe.kr -> http://127.0.0.1:8000
```

FastAPI의 기존 조회 엔드포인트는 SQLite만 읽는다. 이메일 엔드포인트에서만 사용자가 확정한 파일을 첨부하기 위해 Drive 파일 bytes를 읽고 Gmail 발송을 수행한다.

## 3. API 계약

### Endpoint

- Method: `POST`
- Path: `/email/send-file`
- 인증: `Authorization: Bearer <PDO_API_KEY>`
- GPT operationId: `sendEmailWithAttachment`
- GPT consequential 표시: `x-openai-isConsequential: true`

### Request

```json
{
  "file_id": "정확한 Drive 파일 ID",
  "to": "사용자가 확정한 단일 수신자",
  "subject": "사용자가 확정한 제목",
  "body": "사용자가 확정한 plain-text 본문",
  "confirmed": true,
  "idempotency_key": "요청별 고유 키"
}
```

- `confirmed`는 strict boolean이다. `false`이면 HTTP 400, 문자열 `"true"`이면 HTTP 422다.
- 수신자는 정확히 1명만 허용한다. CC/BCC, 다중 수신자 및 bulk send는 지원하지 않는다.
- 첨부는 정확히 1개만 허용한다.
- Google Docs/Sheets/Slides native 파일, 폴더 및 shortcut은 첨부할 수 없다.
- 파일 크기 상한은 MVP-02 서비스의 20 MiB 제한을 그대로 적용한다.

### Success response

```json
{
  "status": "sent",
  "message_id": "Gmail이 반환한 비어 있지 않은 ID",
  "file_id": "발송한 Drive 파일 ID",
  "file_name": "첨부 파일명",
  "recipient": "수신자",
  "idempotent_replay": false
}
```

`status=sent`이면서 `message_id`가 비어 있지 않을 때만 발송 성공으로 판단한다. 동일한 성공 요청의 idempotent replay는 Gmail을 다시 호출하지 않고 기존 결과를 반환한다.

## 4. 오류 처리

| HTTP | 주요 오류 코드 | 의미 |
|---:|---|---|
| 400 | `CONFIRMATION_REQUIRED`, `INVALID_RECIPIENT`, `INVALID_SUBJECT`, `INVALID_BODY`, `INVALID_IDEMPOTENCY_KEY`, `FILE_TRASHED`, `UNSUPPORTED_*`, `ATTACHMENT_TOO_LARGE` | 확인값, 입력 또는 첨부 조건 오류 |
| 401 | Bearer 인증 오류 | API key 누락 또는 불일치 |
| 404 | `FILE_NOT_INDEXED`, `DRIVE_FILE_NOT_FOUND` | SQLite 또는 Drive에 정확한 파일이 없음 |
| 409 | `IDEMPOTENCY_CONFLICT`, `IDEMPOTENCY_IN_PROGRESS`, `IDEMPOTENCY_PREVIOUSLY_FAILED`, `GMAIL_DELIVERY_UNCERTAIN` | 중복 충돌, 진행 중, 이전 실패 또는 전달 불확실. 자동 재시도 금지 |
| 422 | FastAPI validation 오류 | strict boolean 또는 request schema 위반 |
| 502 | `DOWNLOAD_FAILED`, `GMAIL_SEND_FAILED` | Drive/Gmail upstream 처리 실패 |
| 503 | `INDEX_UNAVAILABLE`, `DRIVE_AUTH_FAILED`, `GMAIL_AUTH_FAILED`, `GMAIL_API_NOT_ENABLED` | DB, OAuth 또는 Gmail API 사용 불가 |

OAuth 내부 예외와 secret은 응답에 노출하지 않고 안전한 오류 메시지로 변환한다.

## 5. Idempotency와 발송 안전장치

- 상태 DB: `data/email_send_state.db`
- 상태 테이블: `email_send_state`
- 감사 로그: `logs/email_send.log`
- key와 payload hash의 조합으로 다른 내용의 key 재사용을 거부한다.
- 성공한 같은 요청은 `idempotent_replay=true`로 기존 결과를 반환하고 Gmail service를 만들지 않는다.
- `PENDING` 및 `GMAIL_DELIVERY_UNCERTAIN`은 자동 재시도하지 않는다.
- Gmail 인증 실패는 다운로드 전에 `FAILED`로 기록한다.
- Gmail 전송 요청의 자동 HTTP retry는 비활성화했다.
- 상태 DB와 로그는 `.gitignore` 대상이다.

최종 테스트 시 누적 상태는 다음과 같다.

- `SENT`: 2건 (MVP-02의 기존 성공 1건 + MVP-03 성공 1건)
- `FAILED`: 1건 (이전 활성화 전 실패 기록)
- MVP-03 공개 발송 행: `SENT`, `message_id` 존재, 첨부 27,399 bytes

## 6. OAuth 및 권한 경계

OAuth token은 용도별로 분리되어 있다.

- 기존 메타데이터 인덱싱: `token.json`
  - scope: `https://www.googleapis.com/auth/drive.metadata.readonly`
- 첨부 다운로드: `drive_download_token.json`
  - scope: `https://www.googleapis.com/auth/drive.readonly`
- Gmail 발송: `gmail_send_token.json`
  - scope: `https://www.googleapis.com/auth/gmail.send`

Drive 다운로드 권한은 파일 bytes 읽기에만 사용한다. Drive 쓰기 scope와 Drive 쓰기 API 호출은 없다. 기존 `token.json` SHA-256은 작업 전후 동일한 `8A922A92AF77965D9BAC144CA5627EDA60236165054F14B999EE518683B90743`이다.

## 7. GPT Action 구성 결과

기존 GPT Builder는 같은 domain의 Action을 두 개로 분리 등록하는 것을 허용하지 않았다. 따라서 배포 구성은 기존 `gpt_action_openapi.yaml`에 이메일 POST를 병합했다.

- Public server: `https://drive-api.sungwony.pe.kr`
- 인증: 기존 API Key / Bearer 설정 유지
- 기존 GET operation: 10개 모두 보존
- 신규 POST operation: `sendEmailWithAttachment` 1개
- 전체 operation: 11개
- duplicate operationId: 0개
- Builder schema 오류: 0개
- 공개 범위: 기존과 동일한 `나만 보기`

별도 `gpt_email_action_openapi.yaml`도 독립 이메일 schema와 로컬 검증 자료로 유지한다. 실제 GPT Builder에는 domain 중복을 피하기 위해 병합 schema를 적용했다.

GPT Instructions에는 다음 원칙을 반영했다.

- 정확한 파일 검색 및 `file_id` 확정
- 수신자 추측 금지
- 수신자, 제목, 본문, 첨부 파일명과 ID를 한 번에 표시
- “이대로 발송할까요?” 형태의 최종 확인
- 명확한 승인 후에만 `confirmed=true`
- 내용 변경 시 기존 승인 무효 및 재확인
- 실패 또는 전달 불확실 상태 자동 재시도 금지
- 다중 수신자, 다중 첨부 및 bulk send 금지
- API key와 OAuth token 노출 금지

기존 수동 추가 원칙인 `listFileGroups`의 `min_members` 및 초기 `limit` 지침도 보존했다.

## 8. 테스트 결과

### 로컬 FastAPI

- `POST /email/send-file`, Bearer 없음: HTTP 401
- 유효한 Bearer + `confirmed=false`: HTTP 400 / `CONFIRMATION_REQUIRED`
- 위 두 테스트에서 Drive/Gmail 호출 및 이메일 발송 없음
- `/health`, `/status`, `/openapi.json`: 정상

### Public Cloudflare HTTPS

- `https://drive-api.sungwony.pe.kr/email/send-file` 경로 확인
- Bearer 없음: HTTP 401
- 유효한 Bearer + `confirmed=false`: HTTP 400 / `CONFIRMATION_REQUIRED`
- 실제 승인된 request 1회 전송: HTTP 성공, `status=sent`, `message_id` 존재, `idempotent_replay=false`
- 수신자와 Gmail message ID는 보고서에 기록하지 않았다.
- MVP-03에서 실제 이메일은 정확히 1건만 발송했으며 자동 재시도하지 않았다.

### GPT Builder

- Builder가 기존 GET 10개와 신규 POST 1개를 모두 인식
- `getDriveStatus` Action 실제 호출 성공
- 결과: files 7,844 / folders 1,143 / groups 7,831 / DELETE 분류 4
- latest scan: `SCAN-20260814-100125`, `COMPLETED`
- 이메일 Action 테스트 버튼은 1건 제한과 중복 발송 방지를 위해 실행하지 않음
- 대신 동일 공개 HTTPS endpoint에서 승인된 실제 POST 발송을 검증함
- 변경된 GPT 설정 저장 완료

### 자동 테스트 및 정적 검증

- Python unittest: **77건 통과 / 0건 실패**
- Python compileall: 통과
- YAML parse: 통과
- OpenAPI 3.1 validation: 통과
- 기존 GET operation 수: 10
- 신규 email POST operation 수: 1
- duplicate operationId: 0
- HTTPS server / BearerAuth / consequential flag: 통과
- FastAPI 실제 parameter contract: 통과
- Drive 쓰기 호출 정적 검색: 0건
- `git diff --check`: 오류 없음 (Windows LF/CRLF 안내만 존재)

## 9. 생성 및 수정 파일

### 생성

- `gpt_email_action_openapi.yaml`
- `Project-1-3_MVP-03_진행보고.md`

### 수정

- `api_server.py`
- `email_service.py`
- `test_api_server.py`
- `test_email_service.py`
- `GPTS_INSTRUCTIONS.md`
- `gpt_action_openapi.yaml`

MVP-03에서 외부 Python 패키지를 새로 추가하지 않았다. 기존 Google API client, FastAPI, Uvicorn, HTTPX 및 python-dotenv 구성을 재사용하며 상태 저장에는 Python 표준 `sqlite3`를 사용한다.

## 10. Secret 비포함 확인

다음 값은 source, OpenAPI schema 및 본 보고서에 기록하지 않았다.

- 실제 `PDO_API_KEY`
- OAuth access token / refresh token
- Google OAuth client secret
- Cloudflare Tunnel token
- Gmail message ID
- 실제 수신자 전체 주소

`.env`, `credentials.json`, `token.json`, `drive_download_token.json`, `gmail_send_token.json`, `data/`, `logs/`는 Git 제외 대상이다.

## 11. 현재 제한사항과 다음 단계 경계

- 파일 검색과 선택은 실시간 Drive 검색이 아니라 마지막 SQLite scan 기준이다.
- 이메일 첨부 시 파일 내용을 해석, 검색 또는 요약하지 않는다.
- Google native 파일 export는 지원하지 않는다.
- 수신자 1명, 첨부 1개, 메일 1건만 지원한다.
- 예약 발송, 초안, CC/BCC, 다중 첨부 및 bulk send는 지원하지 않는다.
- 전달 불확실 또는 idempotency 충돌 시 자동 재발송하지 않는다.
- Google Drive 쓰기 기능은 확장 범위 밖이다.

다음 MVP는 본 보고서와 현재 테스트 상태를 기준점으로 시작한다. 이메일 기능 확장 시에도 최종 사용자 확인, one-send 경계, idempotency 및 secret 비노출 원칙을 먼저 유지해야 한다.
