# Python Drive Organizer — 프로젝트 최종 진행보고

- 최종 갱신일: 2026-08-18 (Asia/Seoul)
- 프로젝트 위치: `C:\Users\HLB\Documents\Python-Drive-Organizer`
- GitHub: `https://github.com/sungwony3-lab/Python-Drive-Organizer`
- Public API: `https://drive-api.sungwony.pe.kr`
- API 버전: `1.7-MVP01.1`
- 최종 상태: **PROJECT COMPLETED**
- 검증 상태: **실제 Google Drive, 이메일, Contacts, GPT Actions 및 파일 다운로드 종단 검증 완료**

이 문서는 프로젝트 시작부터 Project 1-7 완료까지의 목표, 구현, 운영 구조, 보안 경계, 실제 검증 결과와 현재 운영 상태를 하나로 정리한 최종 보고서다. 실제 API key, OAuth token, Cloudflare token 및 OAuth client secret은 포함하지 않는다.

---

## 1. 프로젝트 목적과 최종 결과

Python Drive Organizer는 Google Drive의 파일·폴더 메타데이터를 안전하게 인덱싱하고, 파일명 규칙 분석·그룹화·검색·Tree 문서 생성·이메일 전달·주소록 조회를 GPT 자연어 Action으로 사용할 수 있게 만든 Windows 기반 개인 자동화 시스템이다.

최종적으로 다음 기능을 완성했다.

- Google Drive API v3 메타데이터 인덱싱
- SQLite 증분 동기화와 삭제 감지
- Revision/Copy 파일명 Parser 및 파일 그룹화
- 파일·폴더·Revision·Copy·그룹·최근 항목 검색
- FastAPI + Bearer 인증의 로컬/공개 API
- Cloudflare Tunnel HTTPS와 Windows 자동 시작
- GPT Builder Actions 및 8,000자 이내 Instructions
- Gmail 단일/다중 첨부, CC, 일반 메일, Drive Link 메일
- Google Sheets 기반 Contact Directory 동기화와 검색
- 매일 08:00 Drive/Contacts 갱신
- 전체 Drive Tree pagination 및 TXT/DOCX/XLSX Export
- `openaiFileResponse`를 통한 GPT 대화창 파일 반환과 다운로드
- 새 Windows PC 이전·설치·검증 자동화

## 2. 최종 아키텍처

```text
Google Drive metadata ── OAuth ──> Drive index pipeline
                                      │
                                      ├─ SQLite data/drive_index.db
                                      ├─ Parser / Grouping
                                      └─ Daily Refresh 08:00

Google Sheets 주소록 ── readonly OAuth ──> Contacts sync ──> SQLite

PC / Mobile / Custom GPT
        │
        └─ HTTPS + BearerAuth
              │
              ▼
      Cloudflare named Tunnel
              │
              ▼
      FastAPI 127.0.0.1:8000
        ├─ Drive/Contacts SQLite 조회
        ├─ Gmail 발송 Preview → 승인 → Send
        ├─ 제한된 Drive anyone/reader 공유
        └─ Drive Tree Export → openaiFileResponse
```

운영 원칙:

- SQLite가 GPT 조회와 Tree Export의 source of truth다.
- 일반 조회는 실시간 Drive 호출이 아니라 마지막 성공 scan snapshot 기준이다.
- FastAPI는 localhost에만 bind하고 외부 연결은 Cloudflare Tunnel이 담당한다.
- 모든 보호 endpoint는 HTTP Bearer 인증을 요구한다.
- 이메일과 공유는 Preview·명시적 승인·idempotency 검증 후에만 실행한다.

## 3. 단계별 완료 이력

### 3.1 최초 1차 목표 — Drive Organizer MVP-01~07

#### MVP-01 — Python 프로젝트 기반

- Git 저장소, `.venv`, `main.py`, `requirements.txt`, `.gitignore`, README 구성
- PowerShell 실행 환경과 Python 진입점 검증
- 초기 커밋 `3675ed8 MVP-01 initial Python project setup`

#### MVP-02 — Google Drive OAuth + 읽기 연결

- Google Drive API v3 연결
- `credentials.json`으로 최초 브라우저 승인
- `token.json` 재사용 및 refresh 처리
- `drive.metadata.readonly` 범위로 파일·폴더 메타데이터 조회
- 파일/폴더 항목 출력과 한글 UTF-8 콘솔 대응

#### MVP-03 — SQLite Drive Index

- `files`, `folders`, `scan_state` schema
- Drive 전체 페이지 순회 및 파일/폴더 분리 UPSERT
- `SCAN-YYYYMMDD-HHMMSS` 상태 기록
- `RUNNING → COMPLETED/FAILED` 트랜잭션 처리
- ID 기본키를 이용한 중복 방지

#### MVP-04 — 증분 동기화

- INSERT/UPDATE/SKIP/DELETE 판정
- `last_seen_scan_id` 기반 현재 scan 확인
- 정상 전체 scan에서만 DB 삭제 감지 실행
- 실패 scan rollback 및 기존 데이터 보존
- 실제 Drive에서 업로드, 이름 변경, 폴더 이동, 휴지통 이동을 수행해 각 상태 전환 검증

#### MVP-05 — File Name Parser

- Revision, Copy, 단일 괄호 Copy, 한국어 이름 처리
- 파일명 정규화, base name, revision/copy 번호와 자동 분류 저장
- 기존 DB 전체 backfill 및 결정성 검증

#### MVP-06 — File Grouping

- `file_groups`, `file_group_members` schema
- parent, extension, normalized base name 기반 deterministic grouping
- 동일 파일의 다중 그룹 소속 금지
- 안전한 전체 재구성과 rollback 검증

#### MVP-07 — Search & Report

- 파일·폴더 이름 검색
- 직접/재귀 폴더 목록과 경로 계산
- Revision, Copy, auto-delete 분류, 그룹, 최근 항목 조회
- 특정 root/depth/파일 포함 Tree CLI
- SQLite와 Drive 데이터 변경 없는 읽기 전용 검색 검증

### 3.2 Project 1-2 — Local/Public API와 GPT Actions

#### MVP-01 — Local Read-Only API

- FastAPI 기반 `/health`, `/status`, 검색·그룹·Tree endpoint
- SQLite read-only connection
- Swagger `/docs` 및 OpenAPI 제공

#### MVP-02 — API Authentication / Security

- `PDO_API_KEY` 환경변수 기반 Bearer 인증
- constant-time key 비교
- secret 비노출 오류 응답

#### MVP-03 — Cloudflare Tunnel + Windows 자동 시작

- Public HTTPS `https://drive-api.sungwony.pe.kr`
- Cloudflare named tunnel을 localhost FastAPI에 연결
- Windows 로그인 시 FastAPI 자동 시작
- 재부팅 후 자동 복구 검증

#### MVP-04 — GPTs Actions

- GPT Builder용 OpenAPI와 Instructions 작성
- GPT Builder parameter `$ref` 호환성 수정
- HTTPS/Bearer/operationId/FastAPI 계약 검증

#### MVP-05 — PC/Mobile 자연어 종단 테스트

- PC와 모바일에서 상태, 검색, Tree, Revision, Copy, 그룹 자연어 요청 검증
- SQLite snapshot 기준 응답과 ID 추측 금지 원칙 확인

#### Daily Refresh

- Windows Task Scheduler에서 매일 08:00 실행
- Drive scan → Parser → Grouping 순서
- Contacts sync는 Drive pipeline과 독립적으로 실행·기록
- 중복 실행 방지와 `StartWhenAvailable` 적용

### 3.3 Project 1-3 — Email Sending

- Drive 다운로드용 `drive.readonly` OAuth token 분리
- Gmail 발송용 `gmail.send` OAuth token 분리
- 정확한 Drive file ID의 일반 binary 파일 첨부
- Gmail API MIME message 발송
- `POST /email/send-file` 및 `sendEmailWithAttachment`
- `confirmed=true`와 idempotency key 강제
- 인증·다운로드·발송 오류의 의미 있는 코드와 secret 비노출
- 로컬, Cloudflare HTTPS, GPT Builder 및 실제 수신함 종단 성공

### 3.4 Project 1-4 — Enhanced Email

- To 1명, CC 최대 5명, 파일 1~5개
- ATTACHMENT, LINK, AUTO 모드
- Attachment 합계 최대 18 MiB 및 Gmail raw size 안전장치
- 10분 유효 Preview와 exact `preview_id` 기반 승인
- 재시작·다른 working directory에서도 유지되는 SQLite preview state
- plain-text 본문과 첨부 순서 보존

LINK mode 정책:

```text
type=anyone
role=reader
allowFileDiscovery=false
```

- 비-Google 이메일도 링크 열람 가능
- 파일별 기존 permission 확인 후 필요한 경우에만 `permissions.create`
- Google Drive가 반환한 `webViewLink` 사용
- 링크 전달 시 제3자도 열 수 있다는 경고와 명시적 승인 필수
- 여러 파일 중 permission 생성이 하나라도 실패하면 Gmail 발송 중단
- `permissions.update/delete` 및 파일 생성·수정·이동·복사·삭제 금지

실제 Naver/회사 이메일, CC, 다중 첨부와 실제 DWG 작업도면 발송을 검증했다. Gmail API 응답과 실제 수신함을 함께 확인했으며 자동 재발송은 하지 않는다.

### 3.5 Project 1-5 — Windows Migration & Setup

- `setup_windows.ps1`: 가상환경 재구성, requirements 설치, 예약 작업 설정
- `verify_install.ps1`: 비파괴 설치·설정·DB·API 검증
- `prepare_migration.ps1`: private 파일 존재 여부와 이전 준비 검사
- `uninstall_tasks.ps1`: 명시적 요청에서만 예약 작업 제거
- `MIGRATION_GUIDE.md`, `MANUAL_ONLINE_SETUP.md`
- 새 PC용 체크리스트 Markdown/DOCX
- secret과 OAuth token은 Git이나 일반 archive에 넣지 않고 별도 보안 이전
- setup 재실행 idempotency 및 clean-install 전체 테스트 검증

### 3.6 Project 1-6 — Contact Directory

- Google Sheets `spreadsheets.readonly` OAuth
- 전용 `contacts_sheet_token.json`
- `contacts`, `contacts_sync_state`, `contacts_sync_issues` schema
- 이름·소속·직급·이메일·전화번호 정규화
- invalid email과 중복 conflict를 보존하되 발송 대상에서는 차단
- 동명이인 후보를 자동 선택하지 않는 deterministic ranking
- `searchContacts`, `getContact`, `getContactsStatus`
- 이름 기반 To/CC는 검색 후 exact contact ID로 발송 직전 재조회
- 직접 입력 이메일과 주소록 연락처 혼합 지원
- Daily Refresh에 Contacts sync 독립 통합

파일이나 Drive Link가 없는 일반 메일을 위해 다음 흐름도 구현했다.

```text
previewTextEmail → 사용자 승인 → sendTextEmail
```

첨부 없음, Drive Link 없음, exact preview ID, idempotency 및 주소록 최신성 검증을 유지한다.

### 3.7 Project 1-7 — Drive Tree Export

- 기존 Tree traversal을 `build_full_tree()`로 공통화
- 폴더 우선, 이름과 ID tie-break를 사용한 deterministic ordering
- `POST /folders/tree/page`의 서명된 opaque cursor pagination
- full Drive 또는 subtree, folder-only, max depth 옵션
- 서버가 전체 SQLite Tree를 직접 생성해 GPT 응답 크기 제한 회피
- UTF-8 TXT, Word DOCX, Excel XLSX 생성
- `exports/` 저장 및 Git 제외
- Bearer 인증 raw download `GET /exports/{export_id}` 유지

GPT Actions 파일 반환 오류는 다음 전용 Action으로 수정했다.

```text
exportDriveTree
→ exact export_id
→ returnDriveTreeExport
→ openaiFileResponse
→ GPT conversation file
```

- `POST /exports/{export_id}/openai-file`
- filename, 정확한 MIME, base64 content 반환
- 원본 파일 10,000,000 bytes 초과 시 `GPT_FILE_TOO_LARGE`
- base64와 Tree 내용은 로그에 기록하지 않음
- TXT/DOCX/XLSX decode 결과와 원본 bytes 일치
- 실제 GPT 대화창 DOCX 첨부 표시와 사용자 다운로드 성공

## 4. 현재 API와 GPT Action

FastAPI에는 health를 포함한 23개 path가 있으며 GPT Builder schema에는 21개의 고유 operationId가 있다.

### Drive와 Tree

- `getDriveStatus`
- `searchFiles`
- `searchFolders`
- `listFolderChildren`
- `getFolderTree`
- `getDriveTreePage`
- `exportDriveTree`
- `returnDriveTreeExport`
- `listRevisions`
- `listCopies`
- `listAutoDeleteFiles`
- `listFileGroups`
- `listRecentFiles`

### Contacts

- `searchContacts`
- `getContactsStatus`
- `getContact`

### Email

- `sendEmailWithAttachment`
- `previewTextEmail`
- `sendTextEmail`
- `previewEmailWithFiles`
- `sendEmailWithFiles`

조회와 Preview는 non-consequential이다. 실제 Gmail 발송과 승인 후 Drive permission 생성 가능성이 있는 Send는 consequential이다.

## 5. 현재 SQLite 상태

DB: `data/drive_index.db`

- 크기: 13,000,704 bytes
- `PRAGMA integrity_check`: `ok`

| 테이블 | 현재 행 수 |
|---|---:|
| `files` | 8,812 |
| `folders` | 2,394 |
| `file_groups` | 8,799 |
| `file_group_members` | 8,812 |
| `contacts` | 17 |
| `scan_state` | 25 |
| `contacts_sync_state` | 7 |

마지막 성공 Drive snapshot:

- scan ID: `SCAN-20260817-132427`
- status: `COMPLETED`
- files: 8,812
- folders: 2,394
- finished: `2026-08-17T04:24:50Z`

전체 Tree Export:

- 전체 node: 11,206
- 폴더: 2,394
- 파일: 8,812
- page size 500 기준 23페이지
- pagination 누락 0, 중복 0
- TXT/DOCX/XLSX 생성·개방·다운로드 성공

마지막 Contacts sync:

- sync ID: `CONTACTS-20260818-080002`
- status: `COMPLETED`
- rows/valid: 17/17
- inserted: 1
- unchanged: 16
- invalid/conflicts: 0/0

## 6. OAuth, 권한과 secret 분리

| 용도 | OAuth scope | token 파일 |
|---|---|---|
| Drive 메타데이터 scan | `drive.metadata.readonly` | `token.json` |
| 이메일 첨부 다운로드 | `drive.readonly` | `drive_download_token.json` |
| Gmail 발송 | `gmail.send` | `gmail_send_token.json` |
| LINK permission 생성 | `drive` | `drive_share_token.json` |
| Google Sheets 주소록 | `spreadsheets.readonly` | `contacts_sheet_token.json` |

쓰기 경계:

- 일반 Drive indexing, 검색, Contacts, Tree Export는 읽기 전용이다.
- 이메일 attachment는 파일 bytes만 읽는다.
- Drive 쓰기의 유일한 예외는 승인된 LINK send에서의 비검색형 `anyone/reader` `permissions.create`다.
- `files.create/update/copy/delete`, rename, move, trash는 없다.
- Gmail 발송은 사용자가 Preview 내용을 명시적으로 승인한 경우에만 실행한다.

Git 제외 대상:

- `.env`
- `credentials.json`
- 모든 OAuth token JSON
- `data/`
- `logs/`
- `exports/`
- `.venv/`
- secret을 포함할 수 있는 로컬 migration 복사본

## 7. Windows 운영 상태

### FastAPI

- Task: `Python Drive Organizer API`
- 상태: `Running`
- 명령: `.venv\Scripts\python.exe -m uvicorn api_server:app --host 127.0.0.1 --port 8000`
- Windows 로그인 시 자동 시작
- 현재 local/public API version: `1.7-MVP01.1`

### Daily Refresh

- Task: `Python Drive Organizer Daily Refresh`
- 상태: `Ready`
- 다음 실행: 2026-08-19 08:00 (Windows local time)
- 명령: `.venv\Scripts\python.exe daily_refresh.py`
- 중복 실행: IgnoreNew
- PC가 꺼져 있으면 다음 사용 가능 시점에 실행

## 8. 테스트와 실제 종단 검증

최종 자동 검증:

- 전체 unit/integration tests: **198개 모두 통과**
- `pip check`: 이상 없음
- Python compile: 통과
- OpenAPI 3.1 validation: 통과
- operationId 중복: 0
- GPT Instructions: 7,329자, 8,000자 제한 충족
- FastAPI/OpenAPI request/response 계약 일치
- SQLite hash read-only 검사 및 integrity check 통과
- 로그의 secret·본문·Tree·base64 비노출 확인

실제 환경 검증:

- Google Drive scan과 증분 INSERT/UPDATE/SKIP/DELETE
- 파일명 Parser와 Grouping
- PC/mobile GPT 자연어 검색
- Cloudflare 공개 HTTPS와 Bearer 인증
- Gmail 단일/다중 첨부, 일반 메일, CC, Drive Link 발송 및 실제 수신함
- 비-Google 이메일의 anyone-with-link 접근
- Contacts Sheet sync와 이름 기반 수신자 선택
- Windows 재부팅 후 자동 시작
- TXT/DOCX/XLSX Tree Export
- GPT `openaiFileResponse` DOCX 첨부 및 사용자 다운로드

## 9. 현재 제한과 운영 후속 항목

- Drive/Contacts 조회 응답은 마지막 SQLite snapshot 기준이며 실시간 Drive 조회가 아니다.
- Drive 파일 내용 검색·분석 기능은 없다.
- BCC와 bulk email은 지원하지 않는다.
- Enhanced Email은 To 1명, CC 최대 5명, 파일 최대 5개다.
- LINK mode는 링크를 전달받은 제3자도 파일을 열 수 있으므로 항상 명시적 승인이 필요하다.
- GPT 반환 파일은 개별 10MB 제한이다.
- export 자동 만료·정리 정책은 구현하지 않았다.

### 2026-08-18 Daily Refresh 운영 알림

오늘 08:00 예약 실행에서 Contacts sync는 `COMPLETED`였으나 Drive metadata scan은 `invalid_grant: Token has been expired or revoked`로 `FAILED` 기록됐다. 마지막 성공 SQLite snapshot은 그대로 보존됐고 API·GPT·Tree Export는 해당 snapshot으로 정상 동작한다.

Daily Refresh의 Drive pipeline을 다시 활성화하려면 만료된 `token.json`을 프로젝트 밖의 안전한 위치로 먼저 이동한 뒤 로컬 PowerShell에서 다음 명령을 한 번 실행하고 브라우저 OAuth 승인을 완료한다. 현재 인증 코드는 만료 token의 refresh가 `invalid_grant`로 실패하면 자동으로 브라우저 승인으로 전환하지 않으므로 기존 token 파일 분리가 먼저 필요하다.

```powershell
.\.venv\Scripts\python.exe main.py
```

새 `token.json`이 생성되면 `python daily_refresh.py`를 한 번 수동 실행해 Drive scan, Parser, Grouping과 Contacts sync를 재검증한다. Gmail, Drive download/share, Contacts token은 용도별 별도 파일이므로 이 조치와 분리되어 있다. token 내용은 문서나 Git에 기록하지 않는다.

## 10. 주요 산출물

최종 운영 파일:

- `main.py`, `daily_refresh.py`
- `api_server.py`
- `database.py`, `scanner.py`
- `name_parser.py`, `file_grouping.py`, `search_service.py`
- `contacts_sheet_client.py`, `contacts_sync.py`, `contacts_service.py`
- `gmail_client.py`, `email_service.py`, `plain_email_service.py`, `enhanced_email_service.py`
- `drive_download_client.py`, `drive_share_client.py`
- `tree_export_service.py`
- `gpt_action_openapi.yaml`, `GPTS_INSTRUCTIONS.md`
- `setup_windows.ps1`, `verify_install.ps1`, `prepare_migration.ps1`

MVP별 상세 근거는 `MVP-04_진행보고.md`부터 `Project-1-7_Drive-Tree-Export_진행보고.md`까지의 개별 보고서에 보존했다.

## 11. Git과 완료 판정

- 초기 기반 커밋: `3675ed8`
- MVP-04 증분 동기화 커밋: `1f4df9f`
- Project 1-2 완료 커밋: `b70ba26`
- Project 1-3~1-7 완료 커밋: `e95d5c3`
- 최종 보고서 갱신을 포함한 `master`를 GitHub `origin/master`에 push한다.

## 12. 최종 결론

Python Drive Organizer는 계획한 Drive 인덱싱, 분석, 검색, 공개 API, GPT Actions, 이메일, 주소록, Windows 자동 운영, 전체 Tree 문서 반환까지 구현과 실제 종단 검증을 완료했다.

프로젝트 코드와 기능은 완료 상태다. 향후 작업은 새 기능 개발이 아니라 OAuth token 유지, 예약 작업 확인, export 정리와 같은 운영 관리 범위다. Google Drive 파일 자체를 임의로 생성·수정·이동·복사·삭제하지 않으며, 승인된 LINK 이메일의 `anyone/reader` permission 생성만 명시적으로 제한된 예외로 유지한다.
