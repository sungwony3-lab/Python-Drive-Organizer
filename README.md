# Python Drive Organizer

MVP-04는 Google Drive API v3에 읽기 전용 OAuth로 연결하여 파일과 폴더의 메타데이터를 SQLite와 증분 동기화합니다.

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

사용 OAuth scope:

```text
https://www.googleapis.com/auth/drive.metadata.readonly
```
