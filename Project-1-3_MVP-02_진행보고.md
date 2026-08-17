# Python Drive Organizer — Project 1-3 MVP-02 진행보고

- 단계: Project 1-3 — Email Sending
- MVP: MVP-02 Drive 다운로드 + Gmail 첨부 발송 구현
- 완료일: 2026-08-14 (Asia/Seoul)
- 상태: **완료**

## 1. 완료 요약

정확한 Google Drive `file_id`를 SQLite 인덱스와 Drive API에서 다시 검증하고, 일반 binary 파일 1개를 내려받아 Gmail API로 수신자 1명에게 발송하는 로컬 기능을 구현했다.

사용자가 터미널에서 수신자, 제목, 첨부 파일명과 크기를 확인한 뒤 대문자 `SEND`를 입력한 경우에만 Gmail 인증 및 발송이 진행된다. 실제 테스트는 작은 PDF 1개로 정확히 1건 성공했으며 Gmail message ID가 반환되었다.

이번 MVP에서는 GPT Action, FastAPI endpoint 및 기존 OpenAPI schema를 변경하지 않았다. Google Drive 쓰기 operation도 호출하지 않았다.

## 2. 생성·수정 파일

### 소스 및 설정

| 파일 | 구분 | 내용 |
|---|---|---|
| `.gitignore` | 수정 | 새 OAuth token 두 파일을 Git 제외 대상으로 먼저 등록 |
| `drive_download_client.py` | 생성 | `drive.readonly` 전용 OAuth, 정확한 파일 메타데이터 조회 및 제한된 binary 다운로드 |
| `gmail_client.py` | 생성 | `gmail.send` 전용 OAuth, Gmail API v1 서비스와 단일 발송 |
| `email_service.py` | 생성 | 입력·파일 검증, MIME 생성, 다운로드/발송 조정, idempotency와 보안 로그 |
| `email_cli.py` | 생성 | OAuth 및 실제 발송을 위한 사용자 확인형 로컬 CLI |
| `test_email_service.py` | 생성 | 신규 인증 경계, 검증, MIME, 크기 제한, idempotency 및 CLI 취소 테스트 |
| `Project-1-3_MVP-02_진행보고.md` | 생성 | 본 진행보고서 |

### 실행 중 생성되는 Git 제외 파일

| 파일 | 용도 |
|---|---|
| `drive_download_token.json` | Drive 다운로드 전용 OAuth token |
| `gmail_send_token.json` | Gmail 발송 전용 OAuth token |
| `data/email_send_state.db` | idempotency 상태와 성공 message ID의 최소 기록 |
| `logs/email_send.log` | 본문과 첨부를 제외한 마스킹 감사 로그 |

`requirements.txt`에는 패키지를 추가하지 않았다. 기존 Google 공식 Python API 클라이언트와 Python 표준 `sqlite3`, `email` 모듈만 사용했다.

## 3. OAuth 구조

### 기존 인덱싱 인증

```text
token.json
scope: https://www.googleapis.com/auth/drive.metadata.readonly
```

기존 `token.json`은 새 인증 흐름에서 읽거나 덮어쓰지 않았다. 구현 전후 파일 해시와 scope를 확인했으며 값이 동일했다. 기존 `drive_client.py`도 수정하지 않았다.

### Drive 다운로드 인증

```text
drive_download_token.json
scope: https://www.googleapis.com/auth/drive.readonly
```

- 기존 Installed App용 `credentials.json` 재사용
- 최초 실행 시 로컬 브라우저 OAuth 승인
- token 파일과 실제 token 값은 출력·로그·보고서에서 제외
- 기존 인덱싱 token을 사용하지 않음
- Drive content 읽기만 허용하고 쓰기 scope는 사용하지 않음

### Gmail 발송 인증

```text
gmail_send_token.json
scope: https://www.googleapis.com/auth/gmail.send
```

- Drive 다운로드 token과 별도 관리
- Gmail 받은편지함 읽기 및 수정 scope를 추가하지 않음
- `gmail.readonly`, `gmail.modify`, `mail.google.com` 사용 안 함
- Gmail API v1 서비스 생성

두 새 token 파일은 생성 전에 `.gitignore`에 등록했으며 `git check-ignore`로 실제 제외를 확인했다.

## 4. Google Cloud 선행 조건 결과

최초 실제 발송 시 Google Cloud 프로젝트에서 Gmail API가 비활성 상태임이 `GMAIL_API_NOT_ENABLED` 오류로 확인되었다. 코드로 API를 활성화하거나 Cloud 설정을 변경하지 않고 사용자에게 Google Cloud Console 활성화를 요청했다.

사용자가 Gmail API를 활성화한 후 새 idempotency key로 다시 진행했고 Gmail API가 정상적인 message ID를 반환했다. 따라서 최종 상태는 다음과 같다.

- Google Drive API: 기존과 같이 정상
- Gmail API: **활성화 및 실제 발송 응답 확인 완료**
- Installed App OAuth: Drive 다운로드 및 Gmail 발송 각각 정상
- OAuth 승인 사용자/test user 구조: 두 scope 승인 성공으로 사용 가능 확인

## 5. Drive 파일 검증 및 다운로드

`email_service.py`는 전달받은 `file_id`를 바로 신뢰하지 않는다.

1. `data/drive_index.db`를 SQLite read-only mode로 연다.
2. `files.file_id`의 정확한 일치를 검사한다.
3. 같은 ID가 `folders`에 있으면 폴더 오류로 구분한다.
4. Drive API `files.get`으로 동일한 ID의 현재 메타데이터를 조회한다.
5. 반환된 ID, 파일명, MIME type, `trashed`, `size`, `capabilities.canDownload`을 검증한다.
6. 파일명으로 다른 후보를 다시 검색하지 않는다.
7. 검증된 동일 ID만 `files.get(..., alt=media)`에 해당하는 `get_media` 호출로 다운로드한다.

다운로드는 1 MiB chunk와 byte 제한이 있는 메모리 stream을 사용한다. 제한을 넘는 byte는 stream에 기록되기 전에 예외가 발생하므로 무제한 메모리를 사용하지 않는다.

## 6. 지원 및 거절 대상

### 지원

일반 Drive binary 파일 1개를 지원한다.

- PDF
- XLSX
- DOCX
- ZIP
- DWG/DXF 등 CAD 파일
- 이미지
- 기타 일반 Drive blob 파일

### 거절

| 대상 | 오류 코드 |
|---|---|
| Google Docs, Sheets, Slides, Forms 등 native file | `UNSUPPORTED_NATIVE_FILE` |
| Drive folder | `UNSUPPORTED_FOLDER` |
| Drive shortcut | `UNSUPPORTED_SHORTCUT` |

`application/vnd.google-apps.*` export는 이번 MVP에서 구현하지 않았다.

## 7. 18 MiB 제한

프로젝트 운영 제한은 다음과 같다.

```text
18 MiB = 18,874,368 bytes
```

- Drive metadata의 `size`가 제한을 넘으면 다운로드 전에 `ATTACHMENT_TOO_LARGE`로 거절한다.
- size가 없거나 잘못된 경우에도 실제 다운로드 누적 byte를 제한한다.
- 제한을 넘는 chunk는 메모리에 추가되지 않는다.
- 실제 테스트 첨부는 27,399 bytes로 제한 안에 있었다.

## 8. MIME 및 Gmail 발송 방식

MIME 메시지는 Python 표준 라이브러리 `email.message.EmailMessage`로 생성한다.

- `To`: 한 명
- `Subject`: 한 줄, header injection 차단
- Body: plain text
- Attachment: 한 개, Drive의 실제 파일명과 MIME type 사용
- CC/BCC: 없음
- HTML body: 없음
- 다중 첨부/다중 수신자: 없음

생성된 MIME bytes를 URL-safe Base64로 인코딩하고 다음 Gmail API 호출로 발송한다.

```text
users.messages.send(
    userId="me",
    body={"raw": "..."}
)
```

클라이언트 자동 재시도는 `num_retries=0`으로 비활성화했다. Gmail이 message ID를 반환하지 않으면 성공으로 처리하지 않는다.

## 9. 수신자 및 입력 보안

- 빈 수신자 거절
- 쉼표와 세미콜론을 포함한 다중 주소 거절
- CR/LF를 이용한 email header injection 거절
- 복잡한 전체 RFC parser 대신 한 명의 일반적인 이메일 주소 형식만 허용
- 제목의 CR/LF 거절
- Drive 파일명의 CR/LF 거절
- CLI argument로 OAuth token, API key 또는 Cloudflare token을 받지 않음

## 10. 사용자 최종 확인 CLI

실행 형태:

```powershell
python email_cli.py authorize-drive
python email_cli.py authorize-gmail
python email_cli.py send --file-id "..." --to "..." --subject "..." --body "..."
```

발송 전에 터미널에 다음 항목을 표시한다.

- recipient
- subject
- attachment file name
- attachment size
- idempotency request ID

사용자가 정확히 대문자 `SEND`를 입력하지 않으면 Gmail OAuth와 발송을 시작하지 않는다. 실제 테스트에서도 소문자 입력은 취소 처리되어 발송 0건임을 확인한 후, 별도 새 요청에서 대문자 `SEND`를 받아 발송했다.

## 11. Idempotency 구조

Drive 인덱스와 별도의 `data/email_send_state.db`를 사용한다.

저장 항목:

- idempotency key
- 발송 payload의 SHA-256 hash
- `PENDING`, `SENT`, `FAILED` 상태
- 성공 시 Gmail message ID
- file ID와 파일명
- 첨부 크기
- 마스킹 recipient
- 오류 코드와 시각

메일 본문, 첨부 내용 및 recipient 원문은 저장하지 않는다.

동작 원칙:

- Gmail 호출 전에 `PENDING`을 기록한다.
- 같은 key와 같은 payload가 이미 `SENT`면 Gmail을 다시 호출하지 않고 기존 결과를 반환한다.
- 같은 key를 다른 payload에 재사용하면 `IDEMPOTENCY_CONFLICT`로 거절한다.
- `PENDING` 상태는 결과가 불확실할 수 있으므로 자동 재발송하지 않는다.
- 확정 실패 key도 자동 재사용하지 않고 새 사용자 확인과 새 key가 필요하다.

최초 Gmail API 비활성 실패 요청과 활성화 후 성공 요청은 서로 다른 key를 사용했다.

## 12. 로그 보안

허용된 로그 항목만 JSON Lines 형식으로 기록한다.

- timestamp
- status
- file ID
- file name
- attachment size
- masked recipient
- Gmail message ID
- error code

기록하지 않는 항목:

- OAuth access token 및 refresh token
- `PDO_API_KEY`
- `credentials.json` 내용
- Cloudflare token
- Authorization header
- recipient 원문
- 이메일 본문
- 첨부 내용
- MIME raw

단위 테스트에서 실제 recipient 원문과 고유한 본문 문자열이 로그 및 idempotency DB에 존재하지 않음을 확인했다.

## 13. 오류 코드

구현 및 구분한 주요 오류:

- `FILE_NOT_INDEXED`
- `DRIVE_FILE_NOT_FOUND`
- `FILE_TRASHED`
- `UNSUPPORTED_NATIVE_FILE`
- `UNSUPPORTED_FOLDER`
- `UNSUPPORTED_SHORTCUT`
- `ATTACHMENT_TOO_LARGE`
- `INVALID_RECIPIENT`
- `DOWNLOAD_FAILED`
- `GMAIL_AUTH_FAILED`
- `GMAIL_SEND_FAILED`
- `GMAIL_API_NOT_ENABLED`
- idempotency 관련 충돌/진행/이전 실패 오류

사용자 오류에는 token, 첨부 내용 또는 Gmail 원문 응답을 포함하지 않는다.

## 14. 실제 1건 종단 테스트 결과

| 항목 | 결과 |
|---|---|
| 사용자 최종 확인 | 대문자 `SEND` 입력 완료 |
| 정확한 Drive file ID 검증 | 통과 |
| SQLite 존재 확인 | 통과 |
| Drive metadata 재검증 | 통과 |
| 첨부 파일명 | `폴대.pdf` |
| MIME type | `application/pdf` |
| 첨부 크기 | 27,399 bytes |
| recipient | `s***@n***.com` |
| Gmail API 상태 | 활성화 확인 |
| Gmail 발송 상태 | `SENT` |
| Gmail message ID | **존재 확인, 보고서에는 값 비공개** |
| 실제 성공 이메일 수 | 1건 |
| Drive write | 0건 |

최초 비활성 상태의 Gmail API 요청은 `FAILED`로 기록됐고 message ID가 없어 이메일이 발송되지 않았다. 활성화 후의 새 요청만 `SENT`와 message ID를 받았다.

## 15. 테스트 및 회귀 검증

### 신규 테스트

`test_email_service.py`의 12개 테스트가 통과했다.

- 정확한 file ID 사용
- 미인덱스 파일과 폴더 구분
- native/folder/shortcut 거절
- 휴지통/크기 초과/download 불가 거절
- 다중 주소와 header injection 차단
- plain-text MIME와 첨부 1개 생성
- idempotency 재사용 시 중복 Gmail 호출 방지
- 로그 및 DB의 recipient/body 비포함
- 실제 byte 상한
- OAuth token과 scope 분리
- Gmail URL-safe Base64 및 자동 재시도 0회
- 사용자 취소 시 Gmail OAuth/발송 미호출

### 전체 회귀 테스트

```text
Ran 69 tests
OK
```

Parser, Grouping, Search/Tree, FastAPI/Bearer Auth 및 Daily Refresh 테스트를 포함한 전체 suite가 통과했다. Daily Refresh 핵심 실행 순서와 실패 처리 테스트도 통과했다.

기존 인덱싱 인증의 `token.json`과 `drive.metadata.readonly` scope는 변경되지 않았다. 새 이메일 코드가 `drive_index.db`를 read-only mode로 열기 때문에 기존 SQLite 인덱스에도 쓰지 않는다.

## 16. Drive write 0건 확인

새 Drive 모듈이 사용하는 파일 resource 동작은 다음 두 가지뿐이다.

- `files.get`: 정확한 ID의 현재 메타데이터 확인
- `files.get(..., alt=media)`에 대응하는 `get_media`: binary 다운로드

다음 호출은 구현하지 않았다.

- `files.create`
- `files.update`
- `files.copy`
- `files.delete`
- trash 변경
- permissions 변경

실제 Google Drive 파일 및 폴더 변경은 0건이다.

## 17. 기존 기능 영향 및 미구현 범위

변경하지 않은 기능:

- `drive_client.py`와 기존 Drive metadata scan
- Daily Refresh
- SQLite indexing schema와 index 데이터
- Parser와 Grouping
- Search/Tree API
- FastAPI read-only endpoint 및 Bearer 인증
- GPT read-only Actions
- Cloudflare Tunnel
- Windows 자동 시작 및 Task Scheduler
- `gpt_action_openapi.yaml`

아직 구현하지 않은 범위:

- GPT Action 연결
- FastAPI 이메일 endpoint
- Google native export
- 다중 첨부 및 다중 수신자
- CC/BCC
- HTML body
- bulk/예약 발송
- Gmail inbox 읽기

## 18. Secret 비포함 확인

본 보고서에는 다음 실제 값이 포함되어 있지 않다.

- OAuth access token 및 refresh token
- Google OAuth client ID/client secret
- `PDO_API_KEY`
- Cloudflare token
- Authorization header
- 이메일 본문 원문
- 첨부 내용
- recipient 원문
- 실제 Gmail message ID 값

두 token 파일, idempotency DB, 이메일 감사 로그와 기존 secret 파일은 모두 Git 제외 영역에 있다.
