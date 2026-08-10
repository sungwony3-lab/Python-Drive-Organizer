from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


PROJECT_ROOT = Path(__file__).resolve().parent
CREDENTIALS_FILE = PROJECT_ROOT / "credentials.json"
TOKEN_FILE = PROJECT_ROOT / "token.json"
SCOPES = ["https://www.googleapis.com/auth/drive.metadata.readonly"]
DRIVE_METADATA_FIELDS = (
    "id,name,mimeType,parents,size,createdTime,modifiedTime,"
    "md5Checksum,trashed,ownedByMe"
)


def authenticate() -> Credentials:
    """Authenticate with Google OAuth and reuse a saved token when possible."""
    credentials = None

    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if credentials and credentials.valid:
        return credentials

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    else:
        if not CREDENTIALS_FILE.exists():
            raise FileNotFoundError(
                f"{CREDENTIALS_FILE.name}을 프로젝트 루트에 저장하세요."
            )
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        credentials = flow.run_local_server(port=0, timeout_seconds=300)

    TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


def iter_drive_items(credentials: Credentials):
    """Yield all non-trashed Drive files and folders as metadata only."""
    service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    page_token = None

    while True:
        response = service.files().list(
            q="trashed = false",
            pageSize=1000,
            spaces="drive",
            fields=f"nextPageToken,incompleteSearch,files({DRIVE_METADATA_FIELDS})",
            pageToken=page_token,
        ).execute()

        if response.get("incompleteSearch"):
            raise RuntimeError(
                "Google Drive API가 불완전한 검색 결과를 반환했습니다."
            )

        yield from response.get("files", [])

        page_token = response.get("nextPageToken")
        if not page_token:
            break
