# Python Drive Organizer — Project 1-4 MVP-02 진행보고

- 작성일: 2026-08-17 (Asia/Seoul)
- 단계: Project 1-4 — Enhanced Email
- MVP: MVP-02 CC + Multiple Files + Attachment/Link/AUTO 구현
- 상태: **COMPLETED / NEW LINK POLICY E2E VERIFIED**

## 1. 목표와 현재 결과

Project 1-4 MVP-02는 한 명의 To, 최대 다섯 명의 CC, 1~5개의 정확한 Google Drive 파일을 한 번의 Gmail 메시지로 전달하는 기능을 구현한다.

구현된 전송 모드는 다음과 같다.

| 모드 | 동작 |
|---|---|
| `attachment` | 전체 binary가 제한을 만족하면 실제 MIME attachment로 발송 |
| `link` | 파일별 `Anyone with the link / Viewer` 상태를 확인·필요 시 생성한 뒤 Drive 링크 발송 |
| `auto` | attachment 조건을 만족하면 attachment, 그렇지 않으면 link 선택 |

Preview는 외부 변경 없이 최종 수신자·파일·전송 모드·공유 변경 계획을 보여준다. Send는 변경되지 않고 만료되지 않은 Preview와 `confirmed=true`가 모두 있을 때만 실행된다.

## 2. LINK 정책 변경과 이유

MVP-02의 초기 설계는 To/CC 주소마다 다음 direct-user permission을 만드는 방식이었다.

```text
type=user
role=reader
emailAddress=To 또는 CC
```

이 방식은 폐기했다. 실사용 수신자의 대부분이 Gmail/Google 계정이 아니라 Naver, Daum, Outlook, 회사 메일 등 비-Google 이메일 주소이므로, 이메일 주소를 Google Drive 사용자 permission 대상으로 사용하는 방식이 적합하지 않기 때문이다.

변경 이후 LINK mode의 공식 정책은 다음 하나다.

```text
Anyone with the link / Viewer
type=anyone
role=reader
allowFileDiscovery=false
```

To와 CC는 Gmail 수신자 주소로만 사용한다. Google 계정 여부를 조회하거나 판정하지 않으며, Drive permission 계획·생성·idempotency의 수신자 단위 요소로 사용하지 않는다.

## 3. Google Drive 공식 계약 확인

Google Drive API v3 공식 문서에 따라 `Permission` 생성에는 `type`과 `role`이 필요하고, `type=anyone`은 특정 이메일 주소를 요구하지 않는다. `allowFileDiscovery=false`를 명시하여 검색으로 발견되는 공개 상태가 아니라 링크를 가진 사용자만 접근하는 형태로 제한한다.

- [Google Drive 공유 관리 가이드](https://developers.google.com/workspace/drive/api/guides/manage-sharing)
- [Permission 리소스](https://developers.google.com/workspace/drive/api/reference/rest/v3/permissions)
- [permissions.create](https://developers.google.com/workspace/drive/api/reference/rest/v3/permissions/create)
- [File 리소스와 webViewLink](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)

실제 허용된 생성 호출은 개념적으로 다음과 같다.

```python
service.permissions().create(
    fileId=file_id,
    body={
        "type": "anyone",
        "role": "reader",
        "allowFileDiscovery": False,
    },
    fields="id,type,role,allowFileDiscovery",
    supportsAllDrives=True,
)
```

`type=anyone`은 특정 Google 사용자를 초대하는 요청이 아니므로 `emailAddress`와 `sendNotificationEmail`을 전달하지 않는다. 실제 전달 알림은 Gmail API 메시지가 담당한다.

## 4. 최종 처리 구조

```text
ChatGPT GPT
  │
  ├─ POST /email/send-files/preview  [비 consequential]
  │    ├─ Bearer 인증
  │    ├─ SQLite exact file_id 확인
  │    ├─ Drive metadata 및 permission 현재 상태 읽기
  │    ├─ attachment/link 모드 결정
  │    ├─ 파일별 sharing_changes 계산
  │    └─ 10분 유효 Preview와 hash 저장
  │
  └─ 사용자 명시적 승인
       │
       └─ POST /email/send-files     [consequential]
            ├─ confirmed=true 강제
            ├─ Preview 만료·변경·idempotency 재검증
            ├─ LINK: 필요한 파일에만 anyone/reader 생성
            ├─ LINK: 적용 후 Drive API에서 webViewLink 재조회
            ├─ ATTACHMENT: 제한 내 binary 다운로드
            └─ Gmail API로 메시지 1건 발송
```

## 5. Preview 계약

LINK Preview는 수신자별 permission 계획을 만들지 않는다. 응답에 다음 값을 파일별로 제공한다.

- `sharing_mode`: `anyone_with_link_reader`
- `sharing_changes[].file_id`
- `sharing_changes[].action`
  - `existing`: 이미 비검색형 `anyone/reader` 존재, 변경 없음
  - `create_anyone_reader`: 승인 후 생성 예정
- `sharing_changes[].permission_type`: 항상 `anyone`
- `sharing_changes[].role`: 항상 `reader`
- `sharing_changes[].allow_file_discovery`: 항상 `false`

ATTACHMENT Preview의 `sharing_mode`는 `none`이고 `sharing_changes`는 비어 있다.

기존 `anyone` permission이 `reader`보다 넓거나 `allowFileDiscovery=true`이면 자동 축소·수정·삭제하지 않고 `LINK_PERMISSION_TOO_BROAD`로 중단한다.

## 6. 반드시 표시하는 사용자 경고

GPT는 LINK mode를 자동 승인하지 않는다. 다음 내용을 보여준 뒤 사용자의 명시적 승인을 받아야 한다.

> 이 파일은 링크를 가진 누구나 열람할 수 있도록 Google Drive 공유 설정이 변경됩니다. 메일을 받은 사람이 링크를 다른 사람에게 전달하면 그 사람도 파일을 열 수 있습니다.

확인 화면에는 다음 네 가지 의미가 반드시 포함된다.

- Drive 링크 방식
- 파일 개수
- **링크가 있는 모든 사용자에게 Viewer 공개**
- 링크 전달 시 제3자도 접근 가능

## 7. Drive write 허용·금지 경계

LINK Send에서 허용하는 Drive write는 파일별 다음 한 종류뿐이다.

- `permissions.create`
- `type=anyone`
- `role=reader`
- `allowFileDiscovery=false`

같은 비검색형 `anyone/reader` permission이 이미 존재하면 새로 만들지 않는다. 여러 파일은 순차 처리하여 같은 파일에 동시 permission 작업을 하지 않는다.

다음 작업은 구현하지 않았고 허용하지 않는다.

- To/CC 대상 `user` permission 생성
- `group`, `domain` permission
- `writer`, `commenter`, `owner`
- `permissions.update`, `permissions.delete`
- `files.create`, `files.update`, `files.copy`, `files.delete`
- 파일 이름 변경, 이동, 복사, 휴지통 이동, 삭제

## 8. webViewLink 정책

링크 URL은 file ID로 직접 조합하지 않는다. Drive API가 반환한 `webViewLink`만 사용한다.

Send 시에는 permission 처리가 모두 끝난 후 파일 메타데이터를 다시 읽고, 그 시점에 Drive API가 반환한 `webViewLink`로 메일 본문을 만든다. post-share 링크 재조회가 실패하면 Gmail을 호출하지 않는다.

## 9. To와 CC

- To: 정확히 1명
- CC: 선택 사항, 정규화 후 최대 5명
- BCC: 지원하지 않음
- Gmail, Naver, Daum, Outlook, 회사 메일 등 정상 이메일 형식이면 허용
- Google 계정 여부 확인 없음
- To와 중복되는 CC 및 CC 내부 중복 제거
- CR/LF, 쉼표, 세미콜론 등 header injection 위험 입력 거부

## 10. ATTACHMENT와 AUTO

ATTACHMENT mode는 Drive 공유 설정을 전혀 변경하지 않는다.

- 파일 1~5개
- 전체 binary 합계 최대 18 MiB
- 개별 파일도 18 MiB 초과 금지
- 추정 및 최종 Gmail raw message 최대 34 MiB
- Google native file, 다운로드 불가 파일, 크기 불명 파일은 attachment 불가
- 파일 순서를 MIME attachment 순서로 보존

AUTO mode는 attachment 조건을 모두 만족하면 ATTACHMENT를 선택하고, 그 외에는 LINK를 선택한다. LINK 선택은 수신자의 Google 계정 여부와 무관하다.

## 11. Partial failure와 재시도 정책

여러 파일 중 `anyone/reader` permission 생성이 하나라도 실패하면 Gmail을 보내지 않는다.

- 이미 성공한 permission은 자동 삭제하지 않음
- 자동 rollback 없음
- 자동 재시도 없음
- 일부 성공 후 실패: `SHARING_PARTIAL`
- 첫 permission부터 실패: `SHARING_FAILED`
- 공유 후 `webViewLink` 재조회 실패: Gmail 호출 없음

사용자는 현재 Drive 공유 상태를 다시 확인하고 새 Preview부터 시작해야 한다.

## 12. Idempotency와 Preview 무결성

공유 계획 hash는 수신자별 permission이 아니라 다음 파일 단위 상태를 포함한다.

- `file_id`
- 현재 `anyone/reader` 상태
- 생성 필요 여부
- `permission_type=anyone`
- `role=reader`
- `allow_file_discovery=false`

최종 요청 fingerprint에는 Preview ID, 정렬된 파일 ID, 정규화한 To/CC, subject, body, 요청·결정 모드, Preview plan hash, 메타데이터 signature hash, sharing plan hash를 포함한다.

Preview 유효시간은 10분이다. 만료 또는 메타데이터·permission 계획 변경이 있으면 Send를 거부하고 새 Preview 승인을 요구한다. 동일 idempotency key의 성공 결과는 외부 API를 다시 호출하지 않고 재생한다.

## 13. OAuth와 secret 분리

| 용도 | OAuth scope | token 파일 |
|---|---|---|
| 기존 Drive 인덱스 | `drive.metadata.readonly` | `token.json` |
| 첨부·링크 메타데이터 읽기 | `drive.readonly` | `drive_download_token.json` |
| Gmail 발송 | `gmail.send` | `gmail_send_token.json` |
| 제한된 공유 permission 생성 | `drive` | `drive_share_token.json` |

Drive 공유용 scope가 넓기 때문에 전용 token으로 격리하고, 코드의 write 경로를 `anyone/reader` 생성 한 종류로 제한했다. 모든 token, `credentials.json`, `.env`, `data/`, `logs/`는 Git 제외 대상이다.

실제 API key, OAuth access/refresh token, Cloudflare token, client secret은 이 문서와 OpenAPI/GPT Instructions에 기록하지 않았다.

## 14. API와 GPT Action

새 Enhanced Email endpoint:

| Method | Path | operationId | consequential |
|---|---|---|---|
| POST | `/email/send-files/preview` | `previewEmailWithFiles` | false |
| POST | `/email/send-files` | `sendEmailWithFiles` | true |

기존 단일 attachment endpoint `POST /email/send-file` 및 기존 조회 Action 10개를 보존했다. 전체 OpenAPI operationId는 13개이며 중복이 없다. HTTPS server와 BearerAuth도 기존 계약을 유지한다.

## 15. 생성·수정 파일

이번 MVP-02 구현 및 정책 변경의 관련 파일:

- `drive_share_client.py`: 전용 Drive share OAuth와 allowlist permission client
- `enhanced_email_service.py`: Preview, mode 결정, 파일 단위 공유 계획, idempotency, partial failure, Gmail 전송
- `api_server.py`: Enhanced Email request/response와 두 endpoint
- `gpt_action_openapi.yaml`: GPT Action 계약
- `GPTS_INSTRUCTIONS.md`: Preview → 경고 → 승인 → Send 원칙
- `test_enhanced_email_service.py`: 서비스·권한 allowlist·partial failure·post-share 링크 테스트
- `test_api_server.py`: FastAPI 계약과 오류 매핑 테스트
- `Project-1-4_MVP-02_진행보고.md`: 본 진행보고서

외부 Python 패키지를 새로 추가하지 않았다. 기존 Google API client, FastAPI, Uvicorn 및 Python 표준 `sqlite3`를 사용한다.

## 16. 검증 결과

정책 변경 후 다음 검증을 완료했다.

- Python compile: 통과
- 전체 unittest: **106개 통과**
- YAML parse: 통과
- OpenAPI 3.1 validation: 통과
- duplicate operationId: 0
- HTTPS server: 통과
- BearerAuth: 통과
- FastAPI 실제 parameter/response contract: 통과
- Preview consequential=false / Send consequential=true: 통과
- Drive write allowlist 정적 검사: 통과
- 금지된 permission/file write 정적 검사: 통과
- token Git ignore 확인: 통과
- 추적 대상 secret 패턴 검사: 통과
- `git diff --check`: 통과
- Windows 예약 작업 `Python Drive Organizer API`: 새 코드로 재시작 후 `Running`
- 로컬 health `http://127.0.0.1:8000/health`: HTTP 200
- 공개 health `https://drive-api.sungwony.pe.kr/health`: HTTP 200

주요 자동 테스트 시나리오:

- 비-Google To와 비-Google CC 허용
- 수신자 수와 무관하게 파일당 최대 한 개의 `anyone/reader` 계획
- 기존 비검색형 `anyone/reader` 재사용
- 공개 검색형 또는 더 넓은 anyone permission 자동 변경 거부
- 생성 body가 `anyone/reader/allowFileDiscovery=false`로 고정됨
- `emailAddress`, `sendNotificationEmail` 미사용
- permission 일부 실패 시 Gmail 미호출 및 rollback 없음
- permission 적용 후 반환된 `webViewLink` 사용
- post-share 링크 재조회 실패 시 Gmail 미호출
- Preview stale, 만료, idempotency 충돌·재생
- ATTACHMENT mode에서 Drive permission 변경 없음

## 17. 실제 종단 테스트 상태

이전 attachment 방식의 비-Google 수신 테스트는 성공했다. 폐기된 direct-user LINK 방식에서 비-Google 주소를 permission 대상으로 사용하던 실패 테스트는 공식 시나리오에서 제거했다.

새 공식 시나리오는 다음과 같다.

- 비-Google To
- 비-Google CC
- 파일별 `anyone/reader` permission 생성 또는 기존 상태 재사용
- 적용 후 Drive API `webViewLink`로 Gmail 발송

Anyone-with-link는 공개 범위가 넓어지는 consequential 변경이므로, 실제 파일에 대한 read-only Preview를 먼저 제시하고 사용자가 새 경고를 확인해 명시적으로 승인한 뒤에만 실행했다.

2026-08-17 실제 대상 3개에 대한 read-only Preview는 성공했다.

| 파일 | 크기 | 현재 계획 |
|---|---:|---|
| `CCTV R.5.dwg` | 15,847,951 bytes | `create_anyone_reader` |
| `P5 그린동 종합상황실 모니터링용 가설 CCTV설치 공사_260210 (수정).dwg` | 16,250,703 bytes | `create_anyone_reader` |
| `CDC 모듈샾장 900KVA 변대설치_작업도면.dwg` | 21,743,412 bytes | `create_anyone_reader` |

- 전체 크기: 53,842,066 bytes
- 요청 모드: `auto`
- 결정 모드: `link`
- 공유 모드: `anyone_with_link_reader`
- 세 파일 모두 Preview 시점에 비검색형 `anyone/reader`가 없어 생성 필요
- Preview 중 permission 생성 없음
- Preview 중 Gmail 발송 없음

사용자 승인 후 만료 가능성을 제거하기 위해 새 read-only Preview를 다시 생성했고, 승인한 계획과 완전히 같은 경우에만 Send를 실행하는 가드를 적용했다.

### 실제 승인·발송 결과

- 사용자 명시적 승인: 확인
- 새 Preview 재검증: 통과
- 세 파일 `permissions.create`: 모두 성공
- 생성 permission: `type=anyone`, `role=reader`, `allowFileDiscovery=false`
- permission 적용 후 Drive API `webViewLink` 재조회: 세 파일 모두 성공
- Gmail 발송: 성공
- To: `sungwony1@naver.com`
- CC: `swchoi@hlbkorea.com`
- 제목: `[Python Drive Organizer] 실제 작업도면 Drive 링크 테스트`
- 메시지 수: 1건
- idempotent replay: 아님, 최초 실행
- 자동 rollback·자동 재시도: 수행하지 않음
- raw Gmail message ID: 문서에 기록하지 않음

## 18. 현재 제한사항과 다음 단계

- Link를 전달받은 제3자도 접근할 수 있으므로 민감 파일에는 부적합할 수 있음
- Workspace 관리자 정책이 anyone sharing을 막으면 `SHARING_FAILED`
- 기존의 더 넓은 anyone permission은 자동 축소하지 않음
- 생성된 permission의 자동 회수·만료·삭제 기능 없음
- 자동 rollback·재시도 없음
- 파일 내용 검사, 바이러스 검사, DLP 판정 없음
- Drive 링크 접근은 Google Drive 및 조직 정책에 의존
- 링크 수신 및 열람 여부는 To/CC 수신함에서 최종 사용자 확인 필요

다음 단계는 To/CC 수신함에서 메일 수신과 세 Drive 링크의 열람 가능 여부를 확인하는 것이다. 이후 다른 파일을 발송할 때도 반드시 새 Preview → 위험 경고 → 명시적 승인 → Send 순서를 반복한다.

## 19. GPT Action `PREVIEW_NOT_FOUND` 오류 보강 (2026-08-17)

실제 GPT에서 `previewEmailWithFiles` 성공 직후 사용자가 발송을 승인했지만, `sendEmailWithFiles`가 HTTP 404 `PREVIEW_NOT_FOUND`를 반환한 사례를 점검했다. 로컬 서비스 흐름과 SQLite 저장은 정상이었으므로 GPT Action이 직전 응답의 opaque `preview_id`를 문자 단위로 그대로 재사용하도록 Instructions와 OpenAPI 계약을 강화했다.

- 사용자 확인 요약에 `Preview ID` 표시
- 직전 성공 preview의 정확한 `preview_id` 보존 및 재사용 의무화
- ID 생성·추측·요약·일부 생략·대체 금지
- 정확한 ID를 확인할 수 없으면 Send 금지 및 새 Preview/승인 요구
- preview 저장과 send 수신 시 exact ID, 절대 state DB 경로, 프로세스 ID와 작업 디렉터리를 별도 진단 로그에 기록
- lookup 결과를 존재/만료/ID 일치 여부로 구분
- preview cleanup이 구현되어 있지 않음을 진단 필드와 테스트로 확인
- 이메일 주소, 본문, 첨부 bytes, idempotency key 및 인증 secret은 진단 로그에서 제외

상태 DB는 모듈 위치를 기준으로 계산한 절대경로 `data/enhanced_email_state.db`를 사용한다. 새 프로세스와 다른 working directory에서도 같은 DB를 열며, 만료 전 preview는 서비스 재시작 후에도 유지되는 것을 별도 프로세스 테스트로 확인했다. 여러 worker도 이 절대경로의 동일 SQLite 파일을 사용한다.

추가 검증 결과:

- 별도 HTTP client로 Preview와 Send를 순차 호출하고 exact ID로 발송 성공
- `preview_id` 한 글자 변경 시 `PREVIEW_NOT_FOUND`
- 만료 preview는 삭제되지 않고 `PREVIEW_EXPIRED`로 구분
- YAML parse 및 OpenAPI 3.1 validation 통과
- operation 13개, duplicate operationId 0개
- HTTPS server, BearerAuth, consequential 플래그 유지
- 전체 자동 테스트 120개 통과
- FastAPI 재시작 후 로컬 및 공개 health HTTP 200

기존 To/CC, ATTACHMENT/LINK/AUTO, `anyone/reader`, 명시적 승인, idempotency 및 Google Drive 쓰기 제한 정책은 변경하지 않았다.
