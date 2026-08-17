# Python Drive Organizer — 온라인 수동 설정

이 문서는 새 Windows PC에서 자동화할 수 없거나 자동화하면 위험한 온라인 인증 작업만 설명한다. `setup_windows.ps1`은 Google OAuth 승인, Cloudflare Tunnel token 등록, GPT Builder 변경을 자동 실행하지 않는다.

## 1. 다시 하지 않아도 되는 온라인 설정

같은 Google Cloud 프로젝트와 Cloudflare 계정을 계속 사용한다면 일반적으로 다음 서버 측 설정은 유지된다.

- Google Cloud 프로젝트
- Drive API와 Gmail API 활성화
- OAuth consent screen 및 테스트 사용자
- Cloudflare Tunnel 자체와 Public Hostname/DNS 설정
- GPT Builder의 Instructions와 OpenAPI schema

새 PC는 로컬 OAuth token, `.env`, cloudflared connector 및 Windows 예약 작업을 다시 준비해야 한다.

## 2. Google OAuth 공통 준비

1. 기존 PC에서 `credentials.json`을 안전하게 백업하거나 Google Cloud Console에서 같은 OAuth client 파일을 다시 다운로드한다.
2. 새 PC의 프로젝트 루트에 사용자가 직접 `credentials.json`을 배치한다.
3. `credentials.json`의 내용은 메신저, Git, 설치 로그에 붙여넣지 않는다.
4. 아래 네 인증은 필요한 것만 한 번씩 실행한다. 한 명령이 끝나고 token 파일이 생성된 것을 확인한 뒤 다음 명령으로 이동한다.

PowerShell은 프로젝트 루트에서 실행한다.

### Drive metadata 인덱스

- token: `token.json`
- scope: `https://www.googleapis.com/auth/drive.metadata.readonly`

```powershell
.\.venv\Scripts\python.exe -c "from drive_client import authenticate; authenticate(); print('Drive metadata OAuth completed.')"
```

### Drive 파일 읽기·다운로드

- token: `drive_download_token.json`
- scope: `https://www.googleapis.com/auth/drive.readonly`

```powershell
.\.venv\Scripts\python.exe -c "from drive_download_client import authenticate_drive_download; authenticate_drive_download(); print('Drive download OAuth completed.')"
```

### Gmail 발송

- token: `gmail_send_token.json`
- scope: `https://www.googleapis.com/auth/gmail.send`

```powershell
.\.venv\Scripts\python.exe -c "from gmail_client import authenticate_gmail_send; authenticate_gmail_send(); print('Gmail send OAuth completed.')"
```

이 명령은 OAuth 승인만 수행하며 실제 메일을 보내지 않는다.

### Drive 링크 공유

- token: `drive_share_token.json`
- scope: `https://www.googleapis.com/auth/drive`

```powershell
.\.venv\Scripts\python.exe -c "from drive_share_client import authenticate_drive_share; authenticate_drive_share(); print('Drive share OAuth completed.')"
```

이 명령은 OAuth 승인만 수행하며 permission을 생성하지 않는다. 이 scope는 범위가 넓으므로 전용 token으로 분리되어 있다. 실제 코드는 승인된 LINK Send에서만 비검색형 `anyone/reader` 생성 한 종류를 허용한다.

## 3. 기존 token 복사와 새 승인 중 선택

### A. 기존 token 안전 복사

장점은 브라우저 승인을 반복하지 않아도 된다는 것이다. 다음 파일을 암호화된 로컬 저장장치나 승인된 보안 저장소로 옮긴다.

- `token.json`
- `drive_download_token.json`
- `gmail_send_token.json`
- `drive_share_token.json`

복사한 token은 같은 OAuth client 및 Google 계정에서 계속 유효할 수 있다. 따라서 유출되면 위험하며 이메일·Git·일반 클라우드 공유 폴더에 두면 안 된다.

### B. 새 PC에서 OAuth 재승인 — 기본 권장

보안 경계를 명확히 하려면 token 파일은 복사하지 않고 위 명령을 각각 실행해 새 PC에서 재생성한다. 기존 PC를 포맷하기 전 OAuth client와 테스트 사용자 상태를 확인한다. 기존 refresh token을 별도로 폐기하면 다른 장치의 같은 승인이 영향을 받을 수 있으므로 Google 계정의 연결 앱 화면을 확인한 뒤 수행한다.

권장안은 `credentials.json`을 안전하게 직접 배치하고 새 PC에서 필요한 token만 재승인하는 방식이다. 복구 시간을 최소화해야 할 때만 기존 token을 안전 복사한다.

## 4. `.env`와 PDO_API_KEY

기존 `.env`를 안전하게 복사하면 GPT Builder Bearer 설정을 바꾸지 않아도 된다. `.env`를 복사하지 않으면 새 key를 생성한다.

다음 PowerShell 예시는 48바이트 난수를 Base64URL 문자열로 만들어 `.env`에 저장하며 key를 화면에 출력하지 않는다.

```powershell
$bytes = New-Object byte[] 48
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$key = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
Set-Content -LiteralPath '.env' -Value ('PDO_API_KEY=' + $key) -NoNewline -Encoding UTF8
$key = $null
$bytes = $null
$rng.Dispose()
```

새 key를 만들었다면 GPT Builder Action authentication의 Bearer value도 사용자가 직접 같은 값으로 갱신해야 한다. key를 schema, Instructions, 보고서 또는 채팅에 기록하지 않는다.

## 5. Cloudflare Tunnel connector

기존 Tunnel과 `https://drive-api.sungwony.pe.kr` Public Hostname은 Cloudflare 계정에 남아 있으므로 새로 만들 필요가 없다. 새 PC에는 기존 Tunnel의 새 connector replica를 등록한다.

1. `setup_windows.ps1 -InstallCloudflared` 또는 Cloudflare 공식 설치 방법으로 cloudflared binary를 설치한다.
2. Cloudflare Dashboard의 **Networking → Tunnels**에서 기존 Tunnel을 선택한다.
3. **Add a replica**에서 Windows 명령을 확인한다.
4. 관리자 권한 터미널에서 Cloudflare가 제공한 service install 명령을 직접 실행한다.
5. token을 프로젝트 파일, PowerShell history 공유본, 보고서 또는 Git에 저장하지 않는다.
6. `Get-Service cloudflared`에서 `Running`을 확인한다.
7. `verify_install.ps1`로 public health를 확인한다.

현재 운영 방식처럼 token을 별도 파일에 보관할 경우 서비스 명령은 `tunnel run --token-file <보호된 절대경로>` 형태를 사용할 수 있다. token 파일은 프로젝트 밖의 관리자 보호 위치에 두고 ACL을 제한한다. Cloudflare 공식 문서상 Tunnel token을 가진 사용자는 connector를 실행할 수 있으므로 비밀번호와 같은 수준으로 보호해야 한다.

공식 참고:

- [Cloudflare Tunnel token](https://developers.cloudflare.com/tunnel/advanced/tunnel-tokens/)
- [Windows service 실행](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/as-a-service/windows/)
- [cloudflared run parameters와 token-file](https://developers.cloudflare.com/tunnel/advanced/run-parameters/)

## 6. GPT Builder

프로젝트의 `GPTS_INSTRUCTIONS.md`와 `gpt_action_openapi.yaml`을 사용한다.

- 기존 도메인과 API key를 유지했다면 재설정할 필요가 없다.
- PDO_API_KEY를 새로 만들었다면 Action authentication의 Bearer value만 갱신한다.
- schema에 실제 API key를 넣지 않는다.
- Public health와 Bearer `/status` 검증이 끝난 뒤 GPT Action을 테스트한다.
- 이메일 Send와 Drive LINK sharing은 실제 외부 변경이므로 테스트 전 Preview와 명시적 승인을 유지한다.

## 7. setup이 수행하지 않는 작업

- Google Cloud Console 설정 변경
- Drive/Gmail API 활성화 변경
- OAuth consent screen 또는 테스트 사용자 변경
- 브라우저 OAuth 자동 연속 실행
- Cloudflare Dashboard, Tunnel, DNS 또는 도메인 변경
- Tunnel token 자동 취득·기록
- GPT Builder 수정
- 실제 이메일 발송
- Drive permission 또는 파일 변경
