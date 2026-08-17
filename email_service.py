import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from googleapiclient.errors import HttpError

from database import DATABASE_PATH, connect_database
from drive_download_client import (
    DownloadSizeLimitExceeded,
    download_file,
    get_file_metadata,
)
from gmail_client import (
    GmailApiNotEnabledError,
    GmailAuthenticationError,
    GmailDeliveryUncertainError,
    GmailSendError,
    send_message,
)


PROJECT_ROOT = Path(__file__).resolve().parent
MAX_ATTACHMENT_BYTES = 18 * 1024 * 1024
EMAIL_STATE_DATABASE_PATH = PROJECT_ROOT / "data" / "email_send_state.db"
EMAIL_AUDIT_LOG_PATH = PROJECT_ROOT / "logs" / "email_send.log"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
SHORTCUT_MIME_TYPE = "application/vnd.google-apps.shortcut"
GOOGLE_NATIVE_PREFIX = "application/vnd.google-apps."
RECIPIENT_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class EmailServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PreparedEmailFile:
    file_id: str
    recipient: str
    subject: str
    body: str
    file_name: str
    mime_type: str
    size_bytes: int | None
    idempotency_key: str
    payload_hash: str


@dataclass(frozen=True)
class EmailSendResult:
    status: str
    message_id: str
    file_id: str
    file_name: str
    recipient: str
    attachment_size: int
    idempotent_replay: bool = False


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def mask_recipient(recipient: str) -> str:
    local, domain = recipient.rsplit("@", 1)
    masked_local = local[0] + "***" if local else "***"
    domain_parts = domain.split(".")
    domain_name = domain_parts[0]
    masked_domain = (domain_name[0] + "***") if domain_name else "***"
    suffix = "." + ".".join(domain_parts[1:]) if len(domain_parts) > 1 else ""
    return f"{masked_local}@{masked_domain}{suffix}"


def validate_recipient(value: str) -> str:
    if not isinstance(value, str):
        raise EmailServiceError("INVALID_RECIPIENT", "Recipient must be text.")
    recipient = value.strip()
    if (
        not recipient
        or any(character in recipient for character in "\r\n,;")
        or not RECIPIENT_PATTERN.fullmatch(recipient)
    ):
        raise EmailServiceError(
            "INVALID_RECIPIENT",
            "Provide exactly one valid recipient without header characters.",
        )
    return recipient


def validate_message_fields(subject: str, body: str) -> tuple[str, str]:
    if not isinstance(subject, str) or not subject.strip():
        raise EmailServiceError("INVALID_SUBJECT", "Subject must not be empty.")
    if any(character in subject for character in "\r\n"):
        raise EmailServiceError(
            "INVALID_SUBJECT", "Subject must not contain line breaks."
        )
    if len(subject) > 998:
        raise EmailServiceError("INVALID_SUBJECT", "Subject is too long.")
    if not isinstance(body, str):
        raise EmailServiceError("INVALID_BODY", "Body must be plain text.")
    return subject.strip(), body


def validate_idempotency_key(value: str) -> str:
    if not isinstance(value, str) or not IDEMPOTENCY_KEY_PATTERN.fullmatch(value):
        raise EmailServiceError(
            "INVALID_IDEMPOTENCY_KEY",
            "Use 8-128 letters, numbers, dots, underscores, colons, or hyphens.",
        )
    return value


def _load_indexed_file(file_id: str, database_path: Path) -> sqlite3.Row:
    try:
        connection = connect_database(database_path, read_only=True)
    except sqlite3.Error as error:
        raise EmailServiceError(
            "INDEX_UNAVAILABLE", "The Drive index is not available."
        ) from error

    try:
        try:
            row = connection.execute(
                """
                SELECT file_id, name, mime_type, size_bytes, trashed
                FROM files
                WHERE file_id = ?
                """,
                (file_id,),
            ).fetchone()
            if row is not None:
                return row

            folder = connection.execute(
                "SELECT folder_id FROM folders WHERE folder_id = ?",
                (file_id,),
            ).fetchone()
            if folder is not None:
                raise EmailServiceError(
                    "UNSUPPORTED_FOLDER", "Drive folders cannot be attached."
                )
            raise EmailServiceError(
                "FILE_NOT_INDEXED", "The exact file_id is absent from the index."
            )
        except EmailServiceError:
            raise
        except sqlite3.Error as error:
            raise EmailServiceError(
                "INDEX_UNAVAILABLE", "The Drive index could not be read."
            ) from error
    finally:
        connection.close()


def _drive_size(metadata: dict) -> int | None:
    value = metadata.get("size")
    if value is None:
        return None
    try:
        size = int(value)
    except (TypeError, ValueError):
        return None
    return size if size >= 0 else None


def _payload_hash(
    file_id: str, recipient: str, subject: str, body: str
) -> str:
    payload = json.dumps(
        {
            "file_id": file_id,
            "recipient": recipient,
            "subject": subject,
            "body": body,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def prepare_email_file(
    *,
    drive_service,
    file_id: str,
    recipient: str,
    subject: str,
    body: str,
    idempotency_key: str,
    database_path: Path = DATABASE_PATH,
) -> PreparedEmailFile:
    """Validate an exact indexed Drive identity without downloading or sending."""
    if not isinstance(file_id, str) or not file_id.strip():
        raise EmailServiceError("FILE_NOT_INDEXED", "file_id must not be empty.")
    file_id = file_id.strip()
    if any(character in file_id for character in "\r\n"):
        raise EmailServiceError("FILE_NOT_INDEXED", "file_id is invalid.")

    recipient = validate_recipient(recipient)
    subject, body = validate_message_fields(subject, body)
    idempotency_key = validate_idempotency_key(idempotency_key)
    _load_indexed_file(file_id, Path(database_path))

    try:
        metadata = get_file_metadata(drive_service, file_id)
    except HttpError as error:
        if getattr(error.resp, "status", None) == 404:
            raise EmailServiceError(
                "DRIVE_FILE_NOT_FOUND", "Drive no longer contains this file_id."
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
            "UNSUPPORTED_FOLDER", "Drive folders cannot be attached."
        )
    if mime_type == SHORTCUT_MIME_TYPE:
        raise EmailServiceError(
            "UNSUPPORTED_SHORTCUT", "Drive shortcuts cannot be attached."
        )
    if isinstance(mime_type, str) and mime_type.startswith(GOOGLE_NATIVE_PREFIX):
        raise EmailServiceError(
            "UNSUPPORTED_NATIVE_FILE",
            "Google Workspace native files require export and are unsupported.",
        )
    if not isinstance(mime_type, str) or "/" not in mime_type:
        mime_type = "application/octet-stream"

    file_name = metadata.get("name")
    if not isinstance(file_name, str) or not file_name:
        raise EmailServiceError("DOWNLOAD_FAILED", "Drive returned no file name.")
    if any(character in file_name for character in "\r\n"):
        raise EmailServiceError(
            "DOWNLOAD_FAILED", "Attachment file name contains unsafe characters."
        )

    capabilities = metadata.get("capabilities") or {}
    if capabilities.get("canDownload") is not True:
        raise EmailServiceError(
            "DOWNLOAD_FAILED", "The authenticated user cannot download this file."
        )

    size_bytes = _drive_size(metadata)
    if size_bytes is not None and size_bytes > MAX_ATTACHMENT_BYTES:
        raise EmailServiceError(
            "ATTACHMENT_TOO_LARGE",
            f"Attachment exceeds {MAX_ATTACHMENT_BYTES} bytes.",
        )

    return PreparedEmailFile(
        file_id=file_id,
        recipient=recipient,
        subject=subject,
        body=body,
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        idempotency_key=idempotency_key,
        payload_hash=_payload_hash(file_id, recipient, subject, body),
    )


def build_mime_message(prepared: PreparedEmailFile, attachment: bytes) -> bytes:
    message = EmailMessage()
    message["To"] = prepared.recipient
    message["Subject"] = prepared.subject
    message.set_content(prepared.body)

    media_type = prepared.mime_type.split(";", 1)[0].strip()
    try:
        main_type, sub_type = media_type.split("/", 1)
    except ValueError:
        main_type, sub_type = "application", "octet-stream"
    if not main_type or not sub_type:
        main_type, sub_type = "application", "octet-stream"

    message.add_attachment(
        attachment,
        maintype=main_type,
        subtype=sub_type,
        filename=prepared.file_name,
    )
    return message.as_bytes()


class EmailIdempotencyStore:
    def __init__(self, path: Path = EMAIL_STATE_DATABASE_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_send_state (
                    idempotency_key TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message_id TEXT,
                    file_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    attachment_size INTEGER,
                    recipient_masked TEXT NOT NULL,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def begin(self, prepared: PreparedEmailFile) -> tuple[str, str | None]:
        now = utc_timestamp()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT payload_hash, status, message_id
                FROM email_send_state
                WHERE idempotency_key = ?
                """,
                (prepared.idempotency_key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO email_send_state (
                        idempotency_key, payload_hash, status, message_id,
                        file_id, file_name, attachment_size, recipient_masked,
                        error_code, created_at, updated_at
                    ) VALUES (?, ?, 'PENDING', NULL, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        prepared.idempotency_key,
                        prepared.payload_hash,
                        prepared.file_id,
                        prepared.file_name,
                        prepared.size_bytes,
                        mask_recipient(prepared.recipient),
                        now,
                        now,
                    ),
                )
                connection.commit()
                return "NEW", None

            payload_hash, status, message_id = row
            if payload_hash != prepared.payload_hash:
                raise EmailServiceError(
                    "IDEMPOTENCY_CONFLICT",
                    "The idempotency key was already used for different content.",
                )
            if status == "SENT" and message_id:
                connection.commit()
                return "SENT", message_id
            if status == "PENDING":
                raise EmailServiceError(
                    "IDEMPOTENCY_IN_PROGRESS",
                    "A prior send may be in progress or delivery is uncertain.",
                )
            raise EmailServiceError(
                "IDEMPOTENCY_PREVIOUSLY_FAILED",
                "A previous definite failure used this idempotency key.",
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_sent(
        self, idempotency_key: str, message_id: str, attachment_size: int
    ) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                UPDATE email_send_state
                SET status = 'SENT', message_id = ?, attachment_size = ?,
                    error_code = NULL, updated_at = ?
                WHERE idempotency_key = ? AND status = 'PENDING'
                """,
                (message_id, attachment_size, utc_timestamp(), idempotency_key),
            )
            connection.commit()
        finally:
            connection.close()

    def mark_failed(self, idempotency_key: str, error_code: str) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                UPDATE email_send_state
                SET status = 'FAILED', error_code = ?, updated_at = ?
                WHERE idempotency_key = ? AND status = 'PENDING'
                """,
                (error_code, utc_timestamp(), idempotency_key),
            )
            connection.commit()
        finally:
            connection.close()


class EmailAuditLogger:
    def __init__(self, path: Path = EMAIL_AUDIT_LOG_PATH) -> None:
        self.path = Path(path)

    def write(
        self,
        *,
        status: str,
        prepared: PreparedEmailFile,
        attachment_size: int | None,
        message_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "timestamp": utc_timestamp(),
            "status": status,
            "file_id": prepared.file_id,
            "file_name": prepared.file_name,
            "attachment_size": attachment_size,
            "recipient": mask_recipient(prepared.recipient),
            "message_id": message_id,
            "error_code": error_code,
        }
        with self.path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(event, ensure_ascii=False) + "\n")


def send_prepared_email(
    *,
    prepared: PreparedEmailFile,
    drive_service,
    gmail_service=None,
    gmail_service_factory=None,
    state_database_path: Path = EMAIL_STATE_DATABASE_PATH,
    audit_log_path: Path = EMAIL_AUDIT_LOG_PATH,
) -> EmailSendResult:
    store = EmailIdempotencyStore(state_database_path)
    logger = EmailAuditLogger(audit_log_path)
    state, previous_message_id = store.begin(prepared)
    if state == "SENT" and previous_message_id:
        return EmailSendResult(
            status="sent",
            message_id=previous_message_id,
            file_id=prepared.file_id,
            file_name=prepared.file_name,
            recipient=prepared.recipient,
            attachment_size=prepared.size_bytes or 0,
            idempotent_replay=True,
        )

    if gmail_service is None:
        if gmail_service_factory is None:
            store.mark_failed(prepared.idempotency_key, "GMAIL_AUTH_FAILED")
            raise RuntimeError("A Gmail service or service factory is required.")
        try:
            gmail_service = gmail_service_factory()
        except GmailAuthenticationError:
            store.mark_failed(prepared.idempotency_key, "GMAIL_AUTH_FAILED")
            logger.write(
                status="failed",
                prepared=prepared,
                attachment_size=prepared.size_bytes,
                error_code="GMAIL_AUTH_FAILED",
            )
            raise

    try:
        attachment = download_file(
            drive_service,
            prepared.file_id,
            MAX_ATTACHMENT_BYTES,
        )
    except DownloadSizeLimitExceeded:
        store.mark_failed(prepared.idempotency_key, "ATTACHMENT_TOO_LARGE")
        logger.write(
            status="failed",
            prepared=prepared,
            attachment_size=None,
            error_code="ATTACHMENT_TOO_LARGE",
        )
        raise EmailServiceError(
            "ATTACHMENT_TOO_LARGE",
            f"Downloaded attachment exceeds {MAX_ATTACHMENT_BYTES} bytes.",
        ) from None
    except HttpError as error:
        code = (
            "DRIVE_FILE_NOT_FOUND"
            if getattr(error.resp, "status", None) == 404
            else "DOWNLOAD_FAILED"
        )
        store.mark_failed(prepared.idempotency_key, code)
        logger.write(
            status="failed",
            prepared=prepared,
            attachment_size=None,
            error_code=code,
        )
        raise EmailServiceError(code, "Drive attachment download failed.") from None
    except (OSError, RuntimeError) as error:
        store.mark_failed(prepared.idempotency_key, "DOWNLOAD_FAILED")
        logger.write(
            status="failed",
            prepared=prepared,
            attachment_size=None,
            error_code="DOWNLOAD_FAILED",
        )
        raise EmailServiceError(
            "DOWNLOAD_FAILED", "Drive attachment download failed."
        ) from error

    if len(attachment) > MAX_ATTACHMENT_BYTES:
        store.mark_failed(prepared.idempotency_key, "ATTACHMENT_TOO_LARGE")
        raise EmailServiceError(
            "ATTACHMENT_TOO_LARGE",
            f"Downloaded attachment exceeds {MAX_ATTACHMENT_BYTES} bytes.",
        )

    message_bytes = build_mime_message(prepared, attachment)
    try:
        message_id = send_message(gmail_service, message_bytes)
    except GmailApiNotEnabledError:
        store.mark_failed(prepared.idempotency_key, "GMAIL_API_NOT_ENABLED")
        logger.write(
            status="failed",
            prepared=prepared,
            attachment_size=len(attachment),
            error_code="GMAIL_API_NOT_ENABLED",
        )
        raise EmailServiceError(
            "GMAIL_API_NOT_ENABLED",
            "Enable Gmail API in Google Cloud Console before sending.",
        ) from None
    except GmailSendError:
        store.mark_failed(prepared.idempotency_key, "GMAIL_SEND_FAILED")
        logger.write(
            status="failed",
            prepared=prepared,
            attachment_size=len(attachment),
            error_code="GMAIL_SEND_FAILED",
        )
        raise EmailServiceError(
            "GMAIL_SEND_FAILED", "Gmail rejected the send request."
        ) from None
    except GmailDeliveryUncertainError:
        logger.write(
            status="uncertain",
            prepared=prepared,
            attachment_size=len(attachment),
            error_code="GMAIL_DELIVERY_UNCERTAIN",
        )
        raise EmailServiceError(
            "GMAIL_DELIVERY_UNCERTAIN",
            "Delivery is uncertain; do not retry with a new key automatically.",
        ) from None

    store.mark_sent(prepared.idempotency_key, message_id, len(attachment))
    logger.write(
        status="sent",
        prepared=prepared,
        attachment_size=len(attachment),
        message_id=message_id,
    )
    return EmailSendResult(
        status="sent",
        message_id=message_id,
        file_id=prepared.file_id,
        file_name=prepared.file_name,
        recipient=prepared.recipient,
        attachment_size=len(attachment),
    )
