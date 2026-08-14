# Python Drive Organizer — Project 1-2 MVP-03 진행보고

## 1. 완료 상태

- 단계: 프로젝트 1-2
- 챕터: MVP-03 Cloudflare Tunnel HTTPS / FastAPI Windows 자동 시작
- 상태: **COMPLETED**
- 완료일: 2026-08-13
- 공개 주소: `https://drive-api.sungwony.pe.kr`
- 로컬 서비스: `http://127.0.0.1:8000`

기존 Cloudflare Named Tunnel과 Windows 서비스를 변경하지 않고, FastAPI/Uvicorn을 현재 사용자 로그인 후 자동 실행하도록 Windows Task Scheduler에 등록했다. 수동 PowerShell 실행이나 가상환경 활성화 없이 재부팅 후 API와 공개 HTTPS가 정상 동작하는 것까지 검증했다.

이번 MVP에서는 GPTs Actions를 구현하지 않았으며 Google Drive API 코드, OAuth scope, SQLite 데이터와 Cloudflare Tunnel 설정을 변경하지 않았다.

## 2. 생성·수정 사항

### 프로젝트 파일

- 생성: `Project-1-2_MVP-03_진행보고.md`
- 수정: `README.md`
  - Windows 자동 시작 작업의 실행 정보 추가
  - 작업 상태 확인 및 수동 시작·중지 명령 추가
  - secret을 예약 작업 명령이나 문서에 넣지 않는 운영 원칙 추가

### Windows 시스템 구성

- Task Scheduler 작업 생성: `Python Drive Organizer API`
- 기존 `cloudflared` Windows 서비스는 조회와 검증만 수행
- Cloudflare Tunnel route, token 및 서비스 설정은 변경하지 않음

추가 Python 패키지는 설치하지 않았다.

## 3. FastAPI 예약 작업 설정

| 항목 | 설정 |
|---|---|
| 작업 이름 | `Python Drive Organizer API` |
| Trigger | 현재 사용자 Windows 로그인 |
| 시작 지연 | 20초 (`PT20S`) |
| Program | `C:\Users\HLB\Documents\Python-Drive-Organizer\.venv\Scripts\python.exe` |
| Arguments | `-m uvicorn api_server:app --host 127.0.0.1 --port 8000` |
| Start in | `C:\Users\HLB\Documents\Python-Drive-Organizer` |
| 실행 방식 | 사용자 Interactive context, background |
| 중복 실행 정책 | `IgnoreNew` |
| 실행 시간 제한 | 없음 (`PT0S`) |
| 누락된 시작 처리 | `StartWhenAvailable=True` |

PowerShell activation script를 사용하지 않고 가상환경의 Python 실행 파일을 직접 호출한다. API key는 프로젝트 루트의 기존 `.env`에서 `api_server.py`가 읽으며, 예약 작업의 action과 XML에는 API key 또는 Cloudflare token이 없다.

## 4. Cloudflare Tunnel 상태

| 항목 | 확인 결과 |
|---|---|
| Domain | `sungwony.pe.kr` Active |
| Named Tunnel | `python-drive-organizer` |
| Public hostname | `https://drive-api.sungwony.pe.kr` |
| Origin route | `http://127.0.0.1:8000` |
| Windows service name | `cloudflared` |
| 서비스 상태 | `Running` |
| 시작 유형 | `Automatic` |

FastAPI는 계속 `127.0.0.1:8000`에만 bind한다. `0.0.0.0` bind, 공유기 port forwarding, Windows port 8000 직접 공개는 사용하지 않는다. 외부 ingress는 기존 Cloudflare Tunnel만 담당한다.

## 5. Secret 및 Git 제외 확인

- `.env`: Git 제외 대상
- `credentials.json`: Git 제외 대상
- `token.json`: Git 제외 대상
- `data/`: Git 제외 대상
- `logs/`: Git 제외 대상
- 기존 `PDO_API_KEY`: 재사용, 새 key 생성 강제 없음
- API key 실제 값: 출력·로그·보고서·예약 작업·Git에 기록하지 않음
- Cloudflare Tunnel token: 출력·로그·보고서·예약 작업·Git에 기록하지 않음
- OAuth token: 출력·로그·보고서·Git에 기록하지 않음

API 응답 본문에도 사용 중인 API key가 포함되지 않는 것을 확인했다.

## 6. 수동 예약 작업 실행 검증

기존 수동 Uvicorn을 완전히 종료해 `127.0.0.1:8000` 리스너가 0개인 상태를 확인한 후 예약 작업을 수동 실행했다.

| 항목 | 결과 |
|---|---|
| 작업 실행 시각 | 2026-08-13 14:22:43 KST |
| 작업 상태 | `Running` |
| Task result | `267009` — 실행 중 |
| 포트 8000 리스너 | 1개 |
| bind 주소 | `127.0.0.1` |
| Uvicorn PID | 13908 |

실행 중인 작업에 `Start-ScheduledTask`를 한 번 더 호출한 결과:

- 리스너 수: 1개 → 1개
- PID: 13908 → 13908
- 새 Uvicorn 인스턴스 생성 없음
- `MultipleInstancesPolicy`: `IgnoreNew`

따라서 예약 작업 자체를 반복 실행해도 중복 서버 프로세스가 생성되지 않는다.

## 7. 수동 실행 후 HTTP 검증

| 요청 | 인증 | HTTP 결과 |
|---|---|---:|
| `http://127.0.0.1:8000/health` | 없음 | 200 |
| `http://127.0.0.1:8000/status` | 없음 | 401 |
| `http://127.0.0.1:8000/status` | 정상 Bearer | 200 |
| `https://drive-api.sungwony.pe.kr/health` | 없음 | 200 |
| `https://drive-api.sungwony.pe.kr/status` | 없음 | 401 |
| `https://drive-api.sungwony.pe.kr/status` | 정상 Bearer | 200 |

공개 `/health`와 보호된 데이터 endpoint의 인증 정책이 그대로 유지됐다.

## 8. 재부팅 종단 검증

Windows 재부팅 후 사용자가 PowerShell, `.venv` 활성화, Uvicorn 명령 또는 cloudflared 명령을 수동 실행하지 않은 상태에서 검증했다.

| 항목 | 결과 |
|---|---|
| Windows 부팅 시각 | 2026-08-13 14:25:22 KST |
| 예약 작업 실행 시각 | 2026-08-13 14:25:53 KST |
| 예약 작업 상태 | `Running` |
| Trigger delay | `PT20S` |
| Task result | `267009` — 실행 중 |
| 리스너 | 1개, `127.0.0.1:8000` |
| Uvicorn PID | 17236 |
| cloudflared 상태 | `Running` |
| cloudflared 시작 유형 | `Automatic` |

재부팅 후 최종 HTTP 결과:

| 요청 | 인증 | HTTP 결과 |
|---|---|---:|
| localhost `/health` | 없음 | 200 |
| localhost `/status` | 없음 | 401 |
| localhost `/status` | 정상 Bearer | 200 |
| HTTPS `/health` | 없음 | 200 |
| HTTPS `/status` | 없음 | 401 |
| HTTPS `/status` | 정상 Bearer | 200 |

재부팅 후 자동 시작 및 Cloudflare 공개 HTTPS 종단 테스트를 모두 통과했다.

## 9. 실제 `/status` 확인 결과

정상 Bearer 인증으로 조회한 현재 인덱스 상태:

```text
files_count        = 7924
folders_count      = 1141
groups_count       = 7898
auto_delete_count  = 87
latest_scan_id     = SCAN-20260811-114701
latest_scan_status = COMPLETED
```

API는 기존 SQLite Drive Index를 source of truth로 사용한다.

## 10. SQLite read-only 검증

재부팅 전 API 검증 직전의 `data/drive_index.db` SHA-256:

```text
857dfc31d511fb3a9de16e9f84beaec60dda776ee42c0ae594c1623feac0cc21
```

다음 시점의 해시가 모두 동일했다.

- 재부팅 전 HTTP 검증 전후
- Windows 재부팅 후
- 재부팅 후 localhost 및 HTTPS 검증 전후

따라서 자동 시작, 인증 확인 및 공개 HTTPS 조회로 인한 DB 변경은 0건이다. API는 계속 SQLite `mode=ro` 연결을 사용한다.

## 11. 회귀 테스트

실행 명령:

```powershell
.\.venv\Scripts\python.exe -m unittest -v `
  test_name_parser.py `
  test_file_grouping.py `
  test_search_service.py `
  test_api_server.py
```

| 영역 | 테스트 수 | 결과 |
|---|---:|---|
| MVP-05 Parser | 12 | 통과 |
| MVP-06 Grouping | 12 | 통과 |
| MVP-07 Search/Tree | 15 | 통과 |
| Project 1-2 API/Auth | 12 | 통과 |
| 합계 | **51** | **전체 통과** |

추가 검사:

- Python `py_compile`: 통과
- `pip check`: `No broken requirements found.`
- `git diff --check`: 오류 없음
- API 응답 secret 포함 여부: 없음

## 12. 기존 기능 및 Drive 안전성

- `python main.py`: 변경 없음
- `python main.py --parse-only`: 변경 없음
- `python main.py --group-only`: 변경 없음
- Search CLI 및 Tree CLI: 변경 없음
- FastAPI endpoint 및 Bearer 인증: 변경 없음
- SQLite DB 내용과 schema: 변경 없음
- Google Drive API 코드: 변경 없음
- Google Drive 생성·수정·이동·복사·삭제·휴지통 이동: 실행 없음
- Drive write API: 호출 없음
- 유지한 OAuth scope:

```text
https://www.googleapis.com/auth/drive.metadata.readonly
```

## 13. 장애 확인 방법

FastAPI origin이 시작되지 않으면 Cloudflare에서 502가 발생할 수 있다. 무한 재시작 구조를 추가하지 않았으며 다음 순서로 확인한다.

```powershell
Get-ScheduledTask -TaskName "Python Drive Organizer API"
Get-ScheduledTaskInfo -TaskName "Python Drive Organizer API"
Get-NetTCPConnection -LocalPort 8000 -State Listen
```

추가 확인 항목:

- 가상환경 Python executable 경로
- 작업의 working directory
- 프로젝트 루트 `.env` 존재 여부
- port 8000 사용 여부
- `cloudflared` Windows 서비스 상태

## 14. 다음 단계 경계

Project 1-2 MVP-03은 다음 범위까지만 완료했다.

- 기존 Cloudflare Tunnel을 통한 HTTPS 공개 상태 확인
- Bearer 인증 정책 유지
- 로그인 후 FastAPI 자동 시작
- 중복 인스턴스 방지
- 수동 예약 작업 실행 검증
- Windows 재부팅 후 자동 시작 종단 검증

GPTs Actions 및 새로운 쓰기 기능은 아직 구현하지 않았다. 다음 MVP는 이 보고서의 완료 상태와 보안·read-only 경계를 기준으로 별도 진행해야 한다.
