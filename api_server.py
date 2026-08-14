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
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from database import DATABASE_PATH, PROJECT_ROOT, connect_database
from search_service import SearchService


API_TITLE = "Python Drive Organizer Local Read-Only API"
API_DESCRIPTION = (
    "Read-only localhost API for the SQLite Google Drive metadata index. "
    "No Google Drive API calls or Drive actions are executed."
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
    version="1.2-MVP02",
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


app.include_router(protected_router)
