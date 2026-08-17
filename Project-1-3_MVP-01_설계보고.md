# Python Drive Organizer — Project 1-3 MVP-01 설계 보고

- 프로젝트 단계: Project 1-3 — Email Sending
- MVP: MVP-01 OAuth / 이메일 발송 구조 설계
- 작성일: 2026-08-14 (Asia/Seoul)
- 상태: **설계 완료 — 실제 이메일 발송 및 코드 변경 없음**

## 1. 목적과 이번 MVP의 경계

Project 1-3의 목적은 기존 GPT 검색 기능으로 확정한 Google Drive의 `file_id`를 이용해 파일을 내려받고, 사용자가 지정한 수신자에게 Gmail API로 첨부 발송할 수 있는 구조를 추가하는 것이다.

초기 발송 범위는 다음과 같이 제한한다.

- 메일 1건
- 수신자(`To`) 1명
- 첨부 파일 1개
- 제목과 본문 1개씩
- SMTP 및 앱 비밀번호를 사용하지 않고 Gmail API v1의 `users.messages.send` 사용
- Google Drive 파일의 생성, 수정, 이동, 복사, 이름 변경, 삭제 및 휴지통 이동 금지

이번 MVP-01은 인증과 발송 구조를 설계하는 단계다. OAuth 재승인, 토큰 생성, API endpoint 추가, 첨부 다운로드 및 실제 이메일 발송은 수행하지 않았다.

## 2. 현재 OAuth 구조 확인 결과

현재 구현은 [`drive_client.py`](drive_client.py)를 중심으로 다음 구조를 사용한다.

| 항목 | 현재 상태 |
|---|---|
| OAuth 클라이언트 설정 | 프로젝트 루트의 `credentials.json` 사용 |
| OAuth 유형 | Installed application + 로컬 브라우저 승인 |
| 현재 토큰 파일 | 프로젝트 루트의 `token.json` |
| 현재 scope | `https://www.googleapis.com/auth/drive.metadata.readonly` |
| 토큰 재사용 | 유효한 `token.json`을 재사용 |
| 토큰 갱신 | 만료 시 저장된 refresh token으로 갱신 |
| 재승인 | 유효한 토큰 또는 refresh token이 없을 때 로컬 브라우저 OAuth 실행 |
| Drive 동작 | Drive API v3 `files.list`, `trashed = false`, 메타데이터 조회만 수행 |

현재 `SCOPES`, `CREDENTIALS_FILE`, `TOKEN_FILE`은 `drive_client.py`의 모듈 상수로 정의되어 있다. `token.json`에는 현재 `drive.metadata.readonly`만 승인되어 있으므로 파일 바이너리 다운로드나 Gmail 발송 권한이 없다.

`credentials.json`과 `token.json`은 현재 `.gitignore` 대상이다. 이 보고서에는 두 파일의 실제 값, client secret, access token 및 refresh token을 기록하지 않았다.

## 3. 필요한 최소 OAuth scope

### 3.1 Gmail 발송

메일 발송에는 다음 scope 하나만 사용한다.

```text
https://www.googleapis.com/auth/gmail.send
```

이 scope는 사용자를 대신해 이메일을 보내는 권한이며 Gmail 받은편지함 읽기 또는 메일 변경 권한은 포함하지 않는다. Gmail API의 `users.messages.send`가 실제 발송 method다. 다음 scope는 추가하지 않는다.

- `gmail.readonly`
- `gmail.modify`
- `mail.google.com`

근거: [Gmail API OAuth scope](https://developers.google.com/workspace/gmail/api/auth/scopes), [users.messages.send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send), [Gmail API 발송 가이드](https://developers.google.com/workspace/gmail/api/guides/sending)

### 3.2 Drive 첨부 다운로드

현재 `drive.metadata.readonly`는 파일 메타데이터만 조회하므로 첨부 바이너리를 가져올 수 없다. 일반 Drive 파일을 `file_id`로 다운로드하기 위한 권장 scope는 다음과 같다.

```text
https://www.googleapis.com/auth/drive.readonly
```

선택 이유는 다음과 같다.

- 기존 SQLite 인덱스에서 선택한 임의의 기존 Drive 파일을 읽고 다운로드할 수 있다.
- 이름 변경, 이동, 복사, 삭제 또는 업로드 같은 쓰기 동작을 허용하지 않는다.
- `drive.file`은 사용자가 앱으로 열거나 앱이 생성한 특정 파일 중심의 권한이며 파일 쓰기도 허용하므로, 전체 인덱스에서 선택한 기존 파일 다운로드와 엄격한 쓰기 금지 경계에 적합하지 않다.

`drive.readonly`는 파일 내용 전체를 볼 수 있는 강한 읽기 권한이며 Google의 제한 scope에 해당한다. 따라서 인덱싱 프로세스와 분리하고, 토큰 파일 접근권한과 보관 위치를 엄격히 관리해야 한다.

근거: [Drive API OAuth scope 선택](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)

## 4. 권장 인증 분리 구조

### 4.1 비교

| 방식 | 장점 | 위험/단점 | 판단 |
|---|---|---|---|
| A. 기존 `token.json`에 Drive 다운로드와 Gmail 발송 권한 통합 | 파일과 코드 수가 적음 | 무인 Daily Refresh가 파일 내용 읽기와 메일 발송 권한까지 가진 토큰에 접근하게 됨. 기존 안정 동작의 재승인 및 회귀 위험이 큼 | 비권장 |
| B. Drive 조회 토큰과 발송 관련 토큰 분리 | 기존 인덱싱 권한 유지, 장애 및 권한 노출 범위 축소 | 토큰 파일과 최초 동의 절차가 늘어남 | 권장 |

### 4.2 최종 권장안: 세 토큰의 엄격한 분리

```text
token.json
  └─ drive.metadata.readonly
     기존 인덱싱 / Daily Refresh 전용

drive_download_token.json
  └─ drive.readonly
     선택된 첨부 파일 다운로드 전용

gmail_send_token.json
  └─ gmail.send
     Gmail 발송 전용
```

개인 단일 사용자 프로젝트에서도 이 구성이 가장 안전하다. 다운로드 토큰이 노출되어도 이메일을 보낼 수 없고, 발송 토큰이 노출되어도 Drive 파일을 읽을 수 없다. 특히 자동 실행되는 Daily Refresh는 계속 `token.json`만 사용하므로 새 권한을 얻지 않는다.

두 새 인증 흐름은 동일한 Installed App용 `credentials.json`을 재사용할 수 있다. 단, 해당 Google Cloud 프로젝트에서 Gmail API가 활성화되어 있고 OAuth 동의 화면 및 테스트 사용자 설정이 완료되어 있어야 한다.

새 토큰 이름은 현재 `.gitignore`의 정확한 `token.json` 규칙만으로는 보호되지 않는다. MVP-02에서 토큰을 생성하기 **전에** 다음 파일을 `.gitignore`에 명시해야 한다.

```text
drive_download_token.json
gmail_send_token.json
```

와일드카드 `*token*.json`도 가능하지만, 프로젝트 내 다른 정상 파일을 과도하게 제외할 수 있으므로 정확한 파일명을 권장한다.

## 5. OAuth 재승인과 안전한 전환 절차

Installed App OAuth에서 기존 refresh token에 새 scope가 자동으로 추가되지는 않는다. 새 scope는 별도의 사용자 동의와 새 토큰 발급이 필요하다. 공식 Installed App 안내도 새 권한이 필요할 때 새 인증 흐름을 수행하도록 한다.

근거: [OAuth 2.0 for native apps](https://developers.google.com/identity/protocols/oauth2/native-app)

MVP-02의 안전한 절차는 다음과 같다.

1. 현재 프로그램과 Daily Refresh가 `token.json`으로 정상 동작하는지 확인한다.
2. `token.json`을 삭제하거나 덮어쓰지 않는다.
3. 필요한 경우 사용자 전용 비공개 위치에 기존 토큰 파일을 타임스탬프가 있는 이름으로 복사 백업한다. 백업 파일 역시 Git 및 로그에서 제외한다.
4. `drive.readonly` 전용 인증 흐름을 실행해 `drive_download_token.json`을 새로 만든다.
5. `gmail.send` 전용 인증 흐름을 실행해 `gmail_send_token.json`을 새로 만든다.
6. 승인 화면에서 요청 scope가 각각 하나뿐인지 확인한다.
7. 토큰 파일 자체나 토큰 값을 콘솔, 로그, 보고서 또는 Git에 출력하지 않는다.
8. 기존 `token.json`을 사용하는 전체 스캔과 Daily Refresh 회귀 테스트를 수행한다.

이 방식에서는 기존 FastAPI, SQLite 인덱싱 및 Daily Refresh에 영향이 없다. 현재의 Drive 메타데이터 읽기 전용 동작도 그대로 유지된다.

## 6. 권장 모듈 구조와 책임

기존 `drive_client.py`를 확장해 모든 권한을 섞지 않고, 다음과 같이 책임을 분리한다.

```text
drive_client.py
  기존 메타데이터 인덱싱 전용, 변경하지 않음

drive_download_client.py
  drive_download_token.json 관리
  Drive 파일 메타데이터 재검증
  일반 파일 바이너리 다운로드

gmail_client.py
  gmail_send_token.json 관리
  Gmail API 서비스 생성
  MIME 메시지 발송

email_service.py
  file_id 및 입력 검증
  다운로드와 MIME 조립, 발송 흐름 조정
  크기 제한과 오류 정책 적용
  최소 결과 반환
```

`email_service.py`의 예상 책임은 다음과 같다.

1. `file_id`, 수신자, 제목, 본문 및 확인 값 검증
2. SQLite의 현재 파일 레코드와 정확한 `file_id` 일치 확인
3. Drive API에서 이름, MIME type, 크기, 휴지통 상태 및 다운로드 가능 여부 재검증
4. 첨부 크기를 다운로드 전후에 모두 검사
5. 일반 파일 바이너리 다운로드
6. Python 표준 `email.message.EmailMessage`로 MIME 메시지 생성
7. 메시지 원문을 URL-safe Base64로 인코딩
8. Gmail API v1 `users.messages.send(userId="me", body={"raw": ...})` 실행
9. 발송 성공 시 최소한의 결과만 반환

Google API 클라이언트를 통한 발송 예외, 권한 거부, 다운로드 불가, 크기 초과 및 잘못된 수신자는 서로 구분되는 의미 있는 오류로 변환한다. 첨부 파일 내용이나 이메일 본문을 오류 메시지에 포함하지 않는다.

## 7. 첨부 파일 처리 정책

### 7.1 초기 지원 대상

첫 구현에서는 일반 binary 파일을 우선 지원한다.

- PDF
- XLSX
- DOCX
- ZIP
- CAD 및 기타 일반 Drive 파일

일반 파일은 Drive API v3의 `files.get(fileId=..., alt="media")` 방식으로 내려받는다. 실제 MIME type을 알 수 없으면 안전하게 `application/octet-stream`으로 첨부하고 원래 파일명을 유지한다.

### 7.2 Google Workspace native 파일

Google Docs, Sheets 및 Slides는 일반 binary 파일이 아니므로 `files.get(..., alt="media")`로 직접 받을 수 없다. 이 파일들은 `files.export`와 출력 형식 선택이 필요하다.

초기 MVP-02 권장 정책은 다음과 같다.

- `application/vnd.google-apps.*` native 파일은 명확한 `UNSUPPORTED_NATIVE_FILE` 오류로 거절한다.
- 폴더 및 Drive shortcut도 첨부 대상으로 처리하지 않는다.
- 일반 binary 파일 경로가 안정화된 뒤 별도 MVP에서 export를 추가한다.

향후 export 예시는 다음과 같다.

| Drive native 형식 | 후보 export 형식 |
|---|---|
| Google Docs | PDF 또는 DOCX |
| Google Sheets | XLSX 또는 PDF |
| Google Slides | PPTX 또는 PDF |

사용자 확인 없이 export 형식을 임의 선택하면 파일 표현이나 서식이 달라질 수 있으므로, 지원 시에는 결과 형식을 명시해야 한다. Drive API의 native 문서 export 결과에는 10 MB 제한이 있다는 점도 별도 검사해야 한다.

근거: [Drive 파일 다운로드 및 export](https://developers.google.com/workspace/drive/api/guides/manage-downloads)

## 8. 첨부 크기 제한

개인 Gmail의 일반적인 첨부 제한은 총 25 MB이며 Workspace 환경은 관리자가 다르게 설정할 수 있다. Gmail API discovery 문서에서 media upload의 최대 크기는 36,700,160 bytes로 안내되지만, MIME 구성과 인코딩 오버헤드 및 Gmail 계정 정책을 함께 고려해야 한다.

근거: [Gmail 첨부파일 제한](https://support.google.com/mail/answer/6584?hl=ko), [Gmail API discovery](https://gmail.googleapis.com/$discovery/rest?version=v1)

따라서 초기 프로젝트 안전 제한은 다음으로 정한다.

```text
단일 첨부 binary 최대: 18 MiB (18,874,368 bytes)
```

18 MiB는 Google의 공식 제한값이 아니라, Base64/MIME 오버헤드를 감안해 25 MB 한도 안에서 실패 가능성을 낮추기 위한 프로젝트 운영 제한이다.

검사는 두 번 수행한다.

1. Drive `size` 메타데이터가 18 MiB를 초과하면 다운로드 전에 거절한다.
2. 스트리밍 다운로드 중 누적 실제 byte가 18 MiB를 초과하면 즉시 중단하고 거절한다.

크기 메타데이터가 없다고 무제한 다운로드하지 않는다. 메모리 및 임시 저장 공간을 보호하기 위해 실제 byte 상한을 반드시 적용한다.

## 9. 향후 FastAPI endpoint 설계

이번 MVP에서는 endpoint를 구현하지 않는다. MVP-02의 최소 endpoint 후보는 다음과 같다.

```http
POST /email/send-file
Authorization: Bearer <PDO_API_KEY>
Content-Type: application/json
```

예상 request:

```json
{
  "file_id": "drive-file-id",
  "to": "recipient@example.com",
  "subject": "제목",
  "body": "본문",
  "confirmed": true,
  "idempotency_key": "client-generated-unique-value"
}
```

예상 성공 response:

```json
{
  "status": "sent",
  "message_id": "gmail-message-id",
  "file_id": "drive-file-id",
  "file_name": "attachment.pdf",
  "recipient": "recipient@example.com"
}
```

보안 및 동작 원칙:

- 기존 `PDO_API_KEY` Bearer 인증을 그대로 적용한다.
- `confirmed`가 정확히 `true`가 아니면 발송하지 않는다.
- GPT Action에서는 `x-openai-isConsequential: true`로 표시한다.
- 동일 요청 재시도로 중복 발송되지 않도록 `idempotency_key`를 저장하고 재사용을 거절하거나 기존 결과를 반환한다.
- 한 번에 수신자 한 명, 첨부 한 개만 허용한다.
- CC, BCC, 다중 수신자, 다중 첨부 및 bulk send는 허용하지 않는다.
- 서버는 `file_id`로 파일을 다시 조회해 요청 시점의 실제 파일명, 크기 및 다운로드 가능 여부를 재검증한다.

`confirmed: true`만으로는 서버가 실제 사용자의 대화상 확인을 독립적으로 증명할 수 없다는 한계가 있다. 초기 최소 구현에서는 GPT 지침과 consequential Action 확인을 함께 사용하고, 후속 보안 강화에서는 아래 두 단계 구조를 권장한다.

1. `POST /email/prepare-file`: 발송하지 않고 고정된 미리보기와 짧은 만료시간의 일회용 `preview_id` 생성
2. `POST /email/send-file`: 사용자가 확인한 동일 `preview_id`의 변경되지 않은 payload만 한 번 발송

기존 `gpt_action_openapi.yaml`은 Project 1-2의 GET-only 조회 계약이므로 그대로 보존한다. 발송 Action은 별도 schema로 분리해 읽기 전용 Action과 consequential 쓰기 Action의 경계를 명확히 한다.

## 10. GPT 최종 확인 흐름

GPT는 다음 순서를 반드시 지킨다.

1. 기존 `searchFiles` 등 조회 Action으로 후보를 찾는다.
2. 사용자가 원하는 정확한 파일의 `file_id`를 확정한다.
3. 수신자 이메일 주소를 사용자에게 확인한다. 추측하거나 주소록에서 임의 선택하지 않는다.
4. 발송 전에 수신자, 제목, 본문, 첨부 파일명과 가능한 경우 경로를 한 화면에 제시한다.
5. “이 내용으로 발송할까요?”와 같이 명시적인 최종 확인을 요청한다.
6. 사용자가 명확히 승인한 경우에만 send Action을 호출한다.
7. 수신자, 제목, 본문 또는 첨부 중 하나라도 변경되면 기존 승인은 무효로 하고 다시 확인한다.
8. 발송 결과를 성공 또는 실패로 명확히 보고한다.

모호한 답변, 단순 파일 검색 요청, 미리보기 요청 또는 “보낼 준비를 해줘” 같은 표현은 발송 승인으로 해석하지 않는다.

## 11. 로그 및 비밀정보 정책

운영 로그에 허용할 최소 항목은 다음과 같다.

- 처리 시각
- 성공 또는 실패 상태
- `file_id`
- `file_name`
- 마스킹하거나 해시한 recipient
- 성공 시 Gmail `message_id`
- 비민감 오류 코드

수신자 원문은 응답에 필요할 수 있지만 지속 로그에는 기본적으로 마스킹 또는 단방향 해시를 권장한다. 다음 값은 기록하거나 출력하지 않는다.

- OAuth access token
- OAuth refresh token
- `PDO_API_KEY`
- Cloudflare token
- `credentials.json` 내용 및 client secret
- MIME 원문
- 이메일 본문 전체
- 첨부 파일 내용
- HTTP `Authorization` header

임시 파일이 필요한 구현이라면 사용자 전용 임시 디렉터리에 만들고 발송 또는 실패 직후 정리한다. 가능하면 18 MiB 상한 안에서 메모리 또는 제한된 임시 스트림을 사용하며, 첨부 내용을 일반 운영 로그나 SQLite 인덱스 DB에 저장하지 않는다.

## 12. 기존 기능에 미치는 영향

이번 MVP-01은 문서 작성만 수행했으므로 현재 동작 중인 다음 요소에는 변경이 없다.

- Daily Refresh
- SQLite indexing
- File Name Parser
- File Grouping
- Search API
- GPT read-only Actions
- Cloudflare Tunnel
- FastAPI 자동 시작
- Windows Task Scheduler
- Google Drive의 실제 파일 및 폴더

향후에도 기존 `drive_client.py`와 `token.json`을 그대로 두면 인덱싱과 Daily Refresh는 `drive.metadata.readonly`만 유지한다. 새 다운로드/발송 모듈은 사용자 요청으로 메일을 보낼 때만 각각의 별도 토큰을 사용해야 한다.

## 13. MVP-02 구현 범위 제안

MVP-02는 다음 최소 범위로 제한한다.

1. Gmail API 활성화 및 OAuth 동의 화면 설정 확인
2. 새 토큰 두 개를 생성하기 전에 `.gitignore` 보강
3. `drive_download_token.json` 생성 흐름 구현 및 `drive.readonly` 승인
4. `gmail_send_token.json` 생성 흐름 구현 및 `gmail.send` 승인
5. `drive_download_client.py`, `gmail_client.py`, `email_service.py` 최소 구현
6. 일반 binary 파일 1개, 수신자 1명, 메일 1건 지원
7. 18 MiB 사전/실제 크기 제한 적용
8. Google Workspace native 파일, 폴더 및 shortcut은 명시적으로 거절
9. `POST /email/send-file`에 기존 Bearer 인증, 명시적 확인, consequential 표시 및 idempotency 적용
10. 사용자가 지정한 자체 이메일 주소 또는 전용 테스트 주소로만 1건의 종단 테스트
11. 기존 전체 테스트와 Daily Refresh 회귀 검증
12. 실제 token, API key, 메시지 본문 및 첨부 내용 비로그 검증

MVP-02에서도 Drive 쓰기 API는 사용하지 않는다. 파일 export 지원, 다중 첨부, 다중 수신자, CC/BCC, HTML 편집기, 예약 발송, 대량 발송 및 메일함 읽기는 후속 범위로 남긴다.

## 14. 최종 결정 요약

| 설계 항목 | 결정 |
|---|---|
| Gmail 방식 | Gmail API v1 `users.messages.send` |
| Gmail scope | `gmail.send`만 사용 |
| 첨부 다운로드 scope | `drive.readonly` |
| Drive 쓰기 scope | 사용하지 않음 |
| 기존 인덱싱 token | `token.json`과 `drive.metadata.readonly` 그대로 유지 |
| 새 token 구조 | 다운로드와 Gmail 발송 token을 각각 분리 |
| 초기 첨부 대상 | 일반 binary 파일 1개 |
| Google native 파일 | 초기에는 거절, 후속 export 지원 |
| 프로젝트 첨부 상한 | 18 MiB |
| API | 향후 `POST /email/send-file`, Bearer 인증, consequential |
| 사용자 확인 | 정확한 발송 내용 제시 후 명시적 최종 승인 필수 |
| 실제 발송 여부 | 이번 MVP에서는 발송하지 않음 |

## 15. 비밀정보 비포함 확인

이 문서에는 실제 `PDO_API_KEY`, OAuth access token, refresh token, Google OAuth client ID/client secret, Cloudflare token 또는 실제 이메일 인증정보가 포함되어 있지 않다. 예시의 이메일 주소, 파일 ID, message ID 및 idempotency key는 모두 설명용 가상 값이다.
