import io
from pathlib import Path

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


PROJECT_ROOT = Path(__file__).resolve().parent
CREDENTIALS_FILE = PROJECT_ROOT / "credentials.json"
TOKEN_FILE = PROJECT_ROOT / "drive_download_token.json"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
FILE_METADATA_FIELDS = (
    "id,name,mimeType,size,trashed,modifiedTime,version,webViewLink,"
    "resourceKey,driveId,capabilities(canDownload,canShare),"
    "shortcutDetails(targetId,targetMimeType,targetResourceKey)"
)


class DriveDownloadAuthenticationError(RuntimeError):
    """Raised when the dedicated Drive download OAuth flow fails."""


class DownloadSizeLimitExceeded(RuntimeError):
    """Raised before bytes beyond the configured attachment cap are stored."""


class _LimitedBytesIO(io.BytesIO):
    def __init__(self, maximum_bytes: int) -> None:
        super().__init__()
        self.maximum_bytes = maximum_bytes

    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > self.maximum_bytes:
            raise DownloadSizeLimitExceeded(
                "Downloaded attachment exceeds the configured byte limit."
            )
        return super().write(data)


def authenticate_drive_download() -> Credentials:
    """Authorize Drive content reads without touching the index token.json."""
    credentials = None

    try:
        if TOKEN_FILE.exists():
            credentials = Credentials.from_authorized_user_file(
                TOKEN_FILE, SCOPES
            )

        if credentials and credentials.valid and credentials.has_scopes(SCOPES):
            return credentials

        if (
            credentials
            and credentials.expired
            and credentials.refresh_token
            and credentials.has_scopes(SCOPES)
        ):
            credentials.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"{CREDENTIALS_FILE.name} must exist in the project root."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            credentials = flow.run_local_server(port=0, timeout_seconds=300)

        if not credentials.has_scopes(SCOPES):
            raise DriveDownloadAuthenticationError(
                "The Drive download token does not contain drive.readonly."
            )

        TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
        return credentials
    except DriveDownloadAuthenticationError:
        raise
    except (GoogleAuthError, OSError, ValueError) as error:
        raise DriveDownloadAuthenticationError(
            "Drive download OAuth failed. Reauthorize the dedicated token."
        ) from error


def build_drive_download_service(credentials: Credentials | None = None):
    if credentials is None:
        credentials = authenticate_drive_download()
    return build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )


def get_file_metadata(service, file_id: str) -> dict:
    """Fetch the exact Drive identity; never perform a filename search."""
    return (
        service.files()
        .get(
            fileId=file_id,
            fields=FILE_METADATA_FIELDS,
            supportsAllDrives=True,
        )
        .execute()
    )


def download_file(service, file_id: str, maximum_bytes: int) -> bytes:
    """Download one blob file while enforcing a hard in-memory byte cap."""
    stream = _LimitedBytesIO(maximum_bytes)
    request = service.files().get_media(
        fileId=file_id,
        supportsAllDrives=True,
    )
    downloader = MediaIoBaseDownload(
        stream,
        request,
        chunksize=DOWNLOAD_CHUNK_SIZE,
    )
    complete = False
    while not complete:
        _, complete = downloader.next_chunk(num_retries=0)
    return stream.getvalue()
