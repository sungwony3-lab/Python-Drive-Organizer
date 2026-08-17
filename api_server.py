from contextlib import asynccontextmanager
import sqlite3
from collections.abc import Generator
import hmac
import os
from typing import Annotated

from dotenv import load_dotenv
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Security,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, StrictBool

from database import DATABASE_PATH, PROJECT_ROOT, connect_database
from contacts_service import ContactsService
from drive_download_client import (
    DriveDownloadAuthenticationError,
    build_drive_download_service,
)
from drive_share_client import build_drive_share_service
from enhanced_email_service import (
    ENHANCED_DIAGNOSTIC_LOG_PATH,
    ENHANCED_STATE_DATABASE_PATH,
    EnhancedPreview,
    EnhancedSendResult,
    create_enhanced_preview,
    send_enhanced_email,
)
from email_service import (
    EmailServiceError,
    prepare_email_file,
    send_prepared_email,
)
from gmail_client import GmailAuthenticationError, build_gmail_service
from plain_email_service import (
    PLAIN_EMAIL_AUDIT_LOG_PATH,
    PLAIN_EMAIL_STATE_DATABASE_PATH,
    TextEmailPreview,
    TextEmailSendResult,
    create_text_email_preview,
    send_text_email as send_plain_text_email,
)
from search_service import SearchService
from tree_export_service import (
    EXPORT_AUDIT_LOG_PATH,
    EXPORT_DIRECTORY,
    TreeCursorError,
    TreeCursorStaleError,
    TreeExportFileTooLargeError,
    TreeExportNotFoundError,
    build_openai_file_response,
    build_tree_snapshot,
    create_tree_export,
    paginate_tree,
    resolve_export_file,
)


API_TITLE = "Python Drive Organizer API"
API_DESCRIPTION = (
    "Read-only SQLite Google Drive metadata and Contacts endpoints plus "
    "explicitly confirmed Gmail text/attachment/link endpoints. The only "
    "allowlisted Drive write is a user-approved non-discoverable "
    "anyone/reader permission for link delivery."
)
MAX_LIMIT = 1000
API_KEY_ENVIRONMENT_VARIABLE = "PDO_API_KEY"
MINIMUM_API_KEY_LENGTH = 32

bearer_scheme = HTTPBearer(
    auto_error=False,
    bearerFormat="API key",
    scheme_name="BearerAuth",
    description="Bearer API key from the PDO_API_KEY environment variable.",
)


def load_api_environment() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def configured_api_key() -> str:
    api_key = os.getenv(API_KEY_ENVIRONMENT_VARIABLE, "")
    if len(api_key) < MINIMUM_API_KEY_LENGTH:
        raise RuntimeError(
            "PDO_API_KEY must be configured with at least 32 characters."
        )
    return api_key


def require_api_key(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(bearer_scheme)
    ],
) -> None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Valid Bearer authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        expected_api_key = configured_api_key()
    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail="API authentication is not configured.",
        ) from error
    if not hmac.compare_digest(
        credentials.credentials.encode("utf-8"),
        expected_api_key.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=401,
            detail="Valid Bearer authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@asynccontextmanager
async def lifespan(application: FastAPI):
    load_api_environment()
    configured_api_key()
    yield

app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version="1.7-MVP01.1",
    lifespan=lifespan,
)
protected_router = APIRouter(dependencies=[Depends(require_api_key)])


class HealthResponse(BaseModel):
    status: str
    service: str


class StatusResponse(BaseModel):
    files_count: int
    folders_count: int
    groups_count: int
    auto_delete_count: int
    latest_scan_id: str | None
    latest_scan_status: str | None


class FileItem(BaseModel):
    file_id: str
    name: str
    path: str
    extension: str | None
    modified_time: str | None
    revision_type: str | None
    revision_number: int | None
    copy_type: str | None
    copy_number: int | None
    auto_action: str
    group_id: str | None


class FileSearchResponse(BaseModel):
    query: str
    total: int
    showing: int
    items: list[FileItem]


class FolderItem(BaseModel):
    folder_id: str
    name: str
    path: str
    parent_id: str | None


class FolderSearchResponse(BaseModel):
    query: str
    total: int
    showing: int
    items: list[FolderItem]


class ChildItem(BaseModel):
    item_type: str
    item_id: str
    name: str
    path: str
    parent_id: str | None
    extension: str | None = None
    modified_time: str | None = None


class FolderChildrenResponse(BaseModel):
    folder_id: str
    recursive: bool
    total: int
    showing: int
    items: list[ChildItem]


class TreeResponse(BaseModel):
    root_folder: str | None
    max_depth: int | None
    include_files: bool
    folder_count: int
    file_count: int
    tree_text: str


class TreeNodeItem(BaseModel):
    node_type: str
    name: str
    level: int
    path: str
    parent_id: str | None
    id: str
    mime_type: str | None
    modified_time: str | None
    extension: str | None


class TreePageRequest(BaseModel):
    root_folder: str | None = None
    include_files: StrictBool = False
    max_depth: int | None = Field(default=None, ge=0)
    page_size: int = Field(default=500, ge=1, le=1000)
    cursor: str | None = None


class TreePageResponse(BaseModel):
    total_nodes: int
    showing: int
    next_cursor: str | None
    has_more: bool
    items: list[TreeNodeItem]
    latest_scan_id: str | None
    latest_scan_status: str | None
    scan_finished_at: str | None


class TreeExportRequest(BaseModel):
    format: str = Field(pattern="^(txt|docx|xlsx)$")
    root_folder: str | None = None
    include_files: StrictBool = True
    max_depth: int | None = Field(default=None, ge=0)


class TreeExportResponse(BaseModel):
    export_id: str
    format: str
    filename: str
    node_count: int
    folder_count: int
    file_count: int
    latest_scan_id: str | None
    latest_scan_status: str | None
    scan_finished_at: str | None
    created_at: str
    download_endpoint: str


class OpenAIFileItem(BaseModel):
    name: str
    mime_type: str
    content: str


class OpenAIFileResponse(BaseModel):
    openaiFileResponse: list[OpenAIFileItem]


class RevisionItem(BaseModel):
    file_id: str
    name: str
    path: str
    revision_number: int | None
    group_id: str | None
    latest_revision_number: int | None


class RevisionResponse(BaseModel):
    min_revision: int | None
    total: int
    showing: int
    items: list[RevisionItem]


class CopyItem(BaseModel):
    file_id: str
    name: str
    path: str
    copy_type: str
    copy_number: int | None
    auto_action: str
    group_id: str | None


class CopyResponse(BaseModel):
    total: int
    showing: int
    items: list[CopyItem]


class AutoDeleteResponse(CopyResponse):
    classification_only: bool
    drive_action_executed: bool


class GroupMember(BaseModel):
    group_id: str
    file_id: str
    name: str
    member_type: str
    revision_number: int | None
    copy_number: int | None
    auto_action: str


class GroupItem(BaseModel):
    group_id: str
    parent_id: str | None
    folder_path: str
    group_base_name: str
    extension: str | None
    member_count: int
    revision_count: int
    copy_count: int
    auto_delete_count: int
    latest_revision_number: int | None
    members: list[GroupMember]


class GroupResponse(BaseModel):
    min_members: int
    total: int
    showing: int
    items: list[GroupItem]


class RecentItem(BaseModel):
    file_id: str
    name: str
    path: str
    modified_time: str | None


class RecentResponse(BaseModel):
    total: int
    showing: int
    items: list[RecentItem]


class ContactSearchRequest(BaseModel):
    q: str | None = None
    organization: str | None = None
    name: str | None = None
    title: str | None = None
    email: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ContactItem(BaseModel):
    contact_id: str
    organization: str | None
    name: str
    title: str | None
    email: str | None
    phone: str | None
    email_usable: bool
    conflict_code: str | None


class ContactSearchResponse(BaseModel):
    total: int
    showing: int
    items: list[ContactItem]


class ContactsStatusResponse(BaseModel):
    latest_sync_id: str | None
    latest_sync_status: str | None
    last_success_at: str | None
    rows_seen: int
    valid_rows: int
    inserted: int
    updated: int
    deleted: int
    unchanged: int
    invalid: int
    conflicts: int


class EmailSendRequest(BaseModel):
    file_id: str
    to: str
    subject: str
    body: str
    confirmed: StrictBool
    idempotency_key: str


class EmailSendResponse(BaseModel):
    status: str
    message_id: str
    file_id: str
    file_name: str
    recipient: str
    idempotent_replay: bool


class EnhancedEmailPreviewRequest(BaseModel):
    file_ids: list[str]
    to: str
    cc: list[str] = Field(default_factory=list)
    subject: str
    body: str
    mode: str


class EnhancedEmailSendRequest(EnhancedEmailPreviewRequest):
    preview_id: str = Field(
        min_length=1,
        description=(
            "Use exactly the preview_id returned by the immediately preceding "
            "successful previewEmailWithFiles call for the same payload. Never "
            "invent or substitute it."
        ),
    )
    confirmed: StrictBool
    idempotency_key: str


class EnhancedEmailFile(BaseModel):
    file_id: str
    name: str
    mime_type: str | None = None
    size_bytes: int | None = None
    delivery: str | None = None


class EnhancedSharingChange(BaseModel):
    file_id: str
    action: str
    permission_type: str
    role: str
    allow_file_discovery: bool


class EnhancedEmailPreviewResponse(BaseModel):
    preview_id: str
    expires_at: str
    requested_mode: str
    delivery_mode: str
    sharing_mode: str
    file_count: int
    total_size_bytes: int | None
    files: list[EnhancedEmailFile]
    recipient: str
    cc: list[str]
    sharing_changes: list[EnhancedSharingChange]


class EnhancedEmailSendResponse(BaseModel):
    status: str
    delivery_mode: str
    sharing_mode: str
    file_count: int
    files: list[EnhancedEmailFile]
    recipient: str
    cc: list[str]
    message_id: str
    sharing_changes: list[EnhancedSharingChange]
    idempotent_replay: bool


class TextEmailPreviewRequest(BaseModel):
    to: str
    cc: list[str] = Field(default_factory=list)
    subject: str
    body: str


class TextEmailSendRequest(TextEmailPreviewRequest):
    preview_id: str = Field(
        min_length=1,
        description=(
            "Use exactly the preview_id returned by the immediately preceding "
            "successful previewTextEmail call for the same unchanged payload. "
            "Never invent or substitute it."
        ),
    )
    confirmed: StrictBool
    idempotency_key: str


class TextEmailPreviewResponse(BaseModel):
    preview_id: str
    expires_at: str
    recipient: str
    cc: list[str]
    subject: str
    body: str
    attachment_count: int
    drive_link_count: int


class TextEmailSendResponse(BaseModel):
    status: str
    message_id: str
    recipient: str
    cc: list[str]
    attachment_count: int
    drive_link_count: int
    idempotent_replay: bool


Limit = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_LIMIT,
        description="Maximum number of result items to return (1-1000).",
    ),
]


def get_connection() -> Generator[sqlite3.Connection, None, None]:
    try:
        connection = connect_database(DATABASE_PATH, read_only=True)
    except (OSError, sqlite3.Error) as error:
        raise HTTPException(
            status_code=503, detail="SQLite index is unavailable."
        ) from error
    try:
        yield connection
    finally:
        connection.close()


Connection = Annotated[sqlite3.Connection, Depends(get_connection)]


@app.exception_handler(sqlite3.Error)
async def sqlite_error_handler(
    request: Request, error: sqlite3.Error
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "SQLite index could not be read."},
    )


def search_service(connection: sqlite3.Connection) -> SearchService:
    return SearchService(connection)


def contacts_service(connection: sqlite3.Connection) -> ContactsService:
    return ContactsService(connection)


def validate_query(value: str) -> str:
    query = value.strip()
    if not query:
        raise HTTPException(status_code=422, detail="Search query cannot be empty.")
    return query


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Check local API health",
    description="Returns process health without reading SQLite or Google Drive.",
)
def health() -> dict:
    return {"status": "ok", "service": "python-drive-organizer"}


@protected_router.get(
    "/status",
    response_model=StatusResponse,
    summary="Get current Drive index status",
    description="Returns current counts and the latest SQLite scan status.",
)
def status(connection: Connection) -> dict:
    latest_scan = connection.execute(
        """
        SELECT scan_id, status
        FROM scan_state
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
        """
    ).fetchone()
    return {
        "files_count": connection.execute(
            "SELECT COUNT(*) FROM files"
        ).fetchone()[0],
        "folders_count": connection.execute(
            "SELECT COUNT(*) FROM folders"
        ).fetchone()[0],
        "groups_count": connection.execute(
            "SELECT COUNT(*) FROM file_groups"
        ).fetchone()[0],
        "auto_delete_count": connection.execute(
            "SELECT COUNT(*) FROM files WHERE auto_action = 'DELETE'"
        ).fetchone()[0],
        "latest_scan_id": latest_scan["scan_id"] if latest_scan else None,
        "latest_scan_status": latest_scan["status"] if latest_scan else None,
    }


@protected_router.get(
    "/files/search",
    response_model=FileSearchResponse,
    summary="Search indexed files by name",
    description=(
        "Case-insensitive substring search over name, normalized_name, and "
        "base_name in SQLite."
    ),
)
def search_files(
    connection: Connection,
    q: Annotated[str, Query(description="Filename substring to search.")],
    limit: Limit = 100,
) -> dict:
    query = validate_query(q)
    result = search_service(connection).search_name(query, limit)
    return {
        "query": query,
        "total": result.total,
        "showing": result.showing,
        "items": result.items,
    }


@protected_router.get(
    "/folders/search",
    response_model=FolderSearchResponse,
    summary="Search indexed folders by name",
    description="Case-insensitive folder-name substring search in SQLite.",
)
def search_folders(
    connection: Connection,
    q: Annotated[str, Query(description="Folder-name substring to search.")],
    limit: Limit = 100,
) -> dict:
    query = validate_query(q)
    result = search_service(connection).search_folders(query, limit)
    return {
        "query": query,
        "total": result.total,
        "showing": result.showing,
        "items": result.items,
    }


@protected_router.get(
    "/folders/tree",
    response_model=TreeResponse,
    summary="Render the indexed folder tree",
    description=(
        "Builds a deterministic text tree from SQLite only, with cycle and "
        "missing-parent protection."
    ),
)
def folder_tree(
    connection: Connection,
    root_folder: Annotated[
        str | None,
        Query(description="Optional folder_id to use as the tree root."),
    ] = None,
    max_depth: Annotated[
        int | None,
        Query(ge=0, description="Optional maximum depth from the root."),
    ] = None,
    include_files: Annotated[
        bool, Query(description="Include indexed files in the text tree.")
    ] = False,
) -> dict:
    try:
        result = search_service(connection).render_tree(
            root_folder, max_depth, include_files
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "root_folder": root_folder,
        "max_depth": max_depth,
        "include_files": include_files,
        "folder_count": len(result.folder_ids),
        "file_count": len(result.file_ids),
        "tree_text": result.text,
    }


@protected_router.post(
    "/folders/tree/page",
    response_model=TreePageResponse,
    summary="Page through the complete indexed Drive tree",
    description=(
        "Returns deterministic SQLite tree nodes with an opaque signed cursor. "
        "Continue until next_cursor is null; no Google Drive call is made."
    ),
    openapi_extra={"x-openai-isConsequential": False},
)
def folder_tree_page(payload: TreePageRequest, connection: Connection) -> dict:
    try:
        result, snapshot = build_tree_snapshot(
            connection,
            root_folder=payload.root_folder,
            include_files=payload.include_files,
            max_depth=payload.max_depth,
        )
        return paginate_tree(
            result,
            snapshot,
            root_folder=payload.root_folder,
            include_files=payload.include_files,
            max_depth=payload.max_depth,
            page_size=payload.page_size,
            cursor=payload.cursor,
            secret=configured_api_key(),
        )
    except TreeCursorStaleError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "TREE_CURSOR_STALE", "message": str(error)},
        ) from error
    except TreeCursorError as error:
        raise HTTPException(
            status_code=400,
            detail={"code": "TREE_CURSOR_INVALID", "message": str(error)},
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@protected_router.post(
    "/exports/drive-tree",
    response_model=TreeExportResponse,
    summary="Export the complete indexed Drive tree",
    description=(
        "Creates one UTF-8 TXT, Word DOCX, or Excel XLSX from the complete "
        "SQLite tree. It performs no Google Drive API call or Drive write."
    ),
    openapi_extra={"x-openai-isConsequential": False},
)
def export_drive_tree(payload: TreeExportRequest, connection: Connection) -> dict:
    try:
        result = create_tree_export(
            connection,
            export_format=payload.format,
            root_folder=payload.root_folder,
            include_files=payload.include_files,
            max_depth=payload.max_depth,
            export_directory=EXPORT_DIRECTORY,
            audit_log_path=EXPORT_AUDIT_LOG_PATH,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (OSError, RuntimeError) as error:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "TREE_EXPORT_FAILED",
                "message": "The Drive tree export could not be created.",
            },
        ) from error
    response = result.__dict__.copy()
    response["download_endpoint"] = f"/exports/{result.export_id}"
    return response


@protected_router.get(
    "/exports/{export_id}",
    summary="Download one authenticated Drive tree export",
    description=(
        "Returns an exported file by opaque ID. Bearer authentication is "
        "required and no API key is accepted in the URL query string."
    ),
    responses={
        200: {
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            }
        }
    },
    openapi_extra={"x-openai-isConsequential": False},
)
def download_drive_tree_export(export_id: str):
    try:
        path, media_type = resolve_export_file(
            export_id, export_directory=EXPORT_DIRECTORY
        )
    except TreeExportNotFoundError as error:
        raise HTTPException(status_code=404, detail="Export not found.") from error
    return FileResponse(path, media_type=media_type, filename=path.name)


@protected_router.post(
    "/exports/{export_id}/openai-file",
    response_model=OpenAIFileResponse,
    summary="Return one Drive tree export to a GPT conversation",
    description=(
        "Returns the exact export as an OpenAI openaiFileResponse JSON array. "
        "Use the export_id from exportDriveTree; each file is limited to 10 MB."
    ),
    openapi_extra={"x-openai-isConsequential": False},
)
def return_drive_tree_export(export_id: str) -> dict:
    try:
        return build_openai_file_response(
            export_id,
            export_directory=EXPORT_DIRECTORY,
            audit_log_path=EXPORT_AUDIT_LOG_PATH,
        )
    except TreeExportNotFoundError as error:
        raise HTTPException(status_code=404, detail="Export not found.") from error
    except TreeExportFileTooLargeError as error:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "GPT_FILE_TOO_LARGE",
                "message": str(error),
            },
        ) from error


@protected_router.get(
    "/folders/{folder_id}/children",
    response_model=FolderChildrenResponse,
    summary="List child items of an indexed folder",
    description="Lists direct children or all descendants from SQLite only.",
)
def folder_children(
    folder_id: str,
    connection: Connection,
    recursive: Annotated[
        bool, Query(description="Include all descendants when true.")
    ] = False,
    limit: Limit = 100,
) -> dict:
    try:
        result = search_service(connection).list_folder(
            folder_id, recursive, limit
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "folder_id": folder_id,
        "recursive": recursive,
        "total": result.total,
        "showing": result.showing,
        "items": result.items,
    }


@protected_router.get(
    "/revisions",
    response_model=RevisionResponse,
    summary="List parsed Revision files",
    description="Returns existing MVP-05 Revision classifications from SQLite.",
)
def revisions(
    connection: Connection,
    min_revision: Annotated[
        int | None,
        Query(ge=0, description="Optional minimum parsed Revision number."),
    ] = None,
    limit: Limit = 100,
) -> dict:
    result = search_service(connection).search_revisions(limit, min_revision)
    return {
        "min_revision": min_revision,
        "total": result.total,
        "showing": result.showing,
        "items": result.items,
    }


@protected_router.get(
    "/copies",
    response_model=CopyResponse,
    summary="List parsed Copy files",
    description="Returns existing MVP-05 Copy classifications from SQLite.",
)
def copies(connection: Connection, limit: Limit = 100) -> dict:
    result = search_service(connection).search_copies(limit)
    return {
        "total": result.total,
        "showing": result.showing,
        "items": result.items,
    }


@protected_router.get(
    "/auto-delete",
    response_model=AutoDeleteResponse,
    summary="List auto-delete classifications",
    description=(
        "Returns only existing auto_action=DELETE classifications. No Drive "
        "action or new classification is performed."
    ),
)
def auto_delete(connection: Connection, limit: Limit = 100) -> dict:
    result = search_service(connection).search_auto_delete(limit)
    return {
        "classification_only": True,
        "drive_action_executed": False,
        "total": result.total,
        "showing": result.showing,
        "items": result.items,
    }


@protected_router.get(
    "/groups",
    response_model=GroupResponse,
    summary="List deterministic file groups",
    description="Returns MVP-06 group statistics and member metadata.",
)
def groups(
    connection: Connection,
    min_members: Annotated[
        int, Query(ge=1, description="Minimum member_count for a group.")
    ] = 1,
    limit: Limit = 100,
) -> dict:
    result = search_service(connection).search_groups(min_members, limit)
    return {
        "min_members": min_members,
        "total": result.total,
        "showing": result.showing,
        "items": result.items,
    }


@protected_router.get(
    "/recent",
    response_model=RecentResponse,
    summary="List recently modified indexed files",
    description="Sorts current SQLite files by modified_time descending.",
)
def recent(connection: Connection, limit: Limit = 20) -> dict:
    result = search_service(connection).recent(limit)
    return {
        "total": result.total,
        "showing": result.showing,
        "items": result.items,
    }


@protected_router.post(
    "/contacts/search",
    response_model=ContactSearchResponse,
    summary="Search the current SQLite contacts snapshot",
    description=(
        "Searches normalized contact fields in SQLite only. Search values are "
        "accepted in the request body and are not sent to Google Sheets."
    ),
    openapi_extra={"x-openai-isConsequential": False},
)
def search_contacts(
    payload: ContactSearchRequest,
    connection: Connection,
) -> dict:
    try:
        result = contacts_service(connection).search(
            q=payload.q,
            organization=payload.organization,
            name=payload.name,
            title=payload.title,
            email=payload.email,
            limit=payload.limit,
        )
    except ValueError as error:
        if str(error) == "CONTACT_SEARCH_CRITERIA_REQUIRED":
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "CONTACT_SEARCH_CRITERIA_REQUIRED",
                    "message": "At least one contact search criterion is required.",
                },
            ) from error
        raise
    return {
        "total": result.total,
        "showing": result.showing,
        "items": result.items,
    }


@protected_router.get(
    "/contacts/status",
    response_model=ContactsStatusResponse,
    summary="Get the latest Contacts synchronization status",
    description="Returns Contacts sync state from SQLite without calling Sheets.",
)
def contacts_status(connection: Connection) -> dict:
    return contacts_service(connection).status()


@protected_router.get(
    "/contacts/{contact_id}",
    response_model=ContactItem,
    summary="Get one contact by exact opaque ID",
    description="Returns only an exact SQLite contact_id match without fallback.",
)
def get_contact(contact_id: str, connection: Connection) -> dict:
    item = contacts_service(connection).get_contact(contact_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CONTACT_NOT_FOUND",
                "message": "The requested contact_id was not found.",
            },
        )
    return item


EMAIL_ERROR_STATUS = {
    "INVALID_RECIPIENT": 400,
    "INVALID_SUBJECT": 400,
    "INVALID_BODY": 400,
    "INVALID_IDEMPOTENCY_KEY": 400,
    "FILE_TRASHED": 400,
    "UNSUPPORTED_NATIVE_FILE": 400,
    "UNSUPPORTED_FOLDER": 400,
    "UNSUPPORTED_SHORTCUT": 400,
    "ATTACHMENT_TOO_LARGE": 400,
    "FILE_NOT_INDEXED": 404,
    "DRIVE_FILE_NOT_FOUND": 404,
    "IDEMPOTENCY_CONFLICT": 409,
    "IDEMPOTENCY_IN_PROGRESS": 409,
    "IDEMPOTENCY_PREVIOUSLY_FAILED": 409,
    "GMAIL_DELIVERY_UNCERTAIN": 409,
    "INDEX_UNAVAILABLE": 503,
    "GMAIL_API_NOT_ENABLED": 503,
    "DOWNLOAD_FAILED": 502,
    "GMAIL_SEND_FAILED": 502,
    "INVALID_CC": 400,
    "TOO_MANY_CC": 400,
    "INVALID_FILE_IDS": 400,
    "DUPLICATE_FILE_ID": 400,
    "TOO_MANY_FILES": 400,
    "INVALID_MODE": 400,
    "ATTACHMENT_MODE_UNSUPPORTED": 400,
    "TOTAL_ATTACHMENT_TOO_LARGE": 400,
    "RAW_MESSAGE_TOO_LARGE": 400,
    "LINK_UNAVAILABLE": 400,
    "LINK_PERMISSION_TOO_BROAD": 409,
    "PREVIEW_NOT_FOUND": 404,
    "PREVIEW_EXPIRED": 409,
    "PREVIEW_STALE": 409,
    "SHARING_PARTIAL": 409,
    "SHARING_FAILED": 502,
    "DRIVE_SHARE_AUTH_FAILED": 503,
    "DRIVE_AUTH_FAILED": 503,
    "GMAIL_AUTH_FAILED": 503,
}


def email_http_exception(error: EmailServiceError) -> HTTPException:
    return HTTPException(
        status_code=EMAIL_ERROR_STATUS.get(error.code, 500),
        detail={"code": error.code, "message": error.message},
    )


@protected_router.post(
    "/email/send-file",
    response_model=EmailSendResponse,
    summary="Send one Drive file as a Gmail attachment",
    description=(
        "Requires an exact indexed Drive file_id and explicit final user "
        "confirmation. Sends one attachment to one recipient and causes an "
        "actual external email delivery attempt. Never performs a Drive write."
    ),
    openapi_extra={"x-openai-isConsequential": True},
)
def send_email_file(payload: EmailSendRequest) -> dict:
    if payload.confirmed is not True:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CONFIRMATION_REQUIRED",
                "message": "Explicit final confirmation is required.",
            },
        )

    try:
        drive_service = build_drive_download_service()
        prepared = prepare_email_file(
            drive_service=drive_service,
            file_id=payload.file_id,
            recipient=payload.to,
            subject=payload.subject,
            body=payload.body,
            idempotency_key=payload.idempotency_key,
            database_path=DATABASE_PATH,
        )
        result = send_prepared_email(
            prepared=prepared,
            drive_service=drive_service,
            gmail_service_factory=build_gmail_service,
        )
    except DriveDownloadAuthenticationError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DRIVE_AUTH_FAILED",
                "message": "Drive download authentication is unavailable.",
            },
        ) from error
    except GmailAuthenticationError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "GMAIL_AUTH_FAILED",
                "message": "Gmail send authentication is unavailable.",
            },
        ) from error
    except EmailServiceError as error:
        raise email_http_exception(error) from error

    return {
        "status": result.status,
        "message_id": result.message_id,
        "file_id": result.file_id,
        "file_name": result.file_name,
        "recipient": result.recipient,
        "idempotent_replay": result.idempotent_replay,
    }


def text_preview_response(preview: TextEmailPreview) -> dict:
    return {
        "preview_id": preview.preview_id,
        "expires_at": preview.expires_at,
        "recipient": preview.recipient,
        "cc": list(preview.cc),
        "subject": preview.subject,
        "body": preview.body,
        "attachment_count": 0,
        "drive_link_count": 0,
    }


def text_send_response(result: TextEmailSendResult) -> dict:
    return {
        "status": result.status,
        "message_id": result.message_id,
        "recipient": result.recipient,
        "cc": list(result.cc),
        "attachment_count": 0,
        "drive_link_count": 0,
        "idempotent_replay": result.idempotent_replay,
    }


@protected_router.post(
    "/email/send-text/preview",
    response_model=TextEmailPreviewResponse,
    summary="Preview one plain-text email without sending",
    description=(
        "Validates one To, up to five CC addresses, a one-line subject, and a "
        "plain-text body. Returns a short-lived preview without Gmail, Drive, "
        "permission, attachment, or Google Sheets calls."
    ),
    openapi_extra={"x-openai-isConsequential": False},
)
def preview_text_email(payload: TextEmailPreviewRequest) -> dict:
    try:
        preview = create_text_email_preview(
            recipient=payload.to,
            cc=payload.cc,
            subject=payload.subject,
            body=payload.body,
            state_database_path=PLAIN_EMAIL_STATE_DATABASE_PATH,
        )
    except EmailServiceError as error:
        raise email_http_exception(error) from error
    return text_preview_response(preview)


@protected_router.post(
    "/email/send-text",
    response_model=TextEmailSendResponse,
    summary="Send one confirmed plain-text email",
    description=(
        "After explicit approval, sends the unchanged plain-text payload using "
        "exactly the preview_id returned by previewTextEmail. It has no files, "
        "Drive links, or Drive writes and must not be retried automatically."
    ),
    openapi_extra={"x-openai-isConsequential": True},
)
def send_text_email(payload: TextEmailSendRequest) -> dict:
    if payload.confirmed is not True:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CONFIRMATION_REQUIRED",
                "message": "Explicit final confirmation is required.",
            },
        )
    try:
        result = send_plain_text_email(
            preview_id=payload.preview_id,
            recipient=payload.to,
            cc=payload.cc,
            subject=payload.subject,
            body=payload.body,
            idempotency_key=payload.idempotency_key,
            gmail_service_factory=build_gmail_service,
            state_database_path=PLAIN_EMAIL_STATE_DATABASE_PATH,
            audit_log_path=PLAIN_EMAIL_AUDIT_LOG_PATH,
        )
    except EmailServiceError as error:
        raise email_http_exception(error) from error
    return text_send_response(result)


def enhanced_preview_response(preview: EnhancedPreview) -> dict:
    return {
        "preview_id": preview.preview_id,
        "expires_at": preview.expires_at,
        "requested_mode": preview.requested_mode,
        "delivery_mode": preview.delivery_mode,
        "sharing_mode": preview.sharing_mode,
        "file_count": preview.file_count,
        "total_size_bytes": preview.total_size_bytes,
        "files": [
            {
                "file_id": file.file_id,
                "name": file.name,
                "mime_type": file.mime_type,
                "size_bytes": file.size_bytes,
            }
            for file in preview.files
        ],
        "recipient": preview.recipient,
        "cc": list(preview.cc),
        "sharing_changes": [
            {
                "file_id": change.file_id,
                "action": change.action,
                "permission_type": change.permission_type,
                "role": change.role,
                "allow_file_discovery": change.allow_file_discovery,
            }
            for change in preview.sharing_changes
        ],
    }


def enhanced_send_response(result: EnhancedSendResult) -> dict:
    return {
        "status": result.status,
        "delivery_mode": result.delivery_mode,
        "sharing_mode": result.sharing_mode,
        "file_count": result.file_count,
        "files": list(result.files),
        "recipient": result.recipient,
        "cc": list(result.cc),
        "message_id": result.message_id,
        "sharing_changes": list(result.sharing_changes),
        "idempotent_replay": result.idempotent_replay,
    }


@protected_router.post(
    "/email/send-files/preview",
    response_model=EnhancedEmailPreviewResponse,
    summary="Preview one enhanced email without external changes",
    description=(
        "Validates 1-5 exact Drive file IDs, normalizes CC, resolves attachment "
        "or link delivery, and computes a sharing plan. It never downloads a "
        "file, creates a permission, or sends Gmail."
    ),
    openapi_extra={"x-openai-isConsequential": False},
)
def preview_email_files(payload: EnhancedEmailPreviewRequest) -> dict:
    try:
        drive_service = build_drive_download_service()
        preview = create_enhanced_preview(
            drive_service=drive_service,
            file_ids=payload.file_ids,
            recipient=payload.to,
            cc=payload.cc,
            subject=payload.subject,
            body=payload.body,
            mode=payload.mode,
            database_path=DATABASE_PATH,
            state_database_path=ENHANCED_STATE_DATABASE_PATH,
            diagnostic_log_path=ENHANCED_DIAGNOSTIC_LOG_PATH,
        )
    except DriveDownloadAuthenticationError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DRIVE_AUTH_FAILED",
                "message": "Drive read authentication is unavailable.",
            },
        ) from error
    except EmailServiceError as error:
        raise email_http_exception(error) from error
    return enhanced_preview_response(preview)


@protected_router.post(
    "/email/send-files",
    response_model=EnhancedEmailSendResponse,
    summary="Send one confirmed enhanced email",
    description=(
        "Uses exactly the preview_id returned by the immediately preceding "
        "successful preview call for the same unchanged payload. Never invents "
        "or substitutes it. Attachment mode sends 1-5 binary files. Link mode "
        "may create only non-discoverable anyone/reader permissions after "
        "explicit approval, then sends one Gmail message."
    ),
    openapi_extra={"x-openai-isConsequential": True},
)
def send_email_files(payload: EnhancedEmailSendRequest) -> dict:
    if payload.confirmed is not True:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CONFIRMATION_REQUIRED",
                "message": "Explicit final confirmation is required.",
            },
        )
    try:
        result = send_enhanced_email(
            preview_id=payload.preview_id,
            file_ids=payload.file_ids,
            recipient=payload.to,
            cc=payload.cc,
            subject=payload.subject,
            body=payload.body,
            mode=payload.mode,
            idempotency_key=payload.idempotency_key,
            drive_service_factory=build_drive_download_service,
            gmail_service_factory=build_gmail_service,
            share_service_factory=build_drive_share_service,
            database_path=DATABASE_PATH,
            state_database_path=ENHANCED_STATE_DATABASE_PATH,
            diagnostic_log_path=ENHANCED_DIAGNOSTIC_LOG_PATH,
        )
    except EmailServiceError as error:
        raise email_http_exception(error) from error
    return enhanced_send_response(result)


app.include_router(protected_router)
