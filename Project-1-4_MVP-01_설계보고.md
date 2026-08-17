# Python Drive Organizer — Project 1-4 MVP-01 설계보고

- 작성일: 2026-08-14 (Asia/Seoul)
- 단계: Project 1-4 — Enhanced Email
- MVP: MVP-01 CC + Multiple Attachments + Drive Link Mode 설계
- 상태: **DESIGN COMPLETED / IMPLEMENTATION NOT STARTED**

## 1. 이번 MVP의 경계

이번 MVP는 설계만 수행한다.

- Python 실행 코드 변경 없음
- FastAPI/OpenAPI/GPT Instructions 실제 변경 없음
- OAuth scope 및 token 변경 없음
- Google Drive permission 생성 없음
- 실제 이메일 발송 없음
- 기존 `token.json`, `drive_download_token.json`, `gmail_send_token.json` 보존

Project 1-3의 단일 수신자·단일 첨부 endpoint는 다음 구현 단계에서도 하위 호환용으로 유지한다.

## 2. 확정된 제품 제한

| 항목 | Project 1-4 초기 제한 |
|---|---|
| To | 정확히 1명 |
| CC | 선택 사항, 정규화 후 최대 5명 |
| BCC | 지원하지 않음 |
| 파일 | 중복 없는 exact `file_id` 1~5개 |
| 메일 | 한 요청당 1건 |
| bulk send | 금지 |
| Body | plain text만 지원 |
| 모드 | `auto`, `attachment`, `link` |
| Drive 쓰기 | 승인된 LINK 요청의 `permissions.create`, `reader`, `user`만 후보 |

To와 CC를 합친 실제 수신 대상은 최대 6명이다. CC가 To와 같거나 CC 안에서 중복되면 첫 번째 표기만 남기고 제거하며, preview 응답에 최종 정규화 목록을 표시해 사용자가 그 목록을 승인하도록 한다.

## 3. 현재 구현 기준선

현재 Project 1-3 코드는 다음 구조다.

- `EmailMessage["To"]`: 수신자 1명
- `EmailMessage["Subject"]`: 한 줄 제목
- `set_content`: plain-text body
- `add_attachment`: binary attachment 1개
- Gmail raw message: MIME bytes 전체를 URL-safe Base64로 다시 인코딩
- `MAX_ATTACHMENT_BYTES = 18 * 1024 * 1024`
- payload hash: `file_id`, To, subject, body
- Gmail client retry: `num_retries=0`
- Drive download scope: `drive.readonly`
- Gmail scope: `gmail.send`

Google 공식 Gmail API는 RFC 2822 MIME 메시지를 base64url `raw` 값으로 보내도록 설명하고, `users.messages.send`는 To/Cc/Bcc header 수신자를 지원한다. 현재 Python `EmailMessage` 구조는 CC와 다중 attachment를 안전하게 확장할 수 있다.

참고:

- [Gmail API — Create and send email messages](https://developers.google.com/workspace/gmail/api/guides/sending)
- [Gmail API — users.messages.send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send)

## 4. CC 설계

### 4.1 검증 및 정규화

각 주소를 독립적으로 검증한다.

1. 문자열인지 확인
2. 양쪽 공백 제거
3. 빈 주소 거부
4. `CR`, `LF`, 쉼표, 세미콜론 포함 시 거부
5. 현재 단일 수신자와 동일한 이메일 형식 검증 적용
6. 비교용 canonical key는 전체 주소 `casefold()` 사용
7. To와 동일한 CC 제거
8. CC 내부 중복 제거 후 최대 5명 확인

표시 및 MIME header에는 처음 입력된 안전한 주소 표기를 유지하고, 중복 판정·hash·permission 계획에는 canonical key를 사용한다.

### 4.2 MIME header

향후 MIME 구성은 다음 형태다.

```python
message["To"] = normalized_to
if normalized_cc:
    message["Cc"] = ", ".join(normalized_cc)
message["Subject"] = subject
message.set_content(body)
```

개별 주소 검증을 통과한 값만 결합한다. BCC 필드는 request schema, MIME header 및 내부 model 어디에도 추가하지 않는다.

## 5. 다중 파일 검증 설계

단일 `file_id`를 중복 없는 `file_ids` 배열로 확장한다. 파일 순서는 사용자가 승인할 목록과 MIME/link 표시 순서이므로 보존한다. 동일 ID가 두 번 나오면 조용히 제거하지 않고 `DUPLICATE_FILE_ID`로 거부한다.

각 ID를 서로 독립적으로 다음 순서로 검증한다.

1. 빈 값, CR/LF 및 형식 안전성 확인
2. SQLite `files`에서 exact ID 확인
3. SQLite `folders` ID이면 거부
4. Drive `files.get(fileId=exact_id)`로 동일 ID 재확인
5. `trashed=false` 확인
6. `mimeType` 확인
7. `size` 확인
8. `capabilities.canDownload` 및 `capabilities.canShare` 확인
9. `webViewLink` 확인
10. shortcut/native/blob 분류

Drive metadata 권장 fields:

```text
id,name,mimeType,size,trashed,modifiedTime,version,
webViewLink,resourceKey,driveId,
capabilities(canDownload,canShare),
shortcutDetails(targetId,targetMimeType,targetResourceKey)
```

파일명으로 대체 파일을 검색하거나 다시 선택하지 않는다. 하나라도 불명확하거나 검증에 실패하면 전체 요청을 중단한다.

## 6. 전송 모드

### 6.1 `attachment`

- 일반 binary Drive 파일만 허용
- folder, shortcut, Google Workspace native 파일은 거부
- 모든 파일의 `canDownload=true` 필요
- 모든 metadata size가 확인되어야 함
- 개별 및 전체 크기 제한을 모두 통과해야 함
- 파일마다 `EmailMessage.add_attachment(...)` 실행
- Drive permission은 읽거나 변경할 필요가 없음

요청자가 `attachment`를 강제했는데 조건을 충족하지 못하면 LINK로 조용히 바꾸지 않고 오류를 반환한다. 사용자가 LINK로 다시 preview하고 승인해야 한다.

### 6.2 `link`

- 파일 bytes를 다운로드하지 않음
- Drive API가 반환한 `webViewLink`를 plain-text 본문의 표준 링크 구역에 추가
- URL 문자열을 file ID로 직접 조립하지 않음
- binary 파일과 Google Workspace native 파일 모두 허용
- folder와 shortcut은 초기 구현에서 계속 거부
- To와 CC 각각의 공유 상태를 계산
- 필요한 `reader/user` permission 계획을 preview에 표시

Google Drive File resource는 `webViewLink`를 브라우저 editor/viewer용 링크로 정의한다. `webViewLink` 같은 Drive 반환 URL에는 필요한 resource key가 포함되므로 별도 URL 조립이나 `resourceKey` 임의 추가를 하지 않는다.

참고:

- [Drive API File resource — webViewLink](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)
- [Drive resource keys](https://developers.google.com/workspace/drive/api/guides/resource-keys)

### 6.3 `auto`

preview 단계에서 다음 규칙으로 단 한 번 결정한다.

`attachment` 선택 조건:

- 파일 1~5개
- 모두 일반 binary 파일
- 모두 `canDownload=true`
- 모든 size 확인 가능
- 파일별 size가 18 MiB 이하
- 전체 binary size가 18 MiB 이하
- 예상 Gmail API raw payload가 34 MiB 이하

위 조건 중 하나라도 충족하지 못하면 `link`를 선택한다. 단, 모든 파일에 `webViewLink`가 있고 필요한 공유 작업을 수행할 수 있어야 한다. 그렇지 않으면 preview 자체를 실패시킨다.

서버가 선택한 `delivery_mode`는 최종 확인 화면에 반드시 표시한다. preview 후 metadata, 공유 계획 또는 mode가 달라지면 기존 승인을 사용하지 않고 `PREVIEW_STALE`로 중단한다.

## 7. 크기 정책 확정

### 7.1 공식 제한과 인코딩 overhead

- Gmail 개인 계정의 첨부 합계 정책: 25 MB
- Google Workspace 계정: 관리자 정책에 따라 다를 수 있음
- Gmail API discovery의 `users.messages.send` media upload maxSize: 36,700,160 bytes = 35 MiB
- 현재 구현: attachment 자체가 MIME 안에서 base64 인코딩되고, 전체 MIME bytes가 Gmail API `raw`용 base64url로 다시 인코딩됨

참고:

- [Gmail Help — attachment size limit](https://support.google.com/mail/answer/6584)
- [Gmail API discovery document](https://gmail.googleapis.com/$discovery/rest?version=v1)

### 7.2 현재 코드 방식 실측

현재 `EmailMessage.add_attachment`와 `urlsafe_b64encode(message.as_bytes())` 방식으로 최소 header/body를 사용해 측정한 결과다.

| Binary 합계 | MIME bytes | Gmail API `raw` bytes | 35 MiB 미만 |
|---:|---:|---:|---|
| 18 MiB / 18,874,368 bytes | 25,497,424 | 33,996,568 | 예 |
| 20 MiB / 20,971,520 bytes | 28,330,420 | 37,773,896 | 아니오 |

따라서 **프로젝트의 canonical attachment 상한은 18 MiB**로 확정한다.

- 개별 파일 상한: 18 MiB
- 전체 binary 합계 상한: 18 MiB
- 최종 Gmail API base64url raw hard cap: 34 MiB / 35,651,584 bytes
- raw hard cap은 공식 35 MiB보다 1 MiB 낮은 안전 여유

Project 1-3 MVP-02 보고서의 18 MiB가 실제 코드와 일치한다. Project 1-3 MVP-03 보고서의 “20 MiB” 문구는 문서 오류이며, 이 설계의 18 MiB 결정이 이후 구현 기준이다. 이번 설계 MVP에서는 기존 문서나 코드를 수정하지 않는다.

preview에서는 metadata, body 길이, 파일명 및 MIME overhead로 보수적으로 예상한다. 승인 후 실제 MIME을 만들었을 때 34 MiB를 넘으면 발송하거나 LINK로 자동 전환하지 않고 `RAW_MESSAGE_TOO_LARGE`로 실패하고 LINK preview를 다시 생성한다.

## 8. Drive 공유 권한 설계

### 8.1 허용하는 단 하나의 Drive write

```text
permissions.create(
    fileId=<exact approved ID>,
    body={
        "type": "user",
        "role": "reader",
        "emailAddress": <approved To or CC>
    },
    sendNotificationEmail=false,
    supportsAllDrives=true
)
```

- ownership transfer 없음
- `writer`, `commenter`, `organizer`, `fileOrganizer` 금지
- `permissions.update` 금지
- `permissions.delete` 자동 rollback 금지
- `files.create/update/copy/delete` 금지
- rename/move/trash 금지

Google 공식 문서는 permission 생성 시 `type`, `role`이 필요하고 `type=user`에는 `emailAddress`가 필요하다고 설명한다. 같은 파일에 대한 concurrent permission 작업은 지원하지 않으므로 파일별로 순차 실행한다.

참고:

- [Drive API permissions.create](https://developers.google.com/workspace/drive/api/reference/rest/v3/permissions/create)
- [Drive API sharing guide](https://developers.google.com/workspace/drive/api/guides/manage-sharing)

### 8.2 불필요한 permission 방지

preview에서 `permissions.list`의 모든 page를 읽고 다음 fields를 확인한다.

```text
permissions(id,type,role,emailAddress,permissionDetails),nextPageToken
```

정규화된 주소와 일치하는 `type=user` permission이 있고 role이 `reader` 이상이면 새 permission을 만들지 않는다. 기존 writer/commenter/owner 권한을 reader로 낮추지 않는다.

group/domain/anyone 권한만으로 특정 이메일 사용자의 실제 접근 가능 여부를 완전히 증명할 수는 없다. 초기 정책은 직접 user permission이 없으면 해당 주소에 `reader/user` 추가를 계획해 접근을 보장하는 보수적 방식이다. 이로 인해 group/domain을 통한 기존 간접 접근과 direct reader가 함께 존재할 수 있으며, preview에 이를 명시한다.

permission 생성 전 `capabilities.canShare=true`를 확인한다. 조직 정책, 공유 드라이브 역할 또는 외부 공유 제한으로 생성이 거부될 수 있다.

## 9. OAuth write boundary

`permissions.create`가 공식적으로 허용하는 scope는 다음 둘이다.

- `https://www.googleapis.com/auth/drive.file`
- `https://www.googleapis.com/auth/drive`

`drive.file`은 더 좁고 non-sensitive지만 앱이 생성했거나 사용자가 Picker 등을 통해 앱에 명시적으로 연 파일로 접근이 제한된다. 현재 시스템은 SQLite index의 임의 기존 Drive 파일 ID를 사용하며 Google Picker grant가 없다.

따라서 결정은 다음과 같다.

1. **현재 exact-index-ID 구조를 유지하면 기능상 필요한 별도 share token scope는 `drive`다.**
2. `drive.file`을 사용하려면 Project 1-4 범위에 Google Picker 또는 파일별 명시적 app grant를 추가해야 한다.
3. broad `drive` scope는 token 자체로는 넓은 기능을 허용하므로 코드에서 `permissions.create(reader/user)`만 노출하고 정적·동적 deny 테스트를 둔다.
4. scope와 위험을 사용자가 별도로 승인하기 전에는 구현하지 않는다.

새 token 후보:

```text
drive_share_token.json
scope: https://www.googleapis.com/auth/drive
```

기존 token에는 scope를 합치지 않는다.

- `token.json`: metadata index 전용
- `drive_download_token.json`: download read-only 전용
- `gmail_send_token.json`: Gmail send 전용
- `drive_share_token.json`: permission create 전용 후보

Google은 가능한 가장 좁은 scope를 권장하며 `drive.file`을 non-sensitive per-file scope로 설명한다. `drive`는 restricted scope이므로 실제 배포 전에 OAuth verification 및 보안 영향을 확인해야 한다.

참고:

- [Choose Google Drive API scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
- [OAuth 2.0 scopes for Google APIs](https://developers.google.com/identity/protocols/oauth2/scopes)

## 10. 최종 확인 흐름

서버 선택 mode와 permission 변경 대상을 승인 전에 알아야 하므로 preview/send 2단계 계약을 사용한다.

### 10.1 Preview — 변경 없음

`POST /email/send-files/preview`

- Bearer 인증 필요
- `x-openai-isConsequential: false`
- Drive metadata 및 permission 읽기만 수행
- download, permission create, Gmail send 금지
- canonical payload/plan hash 생성
- 짧은 TTL의 `preview_id` 반환

GPT가 사용자에게 표시할 항목:

- To
- 최종 CC 목록
- 제목
- plain-text 본문
- 전체 파일명과 exact IDs
- 파일 개수
- 전체 binary size 또는 “link mode라 다운로드하지 않음”
- 요청 mode와 실제 선택된 delivery mode
- LINK일 때 파일별 viewer permission 추가 예정 주소
- “이대로 실제 발송하고 위 공유 권한을 추가할까요?”

### 10.2 Send — 승인 후 변경

`POST /email/send-files`

- `confirmed`는 strict boolean `true`만 허용
- preview와 동일한 canonical payload 필요
- `preview_id`가 유효하고 plan hash가 같아야 함
- 변경된 항목이 하나라도 있으면 `PREVIEW_STALE`
- permission과 Gmail send는 이 endpoint 안에서만 실행
- `x-openai-isConsequential: true`

OpenAI의 현재 model guidance도 외부 write 같은 작업에 명시적인 승인 경계를 정의하도록 권장한다.

참고: [OpenAI model guidance — autonomy and approval boundaries](https://developers.openai.com/api/docs/guides/latest-model)

## 11. Partial failure 정책

### 11.1 Permission fail-before-send

1. 모든 파일·수신자·mode preflight 완료
2. 최종 승인 확인
3. 필요한 permission을 파일별로 순차 생성
4. 하나라도 실패하면 Gmail을 보내지 않음
5. 이미 생성된 permission은 자동 삭제하지 않음
6. `SHARING_PARTIAL`과 파일/수신자별 상태를 기록
7. 자동 재시도 금지

자동 rollback을 하지 않는 이유:

- rollback은 금지했던 `permissions.delete`를 추가로 허용해야 함
- 기존 permission과 새 permission의 식별 오류가 접근권한 손실로 이어질 수 있음
- rollback 자체가 부분 실패할 수 있음
- 이미 공유된 접근이 잠시 존재했다는 사실을 되돌릴 수 없음

### 11.2 Permission 완료 후 Gmail 실패

- 새 permission은 유지
- 자동 rollback 없음
- definite Gmail failure: `SHARING_COMPLETE_EMAIL_FAILED`
- delivery uncertain: `GMAIL_DELIVERY_UNCERTAIN`
- delivery uncertain은 새 key 우회나 자동 재발송 금지
- 재시도 전 permission을 다시 읽어 기존 생성분을 재사용
- definite failure도 사용자의 새 명시적 승인 없이는 재발송 금지

## 12. Idempotency 확장

canonical payload hash에 최소 다음 값을 포함한다.

```text
normalized To
canonical 정렬된 CC 목록
Subject
Body 전체
순서를 보존한 file_ids
requested mode
resolved delivery mode
file metadata signature (id, mimeType, size, modifiedTime/version)
정렬된 sharing target 계획
preview plan hash
```

정책:

- file ID 순서는 MIME/link 순서이므로 hash에서도 보존
- CC 순서는 의미가 없으므로 canonical 정렬
- duplicate file ID는 사전 거부
- 같은 key와 같은 payload의 `SENT`는 기존 결과 반환
- 같은 key와 다른 payload는 `IDEMPOTENCY_CONFLICT`
- permission 상태는 `(idempotency_key, file_id, recipient_hash)` 단위로 기록
- 이미 `EXISTING` 또는 `CREATED`인 permission은 다시 만들지 않음
- Gmail `SENT` 이전 상태와 permission 결과를 분리 기록

권장 상태:

```text
PREVIEWED
SHARING_PENDING
SHARING_PARTIAL
SHARING_COMPLETE
SEND_PENDING
SENT
EMAIL_FAILED
DELIVERY_UNCERTAIN
```

## 13. FastAPI 계약 변경안

### 13.1 하위 호환

- 기존 `POST /email/send-file` 유지
- 기존 `sendEmailWithAttachment` 유지
- Project 1-3 client와 GPT 대화의 단일 첨부 동작을 깨지 않음

### 13.2 Preview request 후보

```json
{
  "file_ids": ["FILE-1", "FILE-2"],
  "to": "to@example.com",
  "cc": ["cc1@example.com", "cc2@example.com"],
  "subject": "제목",
  "body": "plain-text 본문",
  "mode": "auto"
}
```

Preview response 최소 필드:

```json
{
  "preview_id": "opaque short-lived ID",
  "expires_at": "RFC3339",
  "requested_mode": "auto",
  "delivery_mode": "attachment",
  "file_count": 2,
  "total_size_bytes": 12345,
  "files": [
    {"file_id": "FILE-1", "name": "a.pdf", "size_bytes": 10000}
  ],
  "recipient": "to@example.com",
  "cc": ["cc1@example.com"],
  "sharing_changes": []
}
```

### 13.3 Send request 후보

```json
{
  "preview_id": "opaque short-lived ID",
  "file_ids": ["FILE-1", "FILE-2"],
  "to": "to@example.com",
  "cc": ["cc1@example.com", "cc2@example.com"],
  "subject": "제목",
  "body": "plain-text 본문",
  "mode": "auto",
  "confirmed": true,
  "idempotency_key": "EMAIL-MULTI-UNIQUE-KEY"
}
```

Send response 최소 필드:

```json
{
  "status": "sent",
  "delivery_mode": "link",
  "file_count": 2,
  "files": [
    {"file_id": "FILE-1", "name": "Google 문서", "delivery": "link"}
  ],
  "recipient": "to@example.com",
  "cc": ["cc1@example.com"],
  "message_id": "Gmail message ID",
  "sharing_changes": [
    {"file_id": "FILE-1", "recipient": "cc1@example.com", "action": "created", "role": "reader"}
  ],
  "idempotent_replay": false
}
```

subject/body/file 목록/mode/sharing plan은 preview와 send 사이에 hash로 비교한다. `preview_id`만 신뢰해 서버에 body 원문을 장기 저장하지 않는다.

## 14. 오류 코드 후보

| HTTP | 코드 예 | 의미 |
|---:|---|---|
| 400 | `INVALID_CC`, `TOO_MANY_CC`, `INVALID_FILE_IDS`, `DUPLICATE_FILE_ID`, `TOO_MANY_FILES`, `INVALID_MODE` | request 제한 위반 |
| 400 | `ATTACHMENT_MODE_UNSUPPORTED`, `TOTAL_ATTACHMENT_TOO_LARGE`, `RAW_MESSAGE_TOO_LARGE` | attachment 조건 위반 |
| 400 | `LINK_UNAVAILABLE`, `UNSUPPORTED_FOLDER`, `UNSUPPORTED_SHORTCUT` | link 조건 위반 |
| 400 | `CONFIRMATION_REQUIRED` | strict final approval 없음 |
| 404 | `FILE_NOT_INDEXED`, `DRIVE_FILE_NOT_FOUND`, `PREVIEW_NOT_FOUND` | exact 대상 없음 |
| 409 | `PREVIEW_STALE`, `IDEMPOTENCY_CONFLICT`, `SHARING_PARTIAL`, `GMAIL_DELIVERY_UNCERTAIN` | 계획 변경 또는 부분/불확실 상태 |
| 502 | `SHARING_FAILED`, `DOWNLOAD_FAILED`, `GMAIL_SEND_FAILED` | upstream 실패 |
| 503 | `DRIVE_SHARE_AUTH_FAILED`, `DRIVE_AUTH_FAILED`, `GMAIL_AUTH_FAILED`, `INDEX_UNAVAILABLE` | 인증/서비스 불가 |

## 15. GPT Action 변경안

기존 11개 operation을 보존하고 같은 domain의 병합 OpenAPI schema에 다음 두 operation을 추가한다.

| operationId | Endpoint | Consequential |
|---|---|---|
| `previewEmailWithFiles` | `POST /email/send-files/preview` | `false` |
| `sendEmailWithFiles` | `POST /email/send-files` | `true` |

실제 구현 후 전체 operation은 기존 11개 + 신규 2개 = 13개가 된다. 별도 Action domain 항목을 만들지 않고 기존 `gpt_action_openapi.yaml`에 병합한다.

`sendEmailWithFiles` 설명에는 다음을 명시한다.

- preview 결과와 명확한 최종 승인 후에만 호출
- permission 생성 및 실제 Gmail 발송이 발생
- 자동 재시도 금지
- exact IDs만 허용

## 16. GPT Instructions 변경안

다음 규칙을 기존 이메일 규칙에 추가한다.

1. To는 정확히 1명, CC는 최대 5명, BCC는 지원하지 않는다.
2. 파일은 정확한 ID가 확정된 1~5개만 사용한다.
3. 파일 후보가 하나라도 불명확하면 preview/send를 진행하지 않는다.
4. 먼저 `previewEmailWithFiles`로 최종 CC, mode, 크기 및 permission 계획을 얻는다.
5. `auto` 요청에서도 서버가 결정한 `delivery_mode`를 사용자에게 표시한다.
6. ATTACHMENT이면 전체 파일명, 개수와 총 binary 크기를 표시한다.
7. LINK이면 링크 방식, 파일 수 및 viewer permission이 추가될 주소를 표시한다.
8. 위 전체 내용을 표시한 후 실제 permission 변경과 이메일 발송을 명시해 확인한다.
9. 확인 후에만 `confirmed=true`로 `sendEmailWithFiles`를 호출한다.
10. To, CC, subject, body, file IDs, mode 또는 sharing plan 변경 시 기존 승인을 폐기한다.
11. `SHARING_PARTIAL`, `PREVIEW_STALE`, `DELIVERY_UNCERTAIN`을 자동 재실행하지 않는다.
12. 여러 개의 별도 메일이나 bulk send로 분할하지 않는다.

## 17. Google Workspace native 파일

| 종류 | ATTACHMENT | LINK |
|---|---|---|
| 일반 binary 파일 | 조건부 지원 | 지원 |
| Google Docs/Sheets/Slides/Forms 등 | 지원하지 않음 | `webViewLink`가 있으면 지원 |
| 폴더 | 지원하지 않음 | 초기 구현에서 지원하지 않음 |
| shortcut | 지원하지 않음 | 초기 구현에서 지원하지 않음 |

Native 파일은 LINK에서 export/download하지 않는다. Drive가 반환한 `webViewLink`만 본문에 포함한다.

## 18. 보안 및 로그 원칙

- body 원문을 감사 로그에 기록하지 않음
- file bytes와 MIME raw를 로그에 기록하지 않음
- OAuth access/refresh token 기록 금지
- `PDO_API_KEY`, Cloudflare token, Authorization header 기록 금지
- permission credential 및 OAuth client secret 기록 금지
- To/CC는 지속 로그에서 masking 또는 keyed hash
- state DB에는 canonical payload hash만 저장하고 body 원문은 저장하지 않음
- permission ID와 Gmail message ID는 상태 복구에 필요한 로컬 DB에만 저장하고 일반 로그에서는 생략 또는 hash
- `drive_share_token.json`, preview/state DB 및 audit log는 `.gitignore` 대상
- API 오류 응답은 secret, raw Google error body 및 traceback을 포함하지 않음

## 19. Project 1-4 MVP-02 구현 범위 제안

다음 MVP에서만 구현한다.

1. `drive_share_client.py` 및 분리 token 구조
2. OAuth scope 선택 승인 gate
3. CC validation/normalization
4. 다중 exact file metadata validator
5. 18 MiB 전체 binary 및 34 MiB raw hard cap
6. `auto/attachment/link` resolver
7. read-only preview endpoint와 TTL plan hash
8. `permissions.list` 기반 공유 계획
9. 승인 후 `permissions.create(reader/user)` 제한 wrapper
10. 다중 MIME attachment 및 표준 Drive link body
11. 확장 idempotency/state machine
12. `/email/send-files` endpoint와 두 GPT operations
13. GPT Instructions/OpenAPI 실제 변경
14. unit/integration test에서 Drive write allowlist 검증
15. 실제 permission/email 종단 테스트는 별도 사용자 승인 후 각각 최소 횟수로 실행

구현 순서는 read-only preview와 attachment mode를 먼저 검증한 후, 별도 share token 및 permission write를 마지막 단계에서 활성화한다.

## 20. 최종 설계 결정 요약

- To 1명, CC 최대 5명, 파일 최대 5개
- BCC와 bulk send 금지
- 기존 단일 endpoint 유지, 신규 preview/send v2 추가
- attachment 총 binary 상한은 **18 MiB**
- Gmail API raw hard cap은 **34 MiB**
- auto는 안전한 binary만 attachment, 그 외는 link
- native Google 파일은 LINK만 허용
- Drive 반환 `webViewLink` 사용, URL 직접 조합 금지
- LINK permission은 승인 후 `permissions.create(reader/user)`만 허용
- permission 부분 실패 시 Gmail 발송 금지
- permission 및 Gmail 실패 후 자동 rollback/재시도 금지
- 현재 architecture의 임의 indexed 파일을 공유하려면 별도 `drive` scope token이 기능상 필요
- `drive.file`을 선택하려면 Picker/파일별 app grant 설계가 선행되어야 함
- 이번 MVP에서는 코드, scope, permission 및 이메일을 전혀 변경하지 않음
