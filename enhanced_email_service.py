import base64
import hashlib
import json
import os
import secrets
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from googleapiclient.errors import HttpError

from database import DATABASE_PATH, connect_database
from drive_download_client import (
    DownloadSizeLimitExceeded,
    DriveDownloadAuthenticationError,
    download_file,
    get_file_metadata,
)
from drive_share_client import (
    DriveShareAuthenticationError,
    DriveSharingError,
    create_anyone_reader_permission,
    list_permissions,
)
from email_service import (
    EmailServiceError,
    FOLDER_MIME_TYPE,
    GOOGLE_NATIVE_PREFIX,
    MAX_ATTACHMENT_BYTES,
    SHORTCUT_MIME_TYPE,
    mask_recipient,
    validate_idempotency_key,
    validate_message_fields,
    validate_recipient,
)
from gmail_client import (
    GmailApiNotEnabledError,
    GmailAuthenticationError,
    GmailDeliveryUncertainError,
    GmailSendError,
    send_message,
)


PROJECT_ROOT = Path(__file__).resolve().parent
MAX_FILES = 5
MAX_CC = 5
MAX_GMAIL_RAW_BYTES = 34 * 1024 * 1024
PREVIEW_TTL_SECONDS = 10 * 60
ENHANCED_STATE_DATABASE_PATH = PROJECT_ROOT / "data" / "enhanced_email_state.db"
ENHANCED_AUDIT_LOG_PATH = PROJECT_ROOT / "logs" / "enhanced_email_send.log"
ENHANCED_DIAGNOSTIC_LOG_PATH = PROJECT_ROOT / "logs" / "enhanced_email_debug.log"
ALLOWED_MODES = {"auto", "attachment", "link"}
SHARING_MODE_NONE = "none"
SHARING_MODE_ANYONE_WITH_LINK_READER = "anyone_with_link_reader"


@dataclass(frozen=True)
class EnhancedFile:
    file_id: str
    name: str
    mime_type: str
    size_bytes: int | None
    modified_time: str | None
    version: str | None
    can_download: bool
    can_share: bool
    web_view_link: str | None
    is_native: bool


@dataclass(frozen=True)
class SharingChange:
    file_id: str
    action: str
    permission_type: str = "anyone"
    role: str = "reader"
    allow_file_discovery: bool = False


@dataclass(frozen=True)
class EnhancedPreview:
    preview_id: str
    expires_at: str
    requested_mode: str
    delivery_mode: str
    sharing_mode: str
    file_count: int
    total_size_bytes: int | None
    files: tuple[EnhancedFile, ...]
    recipient: str
    cc: tuple[str, ...]
    sharing_changes: tuple[SharingChange, ...]
    plan_hash: str
    metadata_signature_hash: str
    sharing_plan_hash: str


@dataclass(frozen=True)
class EnhancedSendResult:
    status: str
    delivery_mode: str
    sharing_mode: str
    file_count: int
    files: tuple[dict, ...]
    recipient: str
    cc: tuple[str, ...]
    message_id: str
    sharing_changes: tuple[dict, ...]
    idempotent_replay: bool = False


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds")


def canonical_address(value: str) -> str:
    return value.casefold()


def canonical_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def absolute_path(path: Path) -> str:
    return str(Path(path).resolve())


def _diagnostic(
    path: Path,
    *,
    event: str,
    preview_id: str,
    state_database_path: Path,
    preview_exists: bool | None = None,
    preview_expired: bool | None = None,
    preview_id_matches_stored: bool | None = None,
    expires_at: str | None = None,
) -> None:
    """Append correlation-only diagnostics without message or credential data."""
    event_data = {
        "timestamp": utc_text(),
        "event": event,
        "preview_id": preview_id,
        "state_database_path": absolute_path(state_database_path),
        "process_id": os.getpid(),
        "working_directory": absolute_path(Path.cwd()),
        "preview_exists": preview_exists,
        "preview_expired": preview_expired,
        "preview_id_matches_stored": preview_id_matches_stored,
        "expires_at": expires_at,
        "cleanup_configured": False,
    }
    log_path = Path(path)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(canonical_json(event_data) + "\n")
    except OSError:
        # Diagnostics must never change the preview/send outcome.
        return


def normalize_cc(recipient: str, cc) -> tuple[str, ...]:
    if not isinstance(cc, list):
        raise EmailServiceError("INVALID_CC", "CC must be a list of addresses.")

    recipient_key = canonical_address(recipient)
    seen: set[str] = set()
    normalized: list[str] = []
    for value in cc:
        try:
            address = validate_recipient(value)
        except EmailServiceError as error:
            raise EmailServiceError("INVALID_CC", error.message) from None
        key = canonical_address(address)
        if key == recipient_key or key in seen:
            continue
        seen.add(key)
        normalized.append(address)

    if len(normalized) > MAX_CC:
        raise EmailServiceError(
            "TOO_MANY_CC", f"CC supports at most {MAX_CC} unique addresses."
        )
    return tuple(normalized)


def normalize_file_ids(file_ids) -> tuple[str, ...]:
    if not isinstance(file_ids, list) or not file_ids:
        raise EmailServiceError(
            "INVALID_FILE_IDS", "file_ids must contain 1-5 exact IDs."
        )
    if len(file_ids) > MAX_FILES:
        raise EmailServiceError(
            "TOO_MANY_FILES", f"At most {MAX_FILES} files can be sent."
        )

    normalized: list[str] = []
    seen: set[str] = set()
    for value in file_ids:
        if (
            not isinstance(value, str)
            or not value.strip()
            or any(character in value for character in "\r\n")
        ):
            raise EmailServiceError(
                "INVALID_FILE_IDS", "Every file_id must be a non-empty exact ID."
            )
        file_id = value.strip()
        if file_id in seen:
            raise EmailServiceError(
                "DUPLICATE_FILE_ID", "Duplicate file_ids are not allowed."
            )
        seen.add(file_id)
        normalized.append(file_id)
    return tuple(normalized)


def normalize_enhanced_request(
    *, file_ids, recipient, cc, subject, body, mode
) -> tuple[tuple[str, ...], str, tuple[str, ...], str, str, str]:
    normalized_ids = normalize_file_ids(file_ids)
    normalized_recipient = validate_recipient(recipient)
    normalized_cc = normalize_cc(normalized_recipient, cc)
    normalized_subject, normalized_body = validate_message_fields(subject, body)
    if not isinstance(mode, str) or mode not in ALLOWED_MODES:
        raise EmailServiceError(
            "INVALID_MODE", "mode must be auto, attachment, or link."
        )
    return (
        normalized_ids,
        normalized_recipient,
        normalized_cc,
        normalized_subject,
        normalized_body,
        mode,
    )


def _ensure_indexed(file_ids: tuple[str, ...], database_path: Path) -> None:
    try:
        connection = connect_database(Path(database_path), read_only=True)
    except sqlite3.Error as error:
        raise EmailServiceError(
            "INDEX_UNAVAILABLE", "The Drive index is not available."
        ) from error

    try:
        for file_id in file_ids:
            row = connection.execute(
                "SELECT file_id FROM files WHERE file_id = ?", (file_id,)
            ).fetchone()
            if row is not None:
                continue
            folder = connection.execute(
                "SELECT folder_id FROM folders WHERE folder_id = ?", (file_id,)
            ).fetchone()
            if folder is not None:
                raise EmailServiceError(
                    "UNSUPPORTED_FOLDER", "Drive folders cannot be sent."
                )
            raise EmailServiceError(
                "FILE_NOT_INDEXED", f"The exact file_id {file_id!r} is not indexed."
            )
    except sqlite3.Error as error:
        raise EmailServiceError(
            "INDEX_UNAVAILABLE", "The Drive index is not available."
        ) from error
    finally:
        connection.close()


def _optional_nonnegative_int(value) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _load_drive_files(
    drive_service, file_ids: tuple[str, ...]
) -> tuple[EnhancedFile, ...]:
    files: list[EnhancedFile] = []
    for file_id in file_ids:
        try:
            metadata = get_file_metadata(drive_service, file_id)
        except HttpError as error:
            if getattr(error.resp, "status", None) == 404:
                raise EmailServiceError(
                    "DRIVE_FILE_NOT_FOUND",
                    f"Drive no longer contains file_id {file_id!r}.",
                ) from None
            raise EmailServiceError(
                "DOWNLOAD_FAILED", "Drive metadata validation failed."
            ) from None
        except (OSError, RuntimeError) as error:
            raise EmailServiceError(
                "DOWNLOAD_FAILED", "Drive metadata validation failed."
            ) from error

        if metadata.get("id") != file_id:
            raise EmailServiceError(
                "DRIVE_FILE_NOT_FOUND", "Drive returned a different file identity."
            )
        if metadata.get("trashed") is True:
            raise EmailServiceError("FILE_TRASHED", "Trashed files cannot be sent.")

        mime_type = metadata.get("mimeType")
        if mime_type == FOLDER_MIME_TYPE:
            raise EmailServiceError(
                "UNSUPPORTED_FOLDER", "Drive folders cannot be sent."
            )
        if mime_type == SHORTCUT_MIME_TYPE:
            raise EmailServiceError(
                "UNSUPPORTED_SHORTCUT", "Drive shortcuts cannot be sent."
            )
        if not isinstance(mime_type, str) or "/" not in mime_type:
            mime_type = "application/octet-stream"

        name = metadata.get("name")
        if (
            not isinstance(name, str)
            or not name
            or any(character in name for character in "\r\n")
        ):
            raise EmailServiceError(
                "DOWNLOAD_FAILED", "Drive returned an unsafe file name."
            )

        capabilities = metadata.get("capabilities") or {}
        web_view_link = metadata.get("webViewLink")
        if not isinstance(web_view_link, str) or not web_view_link:
            web_view_link = None
        files.append(
            EnhancedFile(
                file_id=file_id,
                name=name,
                mime_type=mime_type,
                size_bytes=_optional_nonnegative_int(metadata.get("size")),
                modified_time=(
                    metadata.get("modifiedTime")
                    if isinstance(metadata.get("modifiedTime"), str)
                    else None
                ),
                version=(
                    str(metadata.get("version"))
                    if metadata.get("version") is not None
                    else None
                ),
                can_download=capabilities.get("canDownload") is True,
                can_share=capabilities.get("canShare") is True,
                web_view_link=web_view_link,
                is_native=mime_type.startswith(GOOGLE_NATIVE_PREFIX),
            )
        )
    return tuple(files)


def _estimated_gmail_raw_bytes(
    files: tuple[EnhancedFile, ...], subject: str, body: str, cc: tuple[str, ...]
) -> int:
    mime_bytes = len(subject.encode("utf-8")) + len(body.encode("utf-8")) + 8192
    mime_bytes += sum(len(address.encode("utf-8")) for address in cc)
    for file in files:
        if file.size_bytes is None:
            return MAX_GMAIL_RAW_BYTES + 1
        encoded = ((file.size_bytes + 2) // 3) * 4
        line_breaks = ((encoded + 75) // 76) * 2
        mime_bytes += encoded + line_breaks + len(file.name.encode("utf-8")) + 4096
    return ((mime_bytes + 2) // 3) * 4


def _attachment_error_code(
    files: tuple[EnhancedFile, ...], subject: str, body: str, cc: tuple[str, ...]
) -> str | None:
    if any(file.is_native or not file.can_download for file in files):
        return "ATTACHMENT_MODE_UNSUPPORTED"
    if any(file.size_bytes is None for file in files):
        return "ATTACHMENT_MODE_UNSUPPORTED"
    sizes = [file.size_bytes or 0 for file in files]
    if any(size > MAX_ATTACHMENT_BYTES for size in sizes):
        return "TOTAL_ATTACHMENT_TOO_LARGE"
    if sum(sizes) > MAX_ATTACHMENT_BYTES:
        return "TOTAL_ATTACHMENT_TOO_LARGE"
    if _estimated_gmail_raw_bytes(files, subject, body, cc) > MAX_GMAIL_RAW_BYTES:
        return "RAW_MESSAGE_TOO_LARGE"
    return None


def _sharing_plan(
    drive_service,
    files: tuple[EnhancedFile, ...],
) -> tuple[SharingChange, ...]:
    changes: list[SharingChange] = []
    for file in files:
        if not file.web_view_link:
            raise EmailServiceError(
                "LINK_UNAVAILABLE", f"Drive returned no webViewLink for {file.name!r}."
            )
        try:
            permissions = list_permissions(drive_service, file.file_id)
        except DriveSharingError as error:
            raise EmailServiceError(
                "SHARING_FAILED", "Drive permission preview failed."
            ) from error

        anyone_permissions = [
            permission
            for permission in permissions
            if permission.get("type") == "anyone"
        ]
        too_broad = any(
            permission.get("role") != "reader"
            or permission.get("allowFileDiscovery") is True
            for permission in anyone_permissions
        )
        if too_broad:
            raise EmailServiceError(
                "LINK_PERMISSION_TOO_BROAD",
                f"{file.name!r} already has a broader anyone permission.",
            )
        if anyone_permissions:
            changes.append(SharingChange(file.file_id, "existing"))
            continue
        if not file.can_share:
            raise EmailServiceError(
                "SHARING_FAILED",
                f"The authenticated user cannot share {file.name!r}.",
            )
        changes.append(SharingChange(file.file_id, "create_anyone_reader"))
    return tuple(changes)


def _refresh_post_share_links(
    drive_service,
    files: tuple[EnhancedFile, ...],
) -> tuple[EnhancedFile, ...]:
    """Reload Drive-returned webViewLink values after sharing is finalized."""
    refreshed: list[EnhancedFile] = []
    for file in files:
        try:
            metadata = get_file_metadata(drive_service, file.file_id)
        except (HttpError, OSError, RuntimeError) as error:
            raise EmailServiceError(
                "LINK_UNAVAILABLE",
                "Drive link metadata could not be refreshed after sharing.",
            ) from error
        if metadata.get("id") != file.file_id or metadata.get("trashed") is True:
            raise EmailServiceError(
                "LINK_UNAVAILABLE",
                "Drive returned an invalid file while refreshing a shared link.",
            )
        web_view_link = metadata.get("webViewLink")
        if not isinstance(web_view_link, str) or not web_view_link:
            raise EmailServiceError(
                "LINK_UNAVAILABLE",
                f"Drive returned no post-share webViewLink for {file.name!r}.",
            )
        refreshed.append(replace(file, web_view_link=web_view_link))
    return tuple(refreshed)


def _metadata_signatures(files: tuple[EnhancedFile, ...]) -> list[dict]:
    return [
        {
            "file_id": file.file_id,
            "name": file.name,
            "mime_type": file.mime_type,
            "size_bytes": file.size_bytes,
            "modified_time": file.modified_time,
            "version": file.version,
            "can_download": file.can_download,
            "can_share": file.can_share,
            "web_view_link": file.web_view_link,
        }
        for file in files
    ]


class EnhancedEmailStore:
    def __init__(self, path: Path = ENHANCED_STATE_DATABASE_PATH) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS enhanced_email_previews (
                    preview_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    plan_hash TEXT NOT NULL,
                    metadata_signature_hash TEXT NOT NULL,
                    sharing_plan_hash TEXT NOT NULL,
                    recipient_canonical TEXT NOT NULL,
                    cc_canonical_json TEXT NOT NULL,
                    file_ids_json TEXT NOT NULL,
                    requested_mode TEXT NOT NULL,
                    resolved_mode TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS enhanced_email_sends (
                    idempotency_key TEXT PRIMARY KEY,
                    request_fingerprint TEXT NOT NULL,
                    preview_id TEXT NOT NULL,
                    delivery_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message_id TEXT,
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS enhanced_link_permission_events (
                    idempotency_key TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    permission_id TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (idempotency_key, file_id)
                );
                """
            )
            connection.commit()
        finally:
            connection.close()

    def save_preview(self, preview: EnhancedPreview) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                INSERT INTO enhanced_email_previews (
                    preview_id, status, expires_at, plan_hash,
                    metadata_signature_hash, sharing_plan_hash,
                    recipient_canonical, cc_canonical_json, file_ids_json,
                    requested_mode, resolved_mode, created_at
                ) VALUES (?, 'PREVIEWED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preview.preview_id,
                    preview.expires_at,
                    preview.plan_hash,
                    preview.metadata_signature_hash,
                    preview.sharing_plan_hash,
                    canonical_address(preview.recipient),
                    canonical_json(
                        sorted(canonical_address(value) for value in preview.cc)
                    ),
                    canonical_json([file.file_id for file in preview.files]),
                    preview.requested_mode,
                    preview.delivery_mode,
                    utc_text(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def load_preview(
        self, preview_id: str, *, require_unexpired: bool = True
    ) -> sqlite3.Row:
        row = self.find_preview(preview_id)
        if row is None:
            raise EmailServiceError(
                "PREVIEW_NOT_FOUND", "The email preview does not exist."
            )
        expires_at = datetime.fromisoformat(row["expires_at"])
        if require_unexpired and expires_at <= utc_now():
            raise EmailServiceError("PREVIEW_EXPIRED", "The email preview expired.")
        return row

    def find_preview(self, preview_id: str) -> sqlite3.Row | None:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(
                "SELECT * FROM enhanced_email_previews WHERE preview_id = ?",
                (preview_id,),
            ).fetchone()
        finally:
            connection.close()

    def find_send(self, idempotency_key: str) -> sqlite3.Row | None:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(
                "SELECT * FROM enhanced_email_sends WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        finally:
            connection.close()

    def begin_send(
        self,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        preview_id: str,
        delivery_mode: str,
        status: str,
    ) -> None:
        now = utc_text()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                INSERT INTO enhanced_email_sends (
                    idempotency_key, request_fingerprint, preview_id,
                    delivery_mode, status, message_id, result_json,
                    error_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    idempotency_key,
                    request_fingerprint,
                    preview_id,
                    delivery_mode,
                    status,
                    now,
                    now,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def update_send(
        self,
        idempotency_key: str,
        status: str,
        *,
        error_code: str | None = None,
        message_id: str | None = None,
        result_json: str | None = None,
    ) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                UPDATE enhanced_email_sends
                SET status = ?, error_code = ?, message_id = ?, result_json = ?,
                    updated_at = ?
                WHERE idempotency_key = ?
                """,
                (
                    status,
                    error_code,
                    message_id,
                    result_json,
                    utc_text(),
                    idempotency_key,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def record_link_permission(
        self,
        *,
        idempotency_key: str,
        file_id: str,
        status: str,
        permission_id: str | None = None,
    ) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                INSERT INTO enhanced_link_permission_events (
                    idempotency_key, file_id, status,
                    permission_id, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key, file_id) DO UPDATE SET
                    status = excluded.status,
                    permission_id = excluded.permission_id,
                    updated_at = excluded.updated_at
                """,
                (
                    idempotency_key,
                    file_id,
                    status,
                    permission_id,
                    utc_text(),
                ),
            )
            connection.commit()
        finally:
            connection.close()


def create_enhanced_preview(
    *,
    drive_service,
    file_ids,
    recipient,
    cc,
    subject,
    body,
    mode,
    database_path: Path = DATABASE_PATH,
    state_database_path: Path = ENHANCED_STATE_DATABASE_PATH,
    diagnostic_log_path: Path = ENHANCED_DIAGNOSTIC_LOG_PATH,
    persist: bool = True,
    preview_id: str | None = None,
    expires_at: str | None = None,
) -> EnhancedPreview:
    (
        normalized_ids,
        normalized_recipient,
        normalized_cc,
        normalized_subject,
        normalized_body,
        requested_mode,
    ) = normalize_enhanced_request(
        file_ids=file_ids,
        recipient=recipient,
        cc=cc,
        subject=subject,
        body=body,
        mode=mode,
    )
    _ensure_indexed(normalized_ids, Path(database_path))
    files = _load_drive_files(drive_service, normalized_ids)
    attachment_error = _attachment_error_code(
        files, normalized_subject, normalized_body, normalized_cc
    )

    if requested_mode == "attachment":
        if attachment_error:
            raise EmailServiceError(
                attachment_error, "The requested attachment mode is not safe."
            )
        delivery_mode = "attachment"
    elif requested_mode == "auto":
        delivery_mode = "attachment" if not attachment_error else "link"
    else:
        delivery_mode = "link"

    if delivery_mode == "link":
        sharing_mode = SHARING_MODE_ANYONE_WITH_LINK_READER
        sharing_changes = _sharing_plan(drive_service, files)
    else:
        sharing_mode = SHARING_MODE_NONE
        sharing_changes = ()

    metadata_signatures = _metadata_signatures(files)
    metadata_signature_hash = sha256_json(metadata_signatures)
    sharing_values = [asdict(change) for change in sharing_changes]
    sharing_plan_hash = sha256_json(sharing_values)
    plan_hash = sha256_json(
        {
            "to": canonical_address(normalized_recipient),
            "cc": sorted(canonical_address(value) for value in normalized_cc),
            "subject": normalized_subject,
            "body": normalized_body,
            "file_ids": list(normalized_ids),
            "requested_mode": requested_mode,
            "resolved_mode": delivery_mode,
            "sharing_mode": sharing_mode,
            "metadata": metadata_signatures,
            "sharing_plan": sharing_values,
        }
    )
    preview = EnhancedPreview(
        preview_id=preview_id or secrets.token_urlsafe(24),
        expires_at=expires_at
        or utc_text(utc_now() + timedelta(seconds=PREVIEW_TTL_SECONDS)),
        requested_mode=requested_mode,
        delivery_mode=delivery_mode,
        sharing_mode=sharing_mode,
        file_count=len(files),
        total_size_bytes=(
            sum(file.size_bytes or 0 for file in files)
            if all(file.size_bytes is not None for file in files)
            else None
        ),
        files=files,
        recipient=normalized_recipient,
        cc=normalized_cc,
        sharing_changes=sharing_changes,
        plan_hash=plan_hash,
        metadata_signature_hash=metadata_signature_hash,
        sharing_plan_hash=sharing_plan_hash,
    )
    if persist:
        store = EnhancedEmailStore(state_database_path)
        store.save_preview(preview)
        _diagnostic(
            diagnostic_log_path,
            event="preview_persisted",
            preview_id=preview.preview_id,
            state_database_path=store.path,
            preview_exists=True,
            preview_expired=False,
            preview_id_matches_stored=True,
            expires_at=preview.expires_at,
        )
    return preview


def build_attachment_message(
    preview: EnhancedPreview, subject: str, body: str, attachments: tuple[bytes, ...]
) -> bytes:
    if len(attachments) != len(preview.files):
        raise ValueError("Every approved file must have one attachment payload.")
    message = EmailMessage()
    message["To"] = preview.recipient
    if preview.cc:
        message["Cc"] = ", ".join(preview.cc)
    message["Subject"] = subject
    message.set_content(body)
    for file, content in zip(preview.files, attachments):
        media_type = file.mime_type.split(";", 1)[0].strip()
        try:
            main_type, sub_type = media_type.split("/", 1)
        except ValueError:
            main_type, sub_type = "application", "octet-stream"
        if not main_type or not sub_type:
            main_type, sub_type = "application", "octet-stream"
        message.add_attachment(
            content,
            maintype=main_type,
            subtype=sub_type,
            filename=file.name,
        )
    return message.as_bytes()


def build_link_message(preview: EnhancedPreview, subject: str, body: str) -> bytes:
    lines = [body.rstrip(), "", "첨부 파일 링크:"]
    for index, file in enumerate(preview.files, start=1):
        lines.extend((f"{index}. {file.name}", f"   {file.web_view_link}"))
    message = EmailMessage()
    message["To"] = preview.recipient
    if preview.cc:
        message["Cc"] = ", ".join(preview.cc)
    message["Subject"] = subject
    message.set_content("\n".join(lines).rstrip() + "\n")
    return message.as_bytes()


def _result_from_json(value: str, *, replay: bool) -> EnhancedSendResult:
    data = json.loads(value)
    return EnhancedSendResult(
        status=data["status"],
        delivery_mode=data["delivery_mode"],
        sharing_mode=data["sharing_mode"],
        file_count=data["file_count"],
        files=tuple(data["files"]),
        recipient=data["recipient"],
        cc=tuple(data["cc"]),
        message_id=data["message_id"],
        sharing_changes=tuple(data["sharing_changes"]),
        idempotent_replay=replay,
    )


def _audit(
    path: Path,
    *,
    status: str,
    preview: EnhancedPreview,
    error_code: str | None = None,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": utc_text(),
        "status": status,
        "delivery_mode": preview.delivery_mode,
        "file_ids": [file.file_id for file in preview.files],
        "recipient": mask_recipient(preview.recipient),
        "cc": [mask_recipient(value) for value in preview.cc],
        "error_code": error_code,
    }
    with path.open("a", encoding="utf-8") as log_file:
        log_file.write(canonical_json(event) + "\n")


def send_enhanced_email(
    *,
    preview_id,
    file_ids,
    recipient,
    cc,
    subject,
    body,
    mode,
    idempotency_key,
    drive_service_factory,
    gmail_service_factory,
    share_service_factory,
    database_path: Path = DATABASE_PATH,
    state_database_path: Path = ENHANCED_STATE_DATABASE_PATH,
    audit_log_path: Path = ENHANCED_AUDIT_LOG_PATH,
    diagnostic_log_path: Path = ENHANCED_DIAGNOSTIC_LOG_PATH,
) -> EnhancedSendResult:
    if not isinstance(preview_id, str) or not preview_id:
        raise EmailServiceError("PREVIEW_NOT_FOUND", "preview_id is required.")
    idempotency_key = validate_idempotency_key(idempotency_key)
    (
        normalized_ids,
        normalized_recipient,
        normalized_cc,
        normalized_subject,
        normalized_body,
        normalized_mode,
    ) = normalize_enhanced_request(
        file_ids=file_ids,
        recipient=recipient,
        cc=cc,
        subject=subject,
        body=body,
        mode=mode,
    )
    store = EnhancedEmailStore(state_database_path)
    _diagnostic(
        diagnostic_log_path,
        event="send_received",
        preview_id=preview_id,
        state_database_path=store.path,
    )
    preview_row = store.find_preview(preview_id)
    if preview_row is None:
        _diagnostic(
            diagnostic_log_path,
            event="preview_lookup",
            preview_id=preview_id,
            state_database_path=store.path,
            preview_exists=False,
            preview_id_matches_stored=False,
        )
        raise EmailServiceError(
            "PREVIEW_NOT_FOUND", "The email preview does not exist."
        )
    expires_at = datetime.fromisoformat(preview_row["expires_at"])
    preview_expired = expires_at <= utc_now()
    _diagnostic(
        diagnostic_log_path,
        event="preview_lookup",
        preview_id=preview_id,
        state_database_path=store.path,
        preview_exists=True,
        preview_expired=preview_expired,
        preview_id_matches_stored=preview_row["preview_id"] == preview_id,
        expires_at=preview_row["expires_at"],
    )
    request_fingerprint = sha256_json(
        {
            "preview_id": preview_id,
            "file_ids": list(normalized_ids),
            "to": canonical_address(normalized_recipient),
            "cc": sorted(canonical_address(value) for value in normalized_cc),
            "subject": normalized_subject,
            "body": normalized_body,
            "requested_mode": normalized_mode,
            "resolved_mode": preview_row["resolved_mode"],
            "preview_plan_hash": preview_row["plan_hash"],
            "metadata_signature_hash": preview_row["metadata_signature_hash"],
            "sharing_plan_hash": preview_row["sharing_plan_hash"],
        }
    )
    existing = store.find_send(idempotency_key)
    if existing is not None:
        if existing["request_fingerprint"] != request_fingerprint:
            raise EmailServiceError(
                "IDEMPOTENCY_CONFLICT",
                "The idempotency key was already used for different content.",
            )
        if existing["status"] == "SENT" and existing["result_json"]:
            return _result_from_json(existing["result_json"], replay=True)
        if existing["status"] == "SHARING_PARTIAL":
            raise EmailServiceError(
                "SHARING_PARTIAL", "A prior permission operation partially failed."
            )
        if existing["status"] == "DELIVERY_UNCERTAIN":
            raise EmailServiceError(
                "GMAIL_DELIVERY_UNCERTAIN",
                "Delivery is uncertain; do not retry automatically.",
            )
        if existing["status"] in {"SHARING_PENDING", "SEND_PENDING"}:
            raise EmailServiceError(
                "IDEMPOTENCY_IN_PROGRESS", "A prior operation may still be in progress."
            )
        raise EmailServiceError(
            "IDEMPOTENCY_PREVIOUSLY_FAILED",
            "A previous definite failure used this idempotency key.",
        )

    if preview_expired:
        raise EmailServiceError("PREVIEW_EXPIRED", "The email preview expired.")
    try:
        drive_service = drive_service_factory()
    except DriveDownloadAuthenticationError as error:
        raise EmailServiceError(
            "DRIVE_AUTH_FAILED", "Drive download authentication is unavailable."
        ) from error
    current_preview = create_enhanced_preview(
        drive_service=drive_service,
        file_ids=list(normalized_ids),
        recipient=normalized_recipient,
        cc=list(normalized_cc),
        subject=normalized_subject,
        body=normalized_body,
        mode=normalized_mode,
        database_path=database_path,
        state_database_path=state_database_path,
        diagnostic_log_path=diagnostic_log_path,
        persist=False,
        preview_id=preview_id,
        expires_at=preview_row["expires_at"],
    )
    if current_preview.plan_hash != preview_row["plan_hash"]:
        raise EmailServiceError(
            "PREVIEW_STALE", "The preview plan changed and must be approved again."
        )

    initial_status = (
        "SHARING_PENDING"
        if current_preview.delivery_mode == "link"
        else "SEND_PENDING"
    )
    store.begin_send(
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        preview_id=preview_id,
        delivery_mode=current_preview.delivery_mode,
        status=initial_status,
    )

    try:
        gmail_service = gmail_service_factory()
    except GmailAuthenticationError as error:
        store.update_send(
            idempotency_key, "EMAIL_FAILED", error_code="GMAIL_AUTH_FAILED"
        )
        _audit(
            audit_log_path,
            status="email_failed",
            preview=current_preview,
            error_code="GMAIL_AUTH_FAILED",
        )
        raise EmailServiceError(
            "GMAIL_AUTH_FAILED", "Gmail send authentication is unavailable."
        ) from error

    result_changes: list[dict] = []
    if current_preview.delivery_mode == "link":
        create_actions = [
            change
            for change in current_preview.sharing_changes
            if change.action == "create_anyone_reader"
        ]
        share_service = None
        if create_actions:
            try:
                share_service = share_service_factory()
            except DriveShareAuthenticationError as error:
                store.update_send(
                    idempotency_key,
                    "SHARING_FAILED",
                    error_code="DRIVE_SHARE_AUTH_FAILED",
                )
                _audit(
                    audit_log_path,
                    status="sharing_failed",
                    preview=current_preview,
                    error_code="DRIVE_SHARE_AUTH_FAILED",
                )
                raise EmailServiceError(
                    "DRIVE_SHARE_AUTH_FAILED",
                    "Drive sharing OAuth approval is required.",
                ) from error

        created_count = 0
        for change in current_preview.sharing_changes:
            if change.action == "existing":
                store.record_link_permission(
                    idempotency_key=idempotency_key,
                    file_id=change.file_id,
                    status="EXISTING",
                )
                result_changes.append(asdict(change))
                continue
            try:
                permission_id = create_anyone_reader_permission(
                    share_service, change.file_id
                )
            except DriveSharingError as error:
                failure_status = (
                    "SHARING_PARTIAL" if created_count else "SHARING_FAILED"
                )
                store.record_link_permission(
                    idempotency_key=idempotency_key,
                    file_id=change.file_id,
                    status="FAILED",
                )
                store.update_send(
                    idempotency_key,
                    failure_status,
                    error_code=failure_status,
                )
                _audit(
                    audit_log_path,
                    status=failure_status.casefold(),
                    preview=current_preview,
                    error_code=failure_status,
                )
                raise EmailServiceError(
                    failure_status,
                    "Drive sharing failed; Gmail was not called.",
                ) from error
            created_count += 1
            store.record_link_permission(
                idempotency_key=idempotency_key,
                file_id=change.file_id,
                status="CREATED",
                permission_id=permission_id,
            )
            result_changes.append(
                {
                    "file_id": change.file_id,
                    "action": "created",
                    "permission_type": "anyone",
                    "role": "reader",
                    "allow_file_discovery": False,
                }
            )
        try:
            post_share_files = _refresh_post_share_links(
                drive_service, current_preview.files
            )
        except EmailServiceError as error:
            failure_status = "SHARING_PARTIAL" if created_count else "SHARING_FAILED"
            store.update_send(
                idempotency_key,
                failure_status,
                error_code="LINK_UNAVAILABLE",
            )
            _audit(
                audit_log_path,
                status=failure_status.casefold(),
                preview=current_preview,
                error_code="LINK_UNAVAILABLE",
            )
            raise EmailServiceError(
                failure_status,
                "Post-share Drive links could not be loaded; Gmail was not called.",
            ) from error
        current_preview = replace(current_preview, files=post_share_files)
        store.update_send(idempotency_key, "SHARING_COMPLETE")
        message_bytes = build_link_message(
            current_preview, normalized_subject, normalized_body
        )
    else:
        attachments: list[bytes] = []
        downloaded_total = 0
        for file in current_preview.files:
            remaining = MAX_ATTACHMENT_BYTES - downloaded_total
            try:
                content = download_file(drive_service, file.file_id, remaining)
            except DownloadSizeLimitExceeded:
                store.update_send(
                    idempotency_key,
                    "EMAIL_FAILED",
                    error_code="TOTAL_ATTACHMENT_TOO_LARGE",
                )
                raise EmailServiceError(
                    "TOTAL_ATTACHMENT_TOO_LARGE",
                    "Downloaded attachments exceed 18 MiB.",
                ) from None
            except HttpError as error:
                code = (
                    "DRIVE_FILE_NOT_FOUND"
                    if getattr(error.resp, "status", None) == 404
                    else "DOWNLOAD_FAILED"
                )
                store.update_send(idempotency_key, "EMAIL_FAILED", error_code=code)
                raise EmailServiceError(code, "Drive attachment download failed.") from None
            except (OSError, RuntimeError) as error:
                store.update_send(
                    idempotency_key, "EMAIL_FAILED", error_code="DOWNLOAD_FAILED"
                )
                raise EmailServiceError(
                    "DOWNLOAD_FAILED", "Drive attachment download failed."
                ) from error
            if file.size_bytes is None or len(content) != file.size_bytes:
                store.update_send(
                    idempotency_key, "EMAIL_FAILED", error_code="PREVIEW_STALE"
                )
                raise EmailServiceError(
                    "PREVIEW_STALE", "A file size changed after preview."
                )
            downloaded_total += len(content)
            if downloaded_total > MAX_ATTACHMENT_BYTES:
                store.update_send(
                    idempotency_key,
                    "EMAIL_FAILED",
                    error_code="TOTAL_ATTACHMENT_TOO_LARGE",
                )
                raise EmailServiceError(
                    "TOTAL_ATTACHMENT_TOO_LARGE",
                    "Downloaded attachments exceed 18 MiB.",
                )
            attachments.append(content)
        message_bytes = build_attachment_message(
            current_preview,
            normalized_subject,
            normalized_body,
            tuple(attachments),
        )

    encoded_size = len(base64.urlsafe_b64encode(message_bytes))
    if encoded_size > MAX_GMAIL_RAW_BYTES:
        store.update_send(
            idempotency_key, "EMAIL_FAILED", error_code="RAW_MESSAGE_TOO_LARGE"
        )
        raise EmailServiceError(
            "RAW_MESSAGE_TOO_LARGE", "The Gmail raw message exceeds 34 MiB."
        )

    if current_preview.delivery_mode == "link":
        store.update_send(idempotency_key, "SEND_PENDING")
    try:
        message_id = send_message(gmail_service, message_bytes)
    except GmailApiNotEnabledError:
        state = (
            "SHARING_COMPLETE_EMAIL_FAILED"
            if current_preview.delivery_mode == "link"
            else "EMAIL_FAILED"
        )
        store.update_send(
            idempotency_key, state, error_code="GMAIL_API_NOT_ENABLED"
        )
        raise EmailServiceError(
            "GMAIL_API_NOT_ENABLED", "Gmail API is not enabled."
        ) from None
    except GmailSendError:
        state = (
            "SHARING_COMPLETE_EMAIL_FAILED"
            if current_preview.delivery_mode == "link"
            else "EMAIL_FAILED"
        )
        store.update_send(idempotency_key, state, error_code="GMAIL_SEND_FAILED")
        _audit(
            audit_log_path,
            status=state.casefold(),
            preview=current_preview,
            error_code="GMAIL_SEND_FAILED",
        )
        raise EmailServiceError(
            "GMAIL_SEND_FAILED", "Gmail rejected the send request."
        ) from None
    except GmailDeliveryUncertainError:
        store.update_send(
            idempotency_key,
            "DELIVERY_UNCERTAIN",
            error_code="GMAIL_DELIVERY_UNCERTAIN",
        )
        _audit(
            audit_log_path,
            status="delivery_uncertain",
            preview=current_preview,
            error_code="GMAIL_DELIVERY_UNCERTAIN",
        )
        raise EmailServiceError(
            "GMAIL_DELIVERY_UNCERTAIN",
            "Delivery is uncertain; do not retry automatically.",
        ) from None

    result = EnhancedSendResult(
        status="sent",
        delivery_mode=current_preview.delivery_mode,
        sharing_mode=current_preview.sharing_mode,
        file_count=current_preview.file_count,
        files=tuple(
            {
                "file_id": file.file_id,
                "name": file.name,
                "delivery": current_preview.delivery_mode,
            }
            for file in current_preview.files
        ),
        recipient=current_preview.recipient,
        cc=current_preview.cc,
        message_id=message_id,
        sharing_changes=tuple(result_changes),
    )
    result_json = canonical_json(asdict(result))
    store.update_send(
        idempotency_key,
        "SENT",
        message_id=message_id,
        result_json=result_json,
    )
    _audit(audit_log_path, status="sent", preview=current_preview)
    return result
