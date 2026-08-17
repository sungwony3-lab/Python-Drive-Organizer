import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


PROJECT_ROOT = Path(__file__).resolve().parent
CREDENTIALS_FILE = PROJECT_ROOT / "credentials.json"
TOKEN_FILE = PROJECT_ROOT / "contacts_sheet_token.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
SPREADSHEET_ID_ENV = "PDO_CONTACTS_SPREADSHEET_ID"
SHEET_NAME_ENV = "PDO_CONTACTS_SHEET_NAME"


class ContactsSheetError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ContactsSettings:
    spreadsheet_id: str
    sheet_name: str


@dataclass(frozen=True)
class ContactsSheetSnapshot:
    spreadsheet_id: str
    spreadsheet_title: str
    sheet_id: int
    sheet_name: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


def load_contacts_settings() -> ContactsSettings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    spreadsheet_id = os.getenv(SPREADSHEET_ID_ENV, "").strip()
    sheet_name = os.getenv(SHEET_NAME_ENV, "").strip()
    if not spreadsheet_id:
        raise ContactsSheetError(
            "CONTACTS_CONFIG_MISSING",
            f"{SPREADSHEET_ID_ENV} is not configured.",
        )
    if not sheet_name:
        raise ContactsSheetError(
            "CONTACTS_CONFIG_MISSING",
            f"{SHEET_NAME_ENV} is not configured.",
        )
    if any(character in spreadsheet_id for character in "\r\n"):
        raise ContactsSheetError(
            "CONTACTS_CONFIG_INVALID", "The configured spreadsheet ID is invalid."
        )
    if any(character in sheet_name for character in "\r\n"):
        raise ContactsSheetError(
            "CONTACTS_CONFIG_INVALID", "The configured sheet name is invalid."
        )
    return ContactsSettings(spreadsheet_id, sheet_name)


def _validate_scopes(credentials: Credentials) -> None:
    granted = set(credentials.scopes or ())
    expected = set(SCOPES)
    if granted and granted != expected:
        raise ContactsSheetError(
            "CONTACTS_SCOPE_MISMATCH",
            "The contacts token must contain only the Sheets read-only scope.",
        )
    if not credentials.has_scopes(SCOPES):
        raise ContactsSheetError(
            "CONTACTS_SCOPE_MISMATCH",
            "The contacts token is missing the Sheets read-only scope.",
        )


def authenticate_contacts_sheet() -> Credentials:
    credentials = None
    if TOKEN_FILE.exists():
        try:
            credentials = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            _validate_scopes(credentials)
        except (ValueError, OSError) as error:
            raise ContactsSheetError(
                "CONTACTS_TOKEN_INVALID",
                "The contacts Sheets token could not be loaded.",
            ) from error

    if credentials and credentials.valid:
        return credentials

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except RefreshError as error:
            raise ContactsSheetError(
                "CONTACTS_AUTH_FAILED",
                "The contacts Sheets token could not be refreshed.",
            ) from error
    else:
        if not CREDENTIALS_FILE.exists():
            raise ContactsSheetError(
                "CONTACTS_CREDENTIALS_MISSING",
                "credentials.json is required in the project root.",
            )
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        credentials = flow.run_local_server(port=0, timeout_seconds=300)

    _validate_scopes(credentials)
    TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


def build_contacts_sheets_service():
    return build(
        "sheets",
        "v4",
        credentials=authenticate_contacts_sheet(),
        cache_discovery=False,
    )


def _column_name(number: int) -> str:
    if number < 1:
        raise ValueError("Column number must be positive.")
    letters = []
    while number:
        number, remainder = divmod(number - 1, 26)
        letters.append(chr(65 + remainder))
    return "".join(reversed(letters))


def _quoted_sheet_name(sheet_name: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'"


def _http_error_code(error: HttpError, *, not_found_code: str) -> str:
    try:
        payload = json.loads(error.content.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    details = (payload.get("error") or {}).get("details") or []
    reasons = {detail.get("reason") for detail in details}
    if "SERVICE_DISABLED" in reasons:
        return "CONTACTS_SHEETS_API_DISABLED"
    status = getattr(error.resp, "status", None)
    if status == 404:
        return not_found_code
    if status in {401, 403}:
        return "CONTACTS_ACCESS_DENIED"
    return "CONTACTS_READ_FAILED"


def read_contacts_sheet(
    service, settings: ContactsSettings
) -> ContactsSheetSnapshot:
    try:
        metadata = (
            service.spreadsheets()
            .get(
                spreadsheetId=settings.spreadsheet_id,
                fields=(
                    "spreadsheetId,properties(title),"
                    "sheets(properties(sheetId,title,index,hidden,"
                    "gridProperties(rowCount,columnCount,frozenRowCount)))"
                ),
            )
            .execute(num_retries=0)
        )
    except HttpError as error:
        code = _http_error_code(
            error, not_found_code="CONTACTS_SPREADSHEET_NOT_FOUND"
        )
        raise ContactsSheetError(
            code, "The configured contacts spreadsheet could not be read."
        ) from error
    except (OSError, RuntimeError) as error:
        raise ContactsSheetError(
            "CONTACTS_READ_FAILED",
            "The configured contacts spreadsheet could not be read.",
        ) from error

    sheets = metadata.get("sheets") or []
    matches = [
        sheet.get("properties") or {}
        for sheet in sheets
        if (sheet.get("properties") or {}).get("title") == settings.sheet_name
    ]
    if len(matches) != 1:
        raise ContactsSheetError(
            "CONTACTS_TAB_NOT_FOUND",
            "The configured contacts tab was not found exactly once.",
        )

    properties = matches[0]
    sheet_id = properties.get("sheetId")
    if not isinstance(sheet_id, int):
        raise ContactsSheetError(
            "CONTACTS_TAB_INVALID", "The contacts tab has no valid sheetId."
        )
    grid = properties.get("gridProperties") or {}
    column_count = grid.get("columnCount")
    if not isinstance(column_count, int) or column_count < 5:
        column_count = 5
    header_end_column = _column_name(column_count)
    quoted_name = _quoted_sheet_name(settings.sheet_name)

    try:
        header_response = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=settings.spreadsheet_id,
                range=f"{quoted_name}!A1:{header_end_column}1",
                majorDimension="ROWS",
                valueRenderOption="FORMATTED_VALUE",
            )
            .execute(num_retries=0)
        )
        values_response = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=settings.spreadsheet_id,
                range=f"{quoted_name}!A2:E",
                majorDimension="ROWS",
                valueRenderOption="FORMATTED_VALUE",
            )
            .execute(num_retries=0)
        )
    except HttpError as error:
        code = _http_error_code(error, not_found_code="CONTACTS_TAB_NOT_FOUND")
        raise ContactsSheetError(
            code, "The contacts tab values could not be read."
        ) from error
    except (OSError, RuntimeError) as error:
        raise ContactsSheetError(
            "CONTACTS_READ_FAILED", "The contacts tab values could not be read."
        ) from error

    header_values = header_response.get("values") or []
    header = tuple(header_values[0]) if header_values else ()
    rows = tuple(tuple(row) for row in (values_response.get("values") or []))
    return ContactsSheetSnapshot(
        spreadsheet_id=metadata.get("spreadsheetId") or settings.spreadsheet_id,
        spreadsheet_title=(metadata.get("properties") or {}).get("title") or "",
        sheet_id=sheet_id,
        sheet_name=settings.sheet_name,
        header=header,
        rows=rows,
    )
