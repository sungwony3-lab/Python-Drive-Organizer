# Python Drive Organizer — 새 PC 이전 및 재설치 체크리스트 보고서

- 문서 목적: 기존 PC 포맷 전 백업부터 새 PC 설치, 인증, 자동화, 검증, 실사용 확인까지의 전체 이전 절차 정리
- 기준 프로젝트: Python Drive Organizer
- 기준 단계: Project 1-5 — Windows Migration & Setup
- 작성일: 2026-08-17
- 상태: 새 PC 이전용 운영 체크리스트

---

## 1. 전체 이전 흐름

```text
[기존 PC 포맷 전]
prepare_migration.ps1
→ 프로젝트 코드 백업
→ private 파일 및 DB 백업

[새 PC 첫날]
프로젝트 복사 또는 Git clone
→ private 파일 복원
→ setup_windows.ps1
→ 필요한 Google OAuth 승인
→ Cloudflare connector 설정
→ verify_install.ps1

[새 PC 첫날 후반]
GPT 조회 테스트
→ 일반 첨부 이메일 테스트
→ Drive Link 이메일 테스트
→ PC 재부팅 테스트

[다음날 오전 08:00 이후]
Daily Refresh 자동 실행 확인

→ 이전 완료
```

---

# 2. 기존 PC 포맷 전 체크리스트

## 2.1 Migration 사전 검사

프로젝트 폴더에서 PowerShell을 열고 실행한다.

```powershell
.\prepare_migration.ps1
```

확인 항목:

- [ ] REQUIRED 누락 항목이 0개인지 확인
- [ ] Git 작업 트리에 미커밋 변경이 있는지 확인
- [ ] 백업이 필요한 private 파일이 모두 존재하는지 확인
- [ ] DB 파일이 모두 존재하는지 확인
- [ ] `.venv`는 백업 대상에서 제외

## 2.2 반드시 별도 백업할 private 파일

다음 파일은 Git에 포함되지 않으므로 USB, 외장 SSD 등 안전한 저장장치에 별도로 백업한다.

```text
.env
credentials.json

token.json
drive_download_token.json
gmail_send_token.json
drive_share_token.json
```

체크:

- [ ] `.env`
- [ ] `credentials.json`
- [ ] `token.json`
- [ ] `drive_download_token.json`
- [ ] `gmail_send_token.json`
- [ ] `drive_share_token.json`

## 2.3 DB 백업

권장 백업 대상:

```text
data\drive_index.db
data\email_send_state.db
data\enhanced_email_state.db
```

체크:

- [ ] `drive_index.db`
- [ ] `email_send_state.db`
- [ ] `enhanced_email_state.db`

참고:

- `drive_index.db`는 Daily Refresh로 재생성 가능하다.
- `email_send_state.db`와 `enhanced_email_state.db`는 과거 idempotency 및 permission/send 상태 기록을 포함하므로 운영 연속성을 위해 이전을 강력히 권장한다.

## 2.4 프로젝트 소스 백업

- [ ] 최신 소스코드 저장 확인
- [ ] Git 사용 시 최신 commit 확인
- [ ] 필요 시 원격 저장소 push 확인
- [ ] `requirements.txt` 최신 여부 확인
- [ ] `MIGRATION_GUIDE.md` 보관
- [ ] `MANUAL_ONLINE_SETUP.md` 보관

주의:

```text
기존 PC의 .venv는 새 PC로 복사하지 않는다.
```

새 PC에서 `setup_windows.ps1`이 새 `.venv`를 생성한다.

---

# 3. 새 PC 첫날 — 프로젝트 복원

## 3.1 프로젝트 가져오기

방법 A:

```text
Git clone
```

방법 B:

```text
백업한 프로젝트 폴더 복사
```

권장 예시 경로:

```text
C:\Users\<새사용자명>\Documents\Python-Drive-Organizer
```

실제 경로는 달라도 된다. 설치 스크립트는 자신의 위치를 기준으로 프로젝트 root를 계산한다.

확인:

- [ ] `setup_windows.ps1`
- [ ] `verify_install.ps1`
- [ ] `prepare_migration.ps1`
- [ ] `requirements.txt`
- [ ] `api_server.py`
- [ ] `daily_refresh.py`
- [ ] 기타 프로젝트 소스 파일

## 3.2 Private 파일 복원

프로젝트 루트에 복원:

```text
.env
credentials.json
token.json
drive_download_token.json
gmail_send_token.json
drive_share_token.json
```

체크:

- [ ] `.env`
- [ ] `credentials.json`
- [ ] `token.json`
- [ ] `drive_download_token.json`
- [ ] `gmail_send_token.json`
- [ ] `drive_share_token.json`

`data` 폴더에 복원:

- [ ] `drive_index.db`
- [ ] `email_send_state.db`
- [ ] `enhanced_email_state.db`

주의:

- secret 값은 채팅, GitHub, 메일, 문서에 기록하지 않는다.
- 새 PC에서 OAuth 재승인을 선택하는 경우 일부 token 파일은 복사하지 않아도 된다.

---

# 4. 새 PC — PowerShell 준비

프로젝트 폴더로 이동한다.

```powershell
cd C:\Users\<새사용자명>\Documents\Python-Drive-Organizer
```

현재 PowerShell 세션에만 실행 정책을 완화한다.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

체크:

- [ ] PowerShell이 프로젝트 루트에 위치함
- [ ] ExecutionPolicy 임시 허용 완료

---

# 5. 자동 Setup 실행

기본 설치:

```powershell
.\setup_windows.ps1
```

자동 처리/검사 항목:

- Windows / PowerShell 확인
- Python 확인
- Git 확인
- 프로젝트 파일 확인
- `data/`, `logs/` 폴더 생성
- `.gitignore` 확인
- secret 파일 존재 여부 확인
- `.venv` 생성
- `requirements.txt` 설치
- `pip check`
- runtime/email module import
- FastAPI Task Scheduler 등록
- Daily Refresh Task Scheduler 등록
- cloudflared 상태 확인
- localhost `/health`
- unit tests

체크:

- [ ] 기본 setup 실행
- [ ] FAIL = 0 확인
- [ ] MANUAL ACTION REQUIRED 항목 확인

Python이 없는 경우:

```powershell
.\setup_windows.ps1 -InstallPython
```

cloudflared가 없는 경우:

```powershell
.\setup_windows.ps1 -InstallCloudflared
```

필요 시 기존 예약 작업 교체:

```powershell
.\setup_windows.ps1 -ReplaceExistingTasks
```

주의:

- 정상 작업이 이미 있으면 setup은 중복 등록하지 않는다.
- `.venv`는 새 PC에서 새로 생성한다.

---

# 6. Google OAuth 확인

현재 인증 파일 역할:

| 파일 | 역할 |
|---|---|
| `token.json` | Drive metadata readonly / Daily Refresh |
| `drive_download_token.json` | Drive 파일 다운로드 |
| `gmail_send_token.json` | Gmail 메일 발송 |
| `drive_share_token.json` | Drive LINK 공유 permission |

기존 token을 안전하게 복사했다면 그대로 동작할 수 있다.

인증 오류가 발생할 경우:

```text
MANUAL_ONLINE_SETUP.md
```

를 기준으로 해당 OAuth만 재승인한다.

체크:

- [ ] Drive metadata OAuth 정상
- [ ] Drive download OAuth 정상
- [ ] Gmail send OAuth 정상
- [ ] Drive share OAuth 정상

주의:

- 모든 token을 무조건 재발급할 필요는 없다.
- `credentials.json`은 사용자가 직접 안전하게 배치한다.

---

# 7. Cloudflare Tunnel 새 PC 연결

cloudflared 버전 확인:

```powershell
cloudflared --version
```

서비스 확인:

```powershell
Get-Service cloudflared
```

확인:

- [ ] cloudflared 설치됨
- [ ] Windows service 존재
- [ ] 서비스 Running
- [ ] 시작 유형 Automatic
- [ ] 기존 Cloudflare Tunnel에 새 PC connector 등록 완료

기존 공개 주소:

```text
https://drive-api.sungwony.pe.kr
```

주의:

- 새 도메인을 만들 필요 없음
- 기존 Tunnel token은 setup script에 저장하지 않음
- Connector 등록은 `MANUAL_ONLINE_SETUP.md` 기준 수동 온라인 절차

---

# 8. Windows 자동 실행 확인

## 8.1 FastAPI 자동 시작

확인:

```powershell
Get-ScheduledTask -TaskName "Python Drive Organizer API"
Get-ScheduledTaskInfo -TaskName "Python Drive Organizer API"
```

정상 기준:

- [ ] 작업 존재
- [ ] 로그인 후 자동 실행
- [ ] 20초 지연
- [ ] `StartWhenAvailable=True`
- [ ] `MultipleInstances=IgnoreNew`
- [ ] 새 PC 프로젝트의 `.venv\Scripts\python.exe` 사용

실행 내용:

```text
-m uvicorn api_server:app --host 127.0.0.1 --port 8000
```

## 8.2 Daily Refresh 자동 실행

확인:

```powershell
Get-ScheduledTask -TaskName "Python Drive Organizer Daily Refresh"
Get-ScheduledTaskInfo -TaskName "Python Drive Organizer Daily Refresh"
```

정상 기준:

- [ ] 매일 오전 08:00
- [ ] `StartWhenAvailable=True`
- [ ] `MultipleInstances=IgnoreNew`
- [ ] 실행 파일 `daily_refresh.py`

오전 8시에 PC가 꺼져 있었다면 다음 사용 가능 시점에 누락 작업을 한 번 실행한다.

---

# 9. 최종 설치 검증

실행:

```powershell
.\verify_install.ps1
```

검증 항목:

- Python
- `.venv`
- requirements
- secret 파일 존재 여부
- SQLite DB integrity
- Daily Refresh command
- Task Scheduler
- cloudflared
- port 8000
- localhost `/health`
- public `/health`
- Bearer `/status`
- unit tests
- 이메일 관련 module import

체크:

- [ ] `FAIL=0`
- [ ] `MANUAL_ACTION_REQUIRED=0`
- [ ] unit tests 모두 통과

이 검증은 실제 이메일 발송이나 Drive permission 생성 작업을 수행하지 않는다.

---

# 10. Local API 확인

브라우저:

```text
http://127.0.0.1:8000/health
```

체크:

- [ ] 정상 JSON 응답

---

# 11. Public HTTPS 확인

브라우저:

```text
https://drive-api.sungwony.pe.kr/health
```

체크:

- [ ] 정상 응답

실패 시 점검 순서:

```text
FastAPI
→ cloudflared Windows service
→ Cloudflare connector
→ Published hostname
```

---

# 12. GPT 조회 테스트

Custom GPT에서 다음 순서로 확인한다.

### 테스트 1

```text
현재 드라이브 인덱스 상태를 보여줘.
```

- [ ] 정상 응답

### 테스트 2

```text
변대라는 글자가 들어간 파일을 찾아줘.
```

- [ ] 정상 검색
- [ ] Action이 정상 호출됨

API key 관련:

기존 `.env`의 `PDO_API_KEY`를 그대로 복사했다면 GPT Builder Bearer secret을 변경할 필요가 없다.

새 key를 생성했다면:

```text
GPT Builder
→ Actions
→ Authentication
→ Bearer API Key
```

에서 동일한 key로 갱신한다.

---

# 13. 이메일 기능 실사용 확인

## 13.1 일반 첨부 테스트

작은 테스트 파일 사용.

예:

```text
이 테스트 PDF를 내 이메일로 보내줘.
```

체크:

- [ ] 정확한 파일 검색
- [ ] file_id 확정
- [ ] Preview 표시
- [ ] 사용자 승인
- [ ] 메일 1건만 도착
- [ ] 첨부파일 열림

## 13.2 Drive Link 테스트

대용량 파일 또는 여러 파일 사용.

체크:

- [ ] `delivery_mode=link`
- [ ] GPT가 Viewer 공개 경고 표시
- [ ] 승인 전 permission 변경 없음
- [ ] 승인 후 `Anyone with the link / Viewer`
- [ ] 메일 도착
- [ ] Naver/회사메일 등 비-Google 수신자에서 링크 열림
- [ ] 원본 수정 권한 없음

---

# 14. 재부팅 종단 테스트

새 PC를 재부팅한다.

재부팅 후 하지 말아야 할 것:

```text
PowerShell 수동 실행 금지
.venv 수동 활성화 금지
Uvicorn 수동 실행 금지
cloudflared 수동 실행 금지
```

약 1분 후 확인:

```text
https://drive-api.sungwony.pe.kr/health
```

체크:

- [ ] Public health 정상
- [ ] GPT Drive 조회 정상
- [ ] FastAPI 자동 시작 정상
- [ ] cloudflared 자동 시작 정상

---

# 15. 다음날 오전 08:00 자동 갱신 확인

오전 8시 이후:

```powershell
Get-ScheduledTaskInfo -TaskName "Python Drive Organizer Daily Refresh"
```

체크:

- [ ] 최근 실행 시간이 당일 오전 08:00 전후
- [ ] Daily Refresh 정상 종료
- [ ] GPT `/status`에서 최신 scan 확인
- [ ] `latest_scan_status=COMPLETED`

GPT 예시:

```text
마지막 드라이브 갱신 상태를 보여줘.
```

---

# 16. 최종 완료 체크

아래 항목이 모두 완료되면 새 PC 이전을 완료한 것으로 판단한다.

```text
[ ] 프로젝트 복사 또는 Git clone
[ ] private 파일 복원
[ ] DB 복원
[ ] setup_windows.ps1 완료
[ ] Python/.venv 정상
[ ] Google OAuth 정상
[ ] Cloudflare connector 정상
[ ] FastAPI 자동 시작
[ ] 오전 08:00 Daily Refresh
[ ] verify_install.ps1 FAIL=0
[ ] localhost health 정상
[ ] public HTTPS 정상
[ ] GPT 파일 조회 정상
[ ] 일반 첨부 이메일 정상
[ ] Drive Link 이메일 정상
[ ] PC 재부팅 후 자동 복구 정상
[ ] 다음날 오전 08:00 자동 갱신 정상
```

---

# 17. 포맷 전 최우선 주의사항

포맷 전에 특히 다음 파일을 반드시 백업한다.

```text
.env
credentials.json
token.json
drive_download_token.json
gmail_send_token.json
drive_share_token.json

data\drive_index.db
data\email_send_state.db
data\enhanced_email_state.db
```

위 파일은 Git에 포함되지 않으며 포맷 후 자동 복구되지 않는다.

기존 `.venv`와 `logs`는 복사하지 않고 새 PC에서 재생성한다.

---

# 18. 최종 운영 구조

```text
Windows 로그인
├─ Python Drive Organizer API 자동 시작
└─ cloudflared Windows Service 자동 시작
        ↓
https://drive-api.sungwony.pe.kr

매일 오전 08:00
└─ Daily Refresh
    ├─ Google Drive metadata sync
    ├─ filename Parser
    └─ File Grouping
        ↓
    SQLite 최신화

GPT
├─ Drive 조회
├─ 파일 검색
├─ Revision/Copy/Group 조회
└─ 이메일 발송
    ├─ 일반 첨부
    └─ Drive Link / Viewer 공유
```

---

## 완료 판정

**새 PC 첫날:** 설치, OAuth, Cloudflare, GPT, 이메일, 재부팅까지 확인

**다음날 오전 08:00 이후:** Daily Refresh 자동 실행 확인

위 두 단계가 완료되면 Python Drive Organizer 새 PC 이전을 최종 완료한 것으로 판단한다.
