from pathlib import Path

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


PROJECT_ROOT = Path(__file__).resolve().parent
CREDENTIALS_FILE = PROJECT_ROOT / "credentials.json"
TOKEN_FILE = PROJECT_ROOT / "drive_share_token.json"
SCOPES = ["https://www.googleapis.com/auth/drive"]
PERMISSION_FIELDS = (
    "nextPageToken,permissions(id,type,role,allowFileDiscovery,permissionDetails)"
)


class DriveShareAuthenticationError(RuntimeError):
    """Raised when the dedicated Drive sharing OAuth flow fails."""


class DriveSharingError(RuntimeError):
    """Raised when an allowlisted Drive permission operation fails."""


def authenticate_drive_share() -> Credentials:
    """Authorize the isolated Drive sharing token without touching other tokens."""
    credentials = None
    try:
        if TOKEN_FILE.exists():
            credentials = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

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
            raise DriveShareAuthenticationError(
                "The Drive sharing token does not contain the drive scope."
            )

        TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
        return credentials
    except DriveShareAuthenticationError:
        raise
    except (GoogleAuthError, OSError, ValueError) as error:
        raise DriveShareAuthenticationError(
            "Drive sharing OAuth failed. Reauthorize the dedicated token."
        ) from error


def build_drive_share_service(credentials: Credentials | None = None):
    if credentials is None:
        credentials = authenticate_drive_share()
    return build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )


def list_permissions(service, file_id: str) -> list[dict]:
    """Read every ACL page for one exact file ID."""
    permissions: list[dict] = []
    page_token = None
    try:
        while True:
            response = (
                service.permissions()
                .list(
                    fileId=file_id,
                    pageSize=100,
                    pageToken=page_token,
                    fields=PERMISSION_FIELDS,
                    supportsAllDrives=True,
                )
                .execute(num_retries=0)
            )
            permissions.extend(response.get("permissions") or [])
            page_token = response.get("nextPageToken")
            if not page_token:
                return permissions
    except HttpError as error:
        status = getattr(error.resp, "status", None)
        raise DriveSharingError(
            f"Drive permission listing failed (HTTP {status or 'unknown'})."
        ) from None
    except (OSError, RuntimeError) as error:
        raise DriveSharingError("Drive permission listing failed.") from error


def create_anyone_reader_permission(service, file_id: str) -> str:
    """Create exactly one non-discoverable anyone/reader permission."""
    try:
        response = (
            service.permissions()
            .create(
                fileId=file_id,
                body={
                    "type": "anyone",
                    "role": "reader",
                    "allowFileDiscovery": False,
                },
                fields="id,type,role,allowFileDiscovery",
                supportsAllDrives=True,
            )
            .execute(num_retries=0)
        )
    except HttpError as error:
        status = getattr(error.resp, "status", None)
        raise DriveSharingError(
            "Drive anyone-reader permission creation failed "
            f"(HTTP {status or 'unknown'})."
        ) from None
    except (OSError, RuntimeError) as error:
        raise DriveSharingError(
            "Drive anyone-reader permission creation failed."
        ) from error

    permission_id = response.get("id")
    if not isinstance(permission_id, str) or not permission_id:
        raise DriveSharingError("Drive returned no permission ID.")
    if response.get("type") not in (None, "anyone"):
        raise DriveSharingError("Drive returned an unexpected permission type.")
    if response.get("role") not in (None, "reader"):
        raise DriveSharingError("Drive returned an unexpected permission role.")
    if response.get("allowFileDiscovery") is True:
        raise DriveSharingError("Drive returned a publicly discoverable permission.")
    return permission_id
