# Python Drive Organizer — Project 1-5 Migration & Setup 진행보고

- 완료일: 2026-08-17 (Asia/Seoul)
- 단계: Project 1-5 — Windows Migration & Setup
- 상태: **COMPLETED**

## 1. 목표와 결과

현재 Windows PC 포맷 후 새 PC에서 다음 흐름으로 복구할 수 있도록 설치·검증 도구를 추가했다.

```text
프로젝트 복사 또는 Git clone
→ private 파일 수동 배치
→ setup_windows.ps1
→ 필요한 Google OAuth만 사용자 승인
→ verify_install.ps1
```

기존 Drive indexing, Daily Refresh, Parser, Grouping, Search, FastAPI, Cloudflare Tunnel, GPT Actions, Gmail send, Enhanced Email 및 Drive LINK sharing 로직은 변경하지 않았다.

## 2. 생성·수정 파일

### 생성

- `setup_windows.ps1`
- `verify_install.ps1`
- `prepare_migration.ps1`
- `uninstall_tasks.ps1`
- `MANUAL_ONLINE_SETUP.md`
- `MIGRATION_GUIDE.md`
- `test_migration_setup.py`
- `Project-1-5_Migration-Setup_진행보고.md`

### 수정

- `requirements.txt`: 코드가 직접 import하는 `google-auth`, `pydantic`을 명시적 dependency로 추가
- `README.md`: Windows 이전 빠른 시작과 문서 링크 추가

## 3. setup 자동화 범위

`setup_windows.ps1`은 `$PSScriptRoot`에서 프로젝트 root를 계산하며 현재 사용자명, 드라이브 문자 또는 기존 PC 경로를 하드코딩하지 않는다.

자동 또는 검사 범위:

- Windows 및 PowerShell 5.1 이상 확인
- Python executable 동적 탐색 및 3.10 이상 4.0 미만 검증
- 선택적 winget Python 3.14 설치
- Git 설치·버전 확인
- 필수 프로젝트 파일 확인
- `data/`, `logs/` 생성
- `.gitignore`의 venv, secret, DB/log 보호 항목 확인
- secret 파일 존재 여부와 역할만 표시
- 새 `.venv` 생성 또는 현재 PC에서 유효한 기존 venv만 재사용
- `.venv` Python으로 `pip install -r requirements.txt`
- `pip check`와 runtime/email module import
- 두 Task Scheduler 작업 생성 또는 정확한 기존 정의 재사용
- 선택적 cloudflared binary 설치
- cloudflared binary와 Windows service 상태 확인
- FastAPI task 시작 및 localhost `/health`
- unit tests
- `PASS`, `WARNING`, `FAIL`, `MANUAL ACTION REQUIRED` 요약
- secret redaction이 적용된 `logs/setup.log`

Python이 없을 때 기본 동작은 공식 설치 또는 `-InstallPython` 선택을 안내하는 것이다. 설치를 강제하지 않는다. `.venv`가 복사되었거나 현재 경로와 맞지 않으면 source/token/DB를 건드리지 않고 해당 venv만 별도 이름으로 바꾸거나 제거한 뒤 재실행하도록 중단한다.

## 4. setup 옵션과 idempotency

```powershell
.\setup_windows.ps1
.\setup_windows.ps1 -InstallPython
.\setup_windows.ps1 -InstallCloudflared
.\setup_windows.ps1 -ReplaceExistingTasks
.\setup_windows.ps1 -SkipTaskRegistration -SkipUnitTests
```

동일한 정상 Task가 이미 있으면 등록 API를 호출하지 않는다. 같은 이름이지만 경로·argument·trigger가 다른 작업은 기본적으로 덮어쓰지 않고 `MANUAL ACTION REQUIRED`를 출력한다. 사용자가 검토 후 `-ReplaceExistingTasks`를 지정한 경우에만 교체한다.

현재 운영 PC에서 기본 task 처리 경로를 재실행한 결과:

- `Python Drive Organizer API`: `Task already matches`
- `Python Drive Organizer Daily Refresh`: `Task already matches`
- 등록·덮어쓰기 없음
- setup 결과: PASS 37 / WARNING 1(테스트 생략 옵션) / FAIL 0 / MANUAL 0

## 5. Python과 requirements

설치 가능 범위는 현재 dependency metadata의 공통 하한을 기준으로 Python 3.10 이상, 4.0 미만이다. 현재 운영 및 clean simulation 검증 버전은 Python 3.14.7이다.

`requirements.txt` 최종 직접 dependency:

- `google-api-python-client`
- `google-auth`
- `google-auth-httplib2`
- `google-auth-oauthlib`
- `fastapi`
- `pydantic`
- `uvicorn`
- `httpx`
- `python-dotenv`

현재 환경의 `pip check`와 clean-install 환경의 `pip check`가 모두 통과했다. clean install은 requirements만으로 모든 runtime/email import와 전체 unit tests를 통과했다.

## 6. Windows Task Scheduler 계약

### Python Drive Organizer API

| 항목 | 설정 |
|---|---|
| Program | 새 프로젝트의 `.venv\Scripts\python.exe` 절대경로 |
| Arguments | `-m uvicorn api_server:app --host 127.0.0.1 --port 8000` |
| Start in | setup script가 위치한 프로젝트 root |
| Trigger | 현재 사용자 로그인 |
| Delay | 20초 |
| StartWhenAvailable | True |
| MultipleInstances | IgnoreNew |
| RunLevel | Limited |

### Python Drive Organizer Daily Refresh

| 항목 | 설정 |
|---|---|
| Program | 새 프로젝트의 `.venv\Scripts\python.exe` 절대경로 |
| Arguments | `daily_refresh.py` |
| Start in | setup script가 위치한 프로젝트 root |
| Trigger | 매일 Windows local time 08:00 |
| StartWhenAvailable | True |
| MultipleInstances | IgnoreNew |
| RunLevel | Limited |

Daily Refresh 진입점은 실제 코드의 `daily_refresh.py`를 확인해 사용했다. setup이나 verify는 Daily Refresh를 자동 실행하지 않으므로 OAuth 브라우저나 실제 Drive scan을 임의로 시작하지 않는다.

## 7. cloudflared 처리

setup은 cloudflared command와 version을 확인하고 Windows service가 `Running / Automatic`인지 검사한다. binary가 없을 때만 사용자가 `-InstallCloudflared`를 선택할 수 있다.

Tunnel token 취득, 기존 Tunnel replica 등록 및 service install은 온라인 수동 단계로 분리했다. token을 script, source, log 또는 보고서에 넣지 않는다.

현재 검증 결과:

- cloudflared binary: 발견
- version: 2026.7.3
- Windows service: Running / Automatic
- public health: HTTP 정상 응답

공식 근거:

- [Cloudflare Tunnel token](https://developers.cloudflare.com/tunnel/advanced/tunnel-tokens/)
- [Windows cloudflared service](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/as-a-service/windows/)
- [token-file run parameter](https://developers.cloudflare.com/tunnel/advanced/run-parameters/)

## 8. 온라인 수동 범위

`MANUAL_ONLINE_SETUP.md`에 다음을 분리했다.

- Google Cloud 프로젝트·API·OAuth consent 상태 확인
- `credentials.json` 수동 배치
- Drive metadata, Drive download, Gmail send, Drive share OAuth 개별 승인
- 기존 token 안전 복사와 새 승인 전략
- `.env` 안전 복사 또는 화면에 출력하지 않는 PDO_API_KEY 생성
- key 변경 시 GPT Builder Bearer secret 갱신
- 기존 Cloudflare Tunnel에 새 PC connector replica 등록
- GPT Builder schema/Instructions 확인

setup만 실행해 여러 OAuth 브라우저를 자동으로 띄우지 않는다. 실제 Google/Cloudflare/GPT 계정 설정도 변경하지 않는다.

## 9. secret 이전 목록과 정책

| 파일 | 역할 | 정책 |
|---|---|---|
| `.env` | FastAPI PDO_API_KEY | 안전 복사 또는 새 key 생성 |
| `credentials.json` | Google OAuth client | 사용자가 항상 직접 안전 배치 |
| `token.json` | Drive metadata readonly | 안전 복사 또는 재승인 |
| `drive_download_token.json` | Drive readonly/download | 안전 복사 또는 재승인 |
| `gmail_send_token.json` | Gmail send | 안전 복사 또는 재승인 |
| `drive_share_token.json` | Drive share 전용 | 안전 복사 또는 재승인 |

기본 권장은 `credentials.json`을 안전 배치하고 새 PC에서 필요한 OAuth token을 개별 재승인하는 것이다. 빠른 복구가 우선일 때만 기존 token을 암호화된 저장장치로 복사한다.

어떤 script도 위 파일을 자동 생성·임의 복사하거나 값을 console/log에 출력하지 않는다. `.env` 새 key 생성은 문서의 사용자 명시 명령으로만 제공한다.

## 10. SQLite 이전 정책

| DB | 이전 필요성 | 미이전 영향 |
|---|---|---|
| `data/drive_index.db` | 권장, 재생성 가능 | Daily Refresh 완료 전 검색/API index 없음 |
| `data/email_send_state.db` | 권장 | 과거 단일 메일 idempotency·중복 방지 기록 소실 |
| `data/enhanced_email_state.db` | 강력 권장 | Preview/Send/permission event/idempotency 기록 소실 |

Drive index는 metadata scan으로 재생성 가능하다. 이메일 상태 DB는 Gmail/Drive에서 안전하게 완전 재구성할 수 없으므로 운영 연속성을 위해 이전해야 한다. 상태 DB 삭제 후 과거 idempotency key를 다시 사용하면 로컬 중복 방지 근거가 없어질 수 있다.

## 11. Migration backup checker

`prepare_migration.ps1`은 다음을 값 노출 없이 분류한다.

- 존재 여부
- Git tracked
- Git ignored
- REQUIRED
- RECOMMENDED
- REGENERATE

현재 결과:

- 필수 파일 누락: 0
- 모든 private 파일과 세 DB 존재
- secret과 DB 모두 Git ignored 확인
- `.venv`와 logs는 REGENERATE 분류
- Git 작업 트리에 아직 commit되지 않은 변경·미추적 항목이 있어 commit 또는 private backup 필요 경고

평문 private ZIP 생성은 제공하지 않았다. secret을 실수로 공유할 위험을 줄이기 위해 수동 암호화 백업 체크리스트를 우선했다.

## 12. verify_install 결과

`verify_install.ps1`은 이메일 발송과 Drive write 없이 다음을 확인한다.

- Python executable 및 project-local `.venv`
- requirements, `pip check`, imports
- secret 존재 여부만 검사
- 세 SQLite DB 및 drive index read-only `PRAGMA quick_check`
- Daily Refresh command
- 두 Task Scheduler action/trigger/settings
- cloudflared binary/service
- localhost port 8000 및 `/health`
- public HTTPS `/health`
- secret을 표시하지 않는 Bearer `/status`
- unit tests

현재 실제 운영 환경 결과:

```text
PASS=24
WARNING=0
FAIL=0
MANUAL_ACTION_REQUIRED=0
Unit tests=116 passed
```

검증 중 실제 email send, Drive permission create, Drive file 변경은 없었다.

## 13. clean-install simulation

현재 운영 폴더 아래 별도 임시 경로를 만들고 root source/document 파일만 복사했다.

- secret 복사: 0
- 기존 `.venv` 복사: 0
- 기존 DB 복사: 0
- 운영 Task Scheduler 등록·변경: 0
- 새 `.venv` 생성: 성공
- `pip install -r requirements.txt`: 성공
- `pip check`: 성공
- runtime/email imports: 성공
- 전체 unit tests: 116개 통과
- 경로 독립성: 다른 root에서 성공
- 임시 simulation 폴더: 검증 후 제거 완료

clean simulation에서는 운영 작업을 보호하기 위해 `-SkipTaskRegistration`을 사용했다. Task 생성 정의는 PowerShell 5.1/7 parser, 정적 contract test 및 현재 운영 작업의 idempotent match로 검증했다.

## 14. PowerShell 및 보안 검증

- PowerShell 7 parser: 4개 script 통과
- Windows PowerShell 5.1 parser: 4개 script 통과
- 현재 경로 `C:\Users\HLB\...` 하드코딩 없음
- 실제 API key/OAuth token/Cloudflare token/client secret 없음
- setup log redaction 적용
- `verify_install.ps1`에 send 또는 Drive write 호출 없음
- `prepare_migration.ps1`에 private archive 생성 없음
- `uninstall_tasks.ps1`은 `-ConfirmRemoval` 없이는 제거하지 않음
- uninstall은 두 예약 작업 외 source, DB, token, `.env`, cloudflared service를 변경하지 않음

## 15. 기존 기능 영향

기존 Python 실행 로직과 API 계약은 변경하지 않았다. requirements에 직접 dependency 두 개를 명시하고 Windows 설치용 script/document/test만 추가했다.

현재 운영 확인:

- FastAPI task: Running
- Daily Refresh task: 정상 정의 / Ready
- localhost health: 정상
- public health: 정상
- Bearer `/status`: 정상
- cloudflared: Running / Automatic
- 기존 unit tests 포함 전체 116개 통과

## 16. 새 PC 실행 순서

```powershell
# 1. 프로젝트 복사/clone 및 private 파일 배치
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 2. 설치
.\setup_windows.ps1

# 3. MANUAL_ONLINE_SETUP.md에서 필요한 OAuth/Cloudflare 단계만 수행

# 4. 비파괴 검증
.\verify_install.ps1
```

포맷 전에는 반드시 다음을 먼저 실행하고 Git 경고와 private backup 목록을 확인한다.

```powershell
.\prepare_migration.ps1
```
