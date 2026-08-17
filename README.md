# Python Drive Organizer

MVP-05는 Google Drive API v3의 읽기 전용 메타데이터를 SQLite와 증분 동기화하고, 저장된 파일명을 결정론적 규칙으로 분석합니다.

## 준비

1. Google Cloud 프로젝트에서 Google Drive API를 사용 설정합니다.
2. 데스크톱 앱 유형의 OAuth 클라이언트 파일을 `credentials.json`이라는 이름으로 프로젝트 루트에 둡니다.
3. OAuth 앱이 외부 사용자 대상의 테스트 상태라면 Google Auth Platform의 **Audience > Test users**에 로그인할 Google 계정을 추가합니다.
4. 가상환경을 활성화하고 패키지를 설치합니다.

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 실행

```powershell
python main.py
```

최초 실행 시 브라우저에서 Google 계정 로그인과 승인을 진행합니다. 인증 후 생성되는 `token.json`을 다음 실행부터 재사용합니다.

스캔 결과는 `data/drive_index.db`의 `files`, `folders`, `scan_state` 테이블에 저장됩니다. 기존 DB와 현재 Drive 상태를 ID 기준으로 비교하여 INSERT, UPDATE, SKIP, DELETE를 수행하며 실행 후 각 판정 통계를 출력합니다.

전체 Drive 페이지를 정상적으로 읽은 경우에만 이번 스캔에서 발견되지 않은 DB 행을 삭제합니다. 조회 실패 또는 불완전 검색이면 파일·폴더 변경을 롤백하고 `scan_state`를 `FAILED`로 기록합니다. `credentials.json`, `token.json`, `data/`는 Git에 포함되지 않습니다.

Drive API를 호출하지 않고 Parser migration과 기존 파일 backfill만 실행하려면 다음 명령을 사용합니다.

```powershell
python main.py --parse-only
```

Parser 결과는 `normalized_name`, `base_name`, Revision/Copy 정보, `auto_action`, `parser_version` 컬럼에 저장됩니다. `auto_action=DELETE`는 확정된 단일 숫자 괄호 suffix 규칙의 DB 기록일 뿐 실제 Drive 파일을 삭제하지 않습니다.

Drive API나 Parser backfill을 호출하지 않고 현재 SQLite 데이터만 그룹화하려면 다음 명령을 사용합니다.

```powershell
python main.py --group-only
```

그룹 키는 `parent_id + group_base_name + extension`이며, 결과는 `file_groups`와 `file_group_members`에 저장됩니다. 그룹은 파생 데이터로서 트랜잭션 안에서 전체 재구성되며 Drive 파일에는 아무 작업도 하지 않습니다.

## SQLite 읽기 전용 검색과 Tree

MVP-07 검색 명령은 SQLite DB를 read-only 모드로 열며 OAuth나 Drive API를 호출하지 않습니다.

```powershell
python main.py --search-name "검색어" --limit 100
python main.py --search-folder "검색어"
python main.py --list-folder <folder_id> [--recursive]
python main.py --search-revisions [--min-revision 2]
python main.py --search-copies
python main.py --search-auto-delete
python main.py --search-groups [--min-members 2]
python main.py --recent 20
python main.py --changed-in-scan <scan_id>
```

전체 폴더 Tree와 특정 폴더 하위 Tree도 SQLite 데이터만으로 출력할 수 있습니다.

```powershell
python main.py --tree
python main.py --tree --root-folder <folder_id> --max-depth 3
python main.py --tree --include-files --output drive_tree.txt
```

검색 결과의 `path`는 `folders.parent_id`를 따라 실행 시 계산합니다. 누락 parent와 cycle은 출력에 명시하며 무한 순회를 방지합니다.

## Local Read-Only API

프로젝트 1-2 MVP-01의 FastAPI 서버는 기존 SQLite 검색 서비스를 localhost JSON API로 제공합니다.

프로젝트 1-2 MVP-02부터 `/health`를 제외한 모든 데이터 endpoint는 `PDO_API_KEY` Bearer 인증이 필요합니다. API key는 32자 이상이어야 하며, 설정되지 않으면 서버 startup이 실패합니다.

안전한 key 생성 예:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

출력된 값을 현재 PowerShell 세션에 설정하거나 프로젝트 루트의 `.env`에 저장합니다. 실제 값은 Git이나 문서에 기록하지 않습니다.

```powershell
$env:PDO_API_KEY = "<generated-value>"
```

또는 `.env`:

```dotenv
PDO_API_KEY=<generated-value>
```

```powershell
python -m uvicorn api_server:app --host 127.0.0.1 --port 8000
```

로컬 문서:

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/openapi.json`

주요 GET endpoint:

- `/health`, `/status`
- `/files/search`, `/folders/search`
- `/folders/{folder_id}/children`, `/folders/tree`
- `/revisions`, `/copies`, `/auto-delete`, `/groups`, `/recent`
- `/exports/{export_id}`: Bearer 인증된 Tree export 다운로드

Project 1-7 Drive Tree endpoint:

- `POST /folders/tree/page`: 최대 1000개씩 opaque cursor pagination
- `POST /exports/drive-tree`: SQLite 전체 Tree를 TXT/DOCX/XLSX로 생성
- `GET /exports/{export_id}`: 추측하기 어려운 export ID로 인증 다운로드
- `POST /exports/{export_id}/openai-file`: GPT Actions용 `openaiFileResponse` 반환

Tree pagination은 `next_cursor=null`이 될 때까지 직전 cursor를 그대로
전달합니다. Export는 GPT가 수천 개 노드를 조립하지 않고 Python 서버가
`data/drive_index.db`의 한 SQLite snapshot에서 전체 문서를 생성합니다.
생성 파일은 `exports/`에 저장되며 Git에서 제외됩니다. 모든 Tree endpoint는
Google Drive API를 호출하거나 Drive 항목을 변경하지 않습니다.
GPT는 raw binary GET을 호출하지 않고 `exportDriveTree`가 반환한 exact
`export_id`로 `returnDriveTreeExport`를 호출합니다. 반환 전 원본 파일 크기가
10 MB를 넘으면 `GPT_FILE_TOO_LARGE`로 중단하며 base64를 생성하지 않습니다.

보호 endpoint 요청 형식:

```text
Authorization: Bearer <PDO_API_KEY value>
```

Swagger `/docs`의 Authorize 버튼에는 API key 값만 입력합니다. API는 `data/drive_index.db`를 SQLite read-only mode로 열며 Google Drive API를 호출하거나 Drive 파일을 변경하지 않습니다. 이번 단계에서는 반드시 `127.0.0.1`로만 실행하고 외부 공개나 `0.0.0.0` bind를 사용하지 않습니다.

### Windows 자동 시작

등록된 Windows Task Scheduler 작업 `Python Drive Organizer API`가 현재 사용자 로그인 20초 후 다음 명령을 백그라운드로 실행합니다.

```text
Program: C:\Users\HLB\Documents\Python-Drive-Organizer\.venv\Scripts\python.exe
Arguments: -m uvicorn api_server:app --host 127.0.0.1 --port 8000
Start in: C:\Users\HLB\Documents\Python-Drive-Organizer
```

동일 작업이 실행 중이면 새 인스턴스를 시작하지 않습니다. 상태 확인과 수동 시작·중지는 다음 PowerShell 명령을 사용할 수 있습니다.

```powershell
Get-ScheduledTask -TaskName "Python Drive Organizer API"
Get-ScheduledTaskInfo -TaskName "Python Drive Organizer API"
Start-ScheduledTask -TaskName "Python Drive Organizer API"
Stop-ScheduledTask -TaskName "Python Drive Organizer API"
```

Cloudflare Tunnel은 기존 Windows 서비스 구성을 사용합니다. Task Scheduler 작업이나 API 명령에 API key 또는 Tunnel token을 넣지 않습니다.

### Google Drive Index Daily Refresh

별도 Windows Task Scheduler 작업 `Python Drive Organizer Daily Refresh`가 매일 Windows 로컬 시간 오전 08:00에 다음 명령을 실행합니다.

```text
Program: C:\Users\HLB\Documents\Python-Drive-Organizer\.venv\Scripts\python.exe
Arguments: daily_refresh.py
Start in: C:\Users\HLB\Documents\Python-Drive-Organizer
```

Daily Refresh는 다음 순서를 지키며 앞 단계가 실패하면 이후 단계를 실행하지 않습니다.

```text
Drive metadata sync → filename Parser → File Grouping
```

세 단계가 모두 성공한 뒤에만 해당 `scan_state`를 `COMPLETED`로 기록합니다. 실패 시 로그를 남기고 한 번 종료하며 무한 재시작하지 않습니다. Google Drive는 `drive.metadata.readonly` scope로만 조회하고 SQLite 인덱스만 갱신합니다.

수동 실행:

```powershell
.\.venv\Scripts\python.exe .\daily_refresh.py
```

로그 위치:

```text
logs/daily_refresh.log
```

예약 작업은 `StartWhenAvailable=True`이므로 오전 08:00에 PC를 사용할 수 없었다면 다음 사용 가능 시점에 누락 실행을 한 번 시작합니다. `MultipleInstances=IgnoreNew`로 이전 refresh가 실행 중일 때 중복 인스턴스를 만들지 않습니다.

상태 확인과 수동 Run:

```powershell
Get-ScheduledTask -TaskName "Python Drive Organizer Daily Refresh"
Get-ScheduledTaskInfo -TaskName "Python Drive Organizer Daily Refresh"
Start-ScheduledTask -TaskName "Python Drive Organizer Daily Refresh"
```

### GPTs Actions 준비 파일

Project 1-2 MVP-04의 GPT Builder 등록용 준비 파일:

- `gpt_action_openapi.yaml`: SQLite 조회, Tree export, 연락처와 승인형 이메일 Action schema
- `GPTS_INSTRUCTIONS.md`: SQLite Drive Index를 source of truth로 사용하는 GPT Instructions 초안
- `GPTS_ACTION_TEST_SCENARIOS.md`: 자연어 요청과 예상 Action을 연결한 수동 종단 테스트 시나리오

Action schema의 server는 `https://drive-api.sungwony.pe.kr`이며 HTTP Bearer 인증을 사용합니다. 실제 API key는 schema나 문서에 포함하지 않고 GPT Builder의 Authentication 화면에서 별도로 설정합니다.

이번 준비 단계에서는 GPT Builder 등록이나 Action 실행을 수행하지 않습니다. Action에는 Drive 생성·수정·이동·복사·휴지통 이동·삭제 operation이 없으며, 기존 SQLite metadata API만 조회합니다.

사용 OAuth scope:

```text
https://www.googleapis.com/auth/drive.metadata.readonly
```

## Windows PC 이전 및 재설치

포맷 전 준비:

```powershell
.\prepare_migration.ps1
```

새 PC 기본 흐름:

```powershell
.\setup_windows.ps1
# 필요한 Google OAuth 승인을 각각 완료
.\verify_install.ps1
```

기존 PC의 `.venv`는 복사하지 않으며 setup이 새 PC 경로에서 다시 생성합니다. `.env`, `credentials.json`, OAuth token 및 `data/*.db`는 Git에 포함되지 않으므로 별도 보안 이전 정책을 따라야 합니다.

- 전체 절차: [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md)
- Google OAuth, Cloudflare connector, GPT Builder 수동 단계: [MANUAL_ONLINE_SETUP.md](MANUAL_ONLINE_SETUP.md)
- 두 Windows 예약 작업만 제거: `uninstall_tasks.ps1 -ConfirmRemoval`

`verify_install.ps1`은 실제 이메일을 보내거나 Drive permission/file을 변경하지 않습니다.
