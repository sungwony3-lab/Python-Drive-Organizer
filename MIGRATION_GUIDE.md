# Python Drive Organizer — Windows 이전 가이드

이 가이드는 기존 PC 포맷 전 준비부터 새 Windows PC 검증까지 순서대로 진행하기 위한 문서다. 명령은 별도 표시가 없으면 프로젝트 루트 PowerShell에서 실행한다.

## A. 기존 PC 포맷 전

1. 실행 중인 프로젝트가 정상인지 확인한다.
2. 다음 검사기를 실행한다.

```powershell
.\prepare_migration.ps1
```

3. Git 변경·미추적 파일이 있으면 의도한 파일을 검토해 commit/push한다.
4. Git에 포함되지 않는 private 항목은 암호화된 로컬 저장장치 또는 승인된 보안 저장소에 별도 백업한다.

### 반드시 별도 보관하거나 새로 만들어야 하는 private 파일

- `.env`
- `credentials.json`
- `token.json`
- `drive_download_token.json`
- `gmail_send_token.json`
- `drive_share_token.json`

`credentials.json`은 새 PC에 항상 사용자가 직접 배치한다. token 파일은 안전 복사 또는 새 OAuth 승인 중 하나를 선택한다.

### DB 백업 정책

| DB | 이전 정책 | 삭제·미이전 영향 |
|---|---|---|
| `data/drive_index.db` | 이전 권장, 재생성 가능 | 검색 인덱스가 사라지며 Daily Refresh 완료 전 API 검색 불가 |
| `data/email_send_state.db` | 이전 권장 | 기존 단일 메일 idempotency 및 중복 방지 기록 소실 |
| `data/enhanced_email_state.db` | **강력히 이전 권장** | Enhanced Email Preview/Send/idempotency와 permission 처리 이력 소실; 과거 key 중복 방지 불가 |

`drive_index.db`는 Google Drive metadata를 다시 스캔해 만들 수 있다. 이메일 상태 DB는 원격 Gmail/Drive 상태에서 안전하게 완전 재구성할 수 없으므로 운영 연속성을 위해 백업한다.

### 복사하지 않는 항목

- `.venv`: 새 PC 경로와 Python에 묶이므로 절대 재사용하지 않는다.
- `__pycache__`, `*.pyc`: 자동 재생성
- `logs/`: 선택적 운영 기록이며 실행 필수 아님

자동 PRIVATE ZIP은 제공하지 않는다. secret을 평문 ZIP으로 잘못 공유할 위험보다 수동 암호화 백업 체크리스트가 안전하다.

## B. 새 PC 준비

- Windows Update를 완료한다.
- PowerShell 5.1 이상을 사용한다.
- 인터넷 연결을 준비한다.
- Python은 3.10 이상 4.0 미만을 지원한다. 현재 검증 환경은 Python 3.14.7이다.
- Git clone을 사용할 경우 Git을 설치한다.

Python이 없다면 두 방법 중 하나를 사용한다.

1. Python 공식 설치 프로그램으로 설치
2. setup에 안전한 명시 옵션 사용: `.\setup_windows.ps1 -InstallPython`

setup의 자동 설치 옵션은 winget의 공식 `Python.Python.3.14` package를 사용한다.

## C. 프로젝트 복사

Git 사용 시:

```powershell
git clone https://github.com/sungwony3-lab/Python-Drive-Organizer.git
Set-Location .\Python-Drive-Organizer
```

또는 검증한 코드 폴더를 새 PC로 복사한다. 기존 `.venv`는 포함하지 않는다.

## D. secret과 DB 배치

설치 전에 프로젝트 루트에 `.env`, `credentials.json` 및 복사하기로 선택한 token을 배치한다. DB를 이전한다면 `data` 폴더에 원래 파일명으로 넣는다.

private 파일의 실제 값을 PowerShell 화면이나 설치 로그에 출력하지 않는다.

## E. setup 실행

PowerShell 실행 정책이 로컬 script 실행을 막는 경우 현재 프로세스에만 허용한다.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup_windows.ps1
```

setup은 다음을 수행한다.

- Windows/PowerShell/Python/Git 확인
- 새 PC-local `.venv` 생성
- `requirements.txt` 설치 및 `pip check`
- `data/`, `logs/` 생성
- `.gitignore`와 secret 존재 검사
- FastAPI 및 Daily Refresh Task Scheduler 등록
- cloudflared binary/service 상태 확인
- localhost health 및 unit tests

필요할 때만 사용하는 옵션:

```powershell
.\setup_windows.ps1 -InstallPython
.\setup_windows.ps1 -InstallCloudflared
.\setup_windows.ps1 -ReplaceExistingTasks
```

`-ReplaceExistingTasks`는 동일 이름의 기존 작업이 현재 프로젝트 경로와 다를 때 검토 후 사용한다. 기본 실행은 다른 설정의 기존 작업을 자동 덮어쓰지 않는다.

## F. OAuth 필요 여부

token을 복사하지 않았거나 만료·취소됐다면 [MANUAL_ONLINE_SETUP.md](MANUAL_ONLINE_SETUP.md)의 네 OAuth 명령 중 필요한 것만 실행한다. setup은 브라우저 창을 자동 연속 실행하지 않는다.

## G. Cloudflare connector

기존 Cloudflare Tunnel과 DNS는 서버 측에 남는다. 새 PC에서는 기존 Tunnel에 connector replica를 추가하고 Windows service를 등록해야 한다. token이 포함된 명령은 Cloudflare Dashboard에서 직접 받아 관리자 권한으로 실행한다.

상세 절차는 [MANUAL_ONLINE_SETUP.md](MANUAL_ONLINE_SETUP.md)를 따른다.

## H. 설치 검증

```powershell
.\verify_install.ps1
```

검증 항목:

- `.venv` Python과 import
- requirements 및 `pip check`
- secret 존재 여부만 확인
- SQLite 파일과 read-only integrity
- 두 Task Scheduler 정의
- cloudflared binary/service
- localhost port 8000과 `/health`
- 가능하면 public `/health`
- 실제 `.env`를 화면에 표시하지 않는 Bearer `/status`
- database status 응답
- unit tests 및 이메일 module import

verify는 실제 이메일을 보내거나 Drive permission/file을 변경하지 않는다.

## I. 실패 시 복구

### `.venv` 오류

복사된 `.venv` 또는 깨진 `.venv`만 이름을 바꾸거나 삭제한 뒤 setup을 다시 실행한다. source, token, DB는 삭제하지 않는다.

### Task Scheduler 권한 오류

PowerShell을 현재 Windows 사용자로 다시 열고 확인한다. 여전히 거부되면 관리자 PowerShell에서 setup을 실행하되, Task principal은 현재 사용자 Interactive/Limited로 등록되는지 `verify_install.ps1`로 확인한다.

### API health 실패

1. `.env` 존재와 PDO_API_KEY 길이를 확인한다.
2. `data/drive_index.db`가 없으면 OAuth 후 Daily Refresh를 한 번 실행한다.
3. 다음 명령으로 로컬 오류를 확인한다.

```powershell
.\.venv\Scripts\python.exe -m uvicorn api_server:app --host 127.0.0.1 --port 8000
```

확인 후 `Ctrl+C`로 종료하고 예약 작업을 시작한다.

### Daily Refresh 실패

`logs/daily_refresh.log`에서 secret이 제거된 오류를 확인한다. `token.json`이 없거나 무효하면 Drive metadata OAuth를 다시 승인한다.

### Public health 실패

로컬 health가 먼저 성공하는지 확인한 뒤 cloudflared service와 Cloudflare Dashboard connector 상태를 확인한다. DNS나 Tunnel을 즉시 새로 만들지 않는다.

### 작업 설정만 제거

프로젝트 파일·token·DB를 보존하고 두 예약 작업만 제거하려면 명시적으로 실행한다.

```powershell
.\uninstall_tasks.ps1 -ConfirmRemoval
```

## J. 최종 운영 확인

1. `verify_install.ps1`의 FAIL이 0인지 확인한다.
2. `Get-ScheduledTask -TaskName "Python Drive Organizer API"`가 Running인지 확인한다.
3. Daily Refresh의 다음 실행 시각이 08:00인지 확인한다.
4. public health가 HTTP 200인지 확인한다.
5. GPT Builder에서 조회 Action을 먼저 테스트한다.
6. 이메일 또는 LINK mode는 Preview → 위험 설명 → 명시적 승인 순서로만 테스트한다.
