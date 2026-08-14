# Python Drive Organizer — Project 1-2 MVP-02 진행보고

## 1. 완료 상태

- 단계: 프로젝트 1-2
- 챕터: MVP-02 API Authentication / Security
- 상태: **COMPLETED**
- 완료일: 2026-08-11
- API 버전: `1.2-MVP02`
- 로컬 주소: `http://127.0.0.1:8000`
- 인증 환경변수: `PDO_API_KEY`

MVP-01 Local Read-Only API의 데이터 endpoint 전체에 단일 사용자용 HTTP Bearer API key 인증을 적용했다. `/health`만 인증 없이 허용한다.

실제 API key는 소스, README, 로그, 테스트 출력 및 이 보고서에 기록하지 않았다. 검증에는 `secrets`로 매번 생성한 임시 key를 프로세스 환경에만 주입하고 검증 종료 후 제거했다.

이번 MVP에서는 Cloudflare Tunnel, 외부 HTTPS 공개 및 GPTs Actions를 구현하지 않았다.

## 2. 생성·수정 파일

### 생성

- `Project-1-2_MVP-02_진행보고.md`: 인증 구조와 보안 검증 보고서

### 수정

- `api_server.py`
  - 공통 Bearer 인증 dependency
  - `PDO_API_KEY` 환경 및 `.env` 로드
  - constant-time key 비교
  - startup fail-closed 검증
  - 보호 endpoint router
  - API 버전 `1.2-MVP02`
- `test_api_server.py`
  - 인증 없음·오류·정상 인증 테스트
  - fail-closed startup 테스트
  - OpenAPI security scheme 테스트
  - secret 비노출 및 read-only 회귀 테스트
- `requirements.txt`: `python-dotenv` 추가
- `README.md`: key 생성·설정, Bearer 요청 및 Swagger Authorize 사용법 추가

`.gitignore`는 이미 필요한 secret 제외 규칙을 가지고 있어 수정하지 않았다.

## 3. 추가 dependency

- `python-dotenv`
- 설치 버전: 1.2.2

프로젝트 루트 `.env`를 선택적으로 자동 로드하기 위해서만 사용한다. JWT, 사용자 DB, 암호화 framework 등은 추가하지 않았다.

`pip check` 결과:

```text
No broken requirements found.
```

## 4. 인증 구조

요청 형식:

```http
Authorization: Bearer <API_KEY>
```

처리 흐름:

```text
Uvicorn startup
→ 프로젝트 루트 .env 로드(존재할 때만)
→ PDO_API_KEY 존재 및 최소 길이 확인
→ 보호 endpoint 요청에서 공통 HTTPBearer dependency 실행
→ hmac.compare_digest()로 UTF-8 byte 비교
→ 성공 시 기존 SQLite read-only endpoint 실행
```

보안 원칙:

- key는 Python 소스에 하드코딩하지 않음
- key를 SQLite나 다른 파일에 저장하지 않음
- key 자체를 응답·로그·오류 메시지에 포함하지 않음
- 일반 `==` 대신 `hmac.compare_digest()` 사용
- 인증 코드는 endpoint마다 복제하지 않고 router 공통 dependency로 적용
- key가 없거나 32자 미만이면 server startup 실패
- startup 검증을 우회한 보호 요청에서도 key 미설정이면 503으로 fail closed

## 5. Secret 생성 및 설정

최소 32자 이상의 key가 필요하다. 권장 생성 명령:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

현재 PowerShell 세션 설정 예:

```powershell
$env:PDO_API_KEY = "<generated-value>"
```

프로젝트 루트 `.env` 사용 예:

```dotenv
PDO_API_KEY=<generated-value>
```

위 표기는 placeholder이며 실제 secret이 아니다. 생성된 실제 값은 Git, 문서 또는 채팅에 붙여 넣지 않아야 한다.

## 6. 인증 대상

### 인증 없이 허용

- `GET /health`
- FastAPI 문서 및 schema: `/docs`, `/openapi.json`, `/redoc`

문서와 schema는 API key 값을 포함하지 않으며, 실제 사용자 데이터 endpoint는 모두 보호된다.

### Bearer 인증 필수

- `GET /status`
- `GET /files/search`
- `GET /folders/search`
- `GET /folders/{folder_id}/children`
- `GET /folders/tree`
- `GET /revisions`
- `GET /copies`
- `GET /auto-delete`
- `GET /groups`
- `GET /recent`

## 7. 인증 실패와 정상 응답

실제 `127.0.0.1:8000` Uvicorn 검증 결과:

| 요청 | 인증 상태 | HTTP |
|---|---|---:|
| `/health` | Authorization 없음 | 200 |
| `/status` | Authorization 없음 | 401 |
| `/status` | 잘못된 key | 401 |
| `/status` | `Basic` 잘못된 scheme | 401 |
| `/status` | 정상 Bearer key | 200 |
| `/files/search?q=송금확인증` | 정상 Bearer key | 200 |
| `/auto-delete` | 정상 Bearer key | 200 |
| `/folders/tree?max_depth=2` | 정상 Bearer key | 200 |

401 응답:

- JSON `detail`만 반환
- `WWW-Authenticate: Bearer` header 포함
- 전달된 key나 설정된 key를 응답에 포함하지 않음
- 환경변수 값이나 Python traceback을 HTTP 응답에 포함하지 않음

정상 인증 결과:

- `/status`: files 7,924 / folders 1,141 / groups 7,898
- `/files/search?q=송금확인증`: total 2 / showing 2
- `/auto-delete`: total 87
- `classification_only=true`
- `drive_action_executed=false`
- `/folders/tree?max_depth=2`: folders 50

인증 추가 전의 기존 Search/Tree 데이터 결과와 동일하다.

## 8. Fail-closed 검증

현재 프로젝트에는 `.env`가 없고 영구 `PDO_API_KEY` 환경변수도 설정하지 않았다.

key가 없는 상태로 다음 명령을 실제 실행했다.

```powershell
python -m uvicorn api_server:app --host 127.0.0.1 --port 8000
```

결과:

- Uvicorn process 생성 후 application startup 단계에서 중단
- 종료 코드 1
- 오류: `PDO_API_KEY`가 최소 32자로 설정되어야 한다는 local startup message
- 보호 endpoint가 인증 없이 열리지 않음
- 오류에 secret 값이 포함되지 않음

자동 테스트에서도 `.env` 로드를 차단하고 환경변수를 비운 상태에서 application lifespan 진입이 `RuntimeError`로 실패하는 것을 확인했다.

## 9. OpenAPI 및 Swagger

실제 `/openapi.json` 검증:

```text
components.securitySchemes.BearerAuth.type = http
components.securitySchemes.BearerAuth.scheme = bearer
```

- `/health`: operation-level security 없음
- `/status` 및 모든 보호 endpoint: `BearerAuth` security 필요
- Swagger `/docs`: HTTP 200
- Swagger UI에 Bearer `Authorize` 기능 제공
- 정상 Bearer header로 보호 endpoint 호출 성공

Swagger Authorize에는 `Bearer ` 접두어가 아닌 API key 값만 입력한다. 실제 key를 자동화 출력이나 보고서에 넣지 않았다.

## 10. SQLite read-only 보존

정상 인증으로 `/status`, 검색, children, Tree, Revision, Copy, auto-delete, Groups, Recent 등을 호출하기 전후 다음 테이블 전체를 hash했다.

- `files`
- `folders`
- `scan_state`
- `file_groups`
- `file_group_members`

통합 SHA-256:

```text
before: e8d26e7665f018f7b53935a9bb9e2bb403b77cacc076b744da55d6fa3f3f91a9
after:  e8d26e7665f018f7b53935a9bb9e2bb403b77cacc076b744da55d6fa3f3f91a9
```

인증된 API 요청으로 발생한 DB 변경은 0이다.

추가 무결성 결과:

- `PRAGMA integrity_check`: `ok`
- `PRAGMA foreign_key_check`: 위반 0
- API DB 연결: 계속 SQLite `mode=ro`

## 11. 자동 테스트

API 인증 테스트:

```powershell
python -m unittest -v test_api_server.py
```

- Local API 테스트: 12개 통과

전체 회귀:

```powershell
python -m unittest -v `
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

- Python compile: 통과
- `pip check`: 통과
- `git diff --check`: 오류 없음

## 12. 기존 기능 회귀

현재 단계에서 기존 결과를 확인했다.

- `python main.py`: `SCAN-20260811-114701` COMPLETED
  - 파일 seen 7,924 / UPDATE 3 / SKIP 7,921 / DELETE 0
  - 폴더 seen 1,141 / SKIP 1,141 / DELETE 0
- `python main.py --parse-only`
  - Parser version `MVP05-PARSER-1`
  - Rows parsed 0
- `python main.py --group-only`
  - Files 7,924 / Groups 7,898 / Members 7,924
- `--search-name AUTODIM --limit 1`: total 1
- `--tree --max-depth 1`: folders 8
- 전체 Tree: folders 1,141
- 기존 Parser, Grouping, Search/Tree 자동 테스트 전체 통과

API 인증은 `api_server.py`에만 적용되므로 PowerShell CLI에는 API key가 필요하지 않다.

## 13. Git 및 secret 제외 검사

확인 결과:

- `.env`: `.gitignore` 대상, 현재 파일 없음
- `credentials.json`: `.gitignore` 대상, Git tracked 아님
- `token.json`: `.gitignore` 대상, Git tracked 아님
- `data/drive_index.db`: `data/` 규칙으로 Git 제외
- `PDO_API_KEY`: 테스트 종료 후 프로세스 환경에서 제거됨
- 소스의 고정 개발 key: 없음
- README와 보고서: placeholder와 환경변수 이름만 존재
- 테스트 key: `secrets.token_urlsafe(48)`로 runtime 생성
- 실제 key의 응답·로그·보고서 출력: 없음

## 14. Drive 및 OAuth 안전성

- API server와 SearchService에서 `authenticate()` 호출 없음
- Google Drive service 생성 없음
- `files().list()` 호출 없음
- Drive write endpoint 없음
- Drive 생성·삭제·이동·이름 변경·복사 없음
- API key는 Google OAuth와 별개의 localhost API 접근 인증
- OAuth scope 변경 없음
- 기존 OAuth scope:

```text
https://www.googleapis.com/auth/drive.metadata.readonly
```

## 15. Localhost 유지 및 다음 단계

MVP-02에서도 다음 실행 방식만 사용한다.

```powershell
python -m uvicorn api_server:app --host 127.0.0.1 --port 8000
```

검증 후 Uvicorn을 종료했으며 현재 8000 포트는 listening 상태가 아니다.

이번 MVP에서 구현하지 않은 항목:

- `0.0.0.0` bind
- Cloudflare Tunnel
- 외부 HTTPS 공개
- GPTs Actions
- 사용자 로그인/JWT
- API를 통한 DB 변경
- Google Drive write

외부 공개는 별도 승인된 MVP-03에서 진행해야 한다.
