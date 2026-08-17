import base64
import hashlib
import json
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from email_service import (
    EmailServiceError,
    mask_recipient,
    validate_idempotency_key,
    validate_message_fields,
    validate_recipient,
)
from enhanced_email_service import normalize_cc
from gmail_client import (
    GmailApiNotEnabledError,
    GmailAuthenticationError,
    GmailDeliveryUncertainError,
    GmailSendError,
    send_message,
)


PROJECT_ROOT = Path(__file__).resolve().parent
PLAIN_EMAIL_STATE_DATABASE_PATH = PROJECT_ROOT / "data" / "plain_email_state.db"
PLAIN_EMAIL_AUDIT_LOG_PATH = PROJECT_ROOT / "logs" / "plain_email_send.log"
PREVIEW_TTL_SECONDS = 10 * 60
MAX_GMAIL_RAW_BYTES = 34 * 1024 * 1024


@dataclass(frozen=True)
class TextEmailPreview:
    preview_id: str
    expires_at: str
    recipient: str
    cc: tuple[str, ...]
    subject: str
    body: str
    payload_hash: str


@dataclass(frozen=True)
class TextEmailSendResult:
    status: str
    message_id: str
    recipient: str
    cc: tuple[str, ...]
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


def normalize_text_email_request(
    *, recipient, cc, subject, body
) -> tuple[str, tuple[str, ...], str, str]:
    normalized_recipient = validate_recipient(recipient)
    normalized_cc = normalize_cc(normalized_recipient, cc)
    normalized_subject, normalized_body = validate_message_fields(subject, body)
    return normalized_recipient, normalized_cc, normalized_subject, normalized_body


def text_payload_hash(
    *, recipient: str, cc: tuple[str, ...], subject: str, body: str
) -> str:
    return sha256_json(
        {
            "to": canonical_address(recipient),
            "cc": [canonical_address(value) for value in cc],
            "subject": subject,
            "body": body,
        }
    )


class PlainEmailStore:
    def __init__(self, path: Path = PLAIN_EMAIL_STATE_DATABASE_PATH) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS plain_email_previews (
                    preview_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    recipient_canonical TEXT NOT NULL,
                    cc_canonical_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS plain_email_sends (
                    idempotency_key TEXT PRIMARY KEY,
                    request_fingerprint TEXT NOT NULL,
                    preview_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message_id TEXT,
                    result_json TEXT,
                    error_code TEXT,
                    diagnostic_code TEXT,
                    diagnostic_type TEXT,
                    diagnostic_detail TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            existing_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(plain_email_sends)"
                )
            }
            for column_name in (
                "diagnostic_code",
                "diagnostic_type",
                "diagnostic_detail",
            ):
                if column_name not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE plain_email_sends "
                        f"ADD COLUMN {column_name} TEXT"
                    )
            connection.commit()
        finally:
            connection.close()

    def save_preview(self, preview: TextEmailPreview) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                INSERT INTO plain_email_previews (
                    preview_id, status, expires_at, payload_hash,
                    recipient_canonical, cc_canonical_json, created_at
                ) VALUES (?, 'PREVIEWED', ?, ?, ?, ?, ?)
                """,
                (
                    preview.preview_id,
                    preview.expires_at,
                    preview.payload_hash,
                    canonical_address(preview.recipient),
                    canonical_json(
                        [canonical_address(value) for value in preview.cc]
                    ),
                    utc_text(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def find_preview(self, preview_id: str) -> sqlite3.Row | None:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(
                "SELECT * FROM plain_email_previews WHERE preview_id = ?",
                (preview_id,),
            ).fetchone()
        finally:
            connection.close()

    def find_send(self, idempotency_key: str) -> sqlite3.Row | None:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(
                "SELECT * FROM plain_email_sends WHERE idempotency_key = ?",
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
    ) -> None:
        now = utc_text()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                INSERT INTO plain_email_sends (
                    idempotency_key, request_fingerprint, preview_id, status,
                    message_id, result_json, error_code, diagnostic_code,
                    diagnostic_type, diagnostic_detail, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, 'SEND_PENDING', NULL, NULL, NULL,
                    NULL, NULL, NULL, ?, ?
                )
                """,
                (idempotency_key, request_fingerprint, preview_id, now, now),
            )
            connection.commit()
        finally:
            connection.close()

    def update_send(
        self,
        idempotency_key: str,
        status: str,
        *,
        message_id: str | None = None,
        result_json: str | None = None,
        error_code: str | None = None,
        diagnostic_code: str | None = None,
        diagnostic_type: str | None = None,
        diagnostic_detail: str | None = None,
    ) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                UPDATE plain_email_sends
                SET status = ?, message_id = ?, result_json = ?, error_code = ?,
                    diagnostic_code = ?, diagnostic_type = ?,
                    diagnostic_detail = ?,
                    updated_at = ?
                WHERE idempotency_key = ?
                """,
                (
                    status,
                    message_id,
                    result_json,
                    error_code,
                    diagnostic_code,
                    diagnostic_type,
                    diagnostic_detail,
                    utc_text(),
                    idempotency_key,
                ),
            )
            connection.commit()
        finally:
            connection.close()


def _audit(
    path: Path,
    *,
    status: str,
    preview_id: str,
    recipient: str,
    cc_count: int,
    error_code: str | None = None,
    diagnostic_code: str | None = None,
    diagnostic_type: str | None = None,
    diagnostic_detail: str | None = None,
) -> None:
    event = {
        "timestamp": utc_text(),
        "status": status,
        "preview_id": preview_id,
        "recipient": mask_recipient(recipient),
        "cc_count": cc_count,
        "error_code": error_code,
        "diagnostic_code": diagnostic_code,
        "diagnostic_type": diagnostic_type,
        "diagnostic_detail": diagnostic_detail,
    }
    try:
        log_path = Path(path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(canonical_json(event) + "\n")
    except OSError:
        return


def create_text_email_preview(
    *,
    recipient,
    cc,
    subject,
    body,
    state_database_path: Path = PLAIN_EMAIL_STATE_DATABASE_PATH,
    persist: bool = True,
    preview_id: str | None = None,
    expires_at: str | None = None,
) -> TextEmailPreview:
    (
        normalized_recipient,
        normalized_cc,
        normalized_subject,
        normalized_body,
    ) = normalize_text_email_request(
        recipient=recipient,
        cc=cc,
        subject=subject,
        body=body,
    )
    payload_hash = text_payload_hash(
        recipient=normalized_recipient,
        cc=normalized_cc,
        subject=normalized_subject,
        body=normalized_body,
    )
    preview = TextEmailPreview(
        preview_id=preview_id or secrets.token_urlsafe(24),
        expires_at=expires_at
        or utc_text(utc_now() + timedelta(seconds=PREVIEW_TTL_SECONDS)),
        recipient=normalized_recipient,
        cc=normalized_cc,
        subject=normalized_subject,
        body=normalized_body,
        payload_hash=payload_hash,
    )
    if persist:
        PlainEmailStore(state_database_path).save_preview(preview)
    return preview


def build_text_message(
    *, recipient: str, cc: tuple[str, ...], subject: str, body: str
) -> bytes:
    message = EmailMessage()
    message["To"] = recipient
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message.set_content(body)
    return message.as_bytes()


def _result_from_json(value: str, *, replay: bool) -> TextEmailSendResult:
    data = json.loads(value)
    return TextEmailSendResult(
        status=data["status"],
        message_id=data["message_id"],
        recipient=data["recipient"],
        cc=tuple(data["cc"]),
        idempotent_replay=replay,
    )


def send_text_email(
    *,
    preview_id,
    recipient,
    cc,
    subject,
    body,
    idempotency_key,
    gmail_service_factory,
    state_database_path: Path = PLAIN_EMAIL_STATE_DATABASE_PATH,
    audit_log_path: Path = PLAIN_EMAIL_AUDIT_LOG_PATH,
) -> TextEmailSendResult:
    if (
        not isinstance(preview_id, str)
        or not preview_id
        or any(character in preview_id for character in "\r\n")
    ):
        raise EmailServiceError("PREVIEW_NOT_FOUND", "preview_id is required.")
    idempotency_key = validate_idempotency_key(idempotency_key)
    (
        normalized_recipient,
        normalized_cc,
        normalized_subject,
        normalized_body,
    ) = normalize_text_email_request(
        recipient=recipient,
        cc=cc,
        subject=subject,
        body=body,
    )
    payload_hash = text_payload_hash(
        recipient=normalized_recipient,
        cc=normalized_cc,
        subject=normalized_subject,
        body=normalized_body,
    )
    request_fingerprint = sha256_json(
        {"preview_id": preview_id, "payload_hash": payload_hash}
    )
    store = PlainEmailStore(state_database_path)

    existing = store.find_send(idempotency_key)
    if existing is not None:
        if existing["request_fingerprint"] != request_fingerprint:
            raise EmailServiceError(
                "IDEMPOTENCY_CONFLICT",
                "The idempotency key was already used for different content.",
            )
        if existing["status"] == "SENT" and existing["result_json"]:
            return _result_from_json(existing["result_json"], replay=True)
        if existing["status"] == "DELIVERY_UNCERTAIN":
            diagnostic_code = existing["diagnostic_code"]
            suffix = (
                f" Diagnostic: {diagnostic_code}."
                if diagnostic_code
                else ""
            )
            raise EmailServiceError(
                "GMAIL_DELIVERY_UNCERTAIN",
                "Delivery is uncertain; do not retry automatically."
                + suffix,
            )
        if existing["status"] == "SEND_PENDING":
            raise EmailServiceError(
                "IDEMPOTENCY_IN_PROGRESS",
                "A prior operation may still be in progress.",
            )
        raise EmailServiceError(
            "IDEMPOTENCY_PREVIOUSLY_FAILED",
            "A previous definite failure used this idempotency key.",
        )

    preview = store.find_preview(preview_id)
    if preview is None:
        raise EmailServiceError(
            "PREVIEW_NOT_FOUND", "The plain email preview does not exist."
        )
    if datetime.fromisoformat(preview["expires_at"]) <= utc_now():
        raise EmailServiceError("PREVIEW_EXPIRED", "The email preview expired.")
    if preview["payload_hash"] != payload_hash:
        raise EmailServiceError(
            "PREVIEW_STALE", "The plain email payload changed after preview."
        )

    message_bytes = build_text_message(
        recipient=normalized_recipient,
        cc=normalized_cc,
        subject=normalized_subject,
        body=normalized_body,
    )
    if len(base64.urlsafe_b64encode(message_bytes)) > MAX_GMAIL_RAW_BYTES:
        raise EmailServiceError(
            "RAW_MESSAGE_TOO_LARGE", "The Gmail raw message exceeds 34 MiB."
        )

    store.begin_send(
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        preview_id=preview_id,
    )
    try:
        gmail_service = gmail_service_factory()
    except GmailAuthenticationError as error:
        store.update_send(
            idempotency_key,
            "EMAIL_FAILED",
            error_code="GMAIL_AUTH_FAILED",
            diagnostic_code=error.reason,
            diagnostic_type=error.cause_type,
            diagnostic_detail=error.diagnostic_detail,
        )
        _audit(
            audit_log_path,
            status="email_failed",
            preview_id=preview_id,
            recipient=normalized_recipient,
            cc_count=len(normalized_cc),
            error_code="GMAIL_AUTH_FAILED",
            diagnostic_code=error.reason,
            diagnostic_type=error.cause_type,
            diagnostic_detail=error.diagnostic_detail,
        )
        raise EmailServiceError(
            "GMAIL_AUTH_FAILED",
            "Gmail send authentication is unavailable. "
            f"Diagnostic: {error.reason}.",
        ) from error

    try:
        message_id = send_message(gmail_service, message_bytes)
    except GmailApiNotEnabledError:
        store.update_send(
            idempotency_key, "EMAIL_FAILED", error_code="GMAIL_API_NOT_ENABLED"
        )
        _audit(
            audit_log_path,
            status="email_failed",
            preview_id=preview_id,
            recipient=normalized_recipient,
            cc_count=len(normalized_cc),
            error_code="GMAIL_API_NOT_ENABLED",
        )
        raise EmailServiceError(
            "GMAIL_API_NOT_ENABLED", "Gmail API is not enabled."
        ) from None
    except GmailSendError:
        store.update_send(
            idempotency_key, "EMAIL_FAILED", error_code="GMAIL_SEND_FAILED"
        )
        _audit(
            audit_log_path,
            status="email_failed",
            preview_id=preview_id,
            recipient=normalized_recipient,
            cc_count=len(normalized_cc),
            error_code="GMAIL_SEND_FAILED",
        )
        raise EmailServiceError(
            "GMAIL_SEND_FAILED", "Gmail rejected the send request."
        ) from None
    except GmailDeliveryUncertainError as error:
        store.update_send(
            idempotency_key,
            "DELIVERY_UNCERTAIN",
            error_code="GMAIL_DELIVERY_UNCERTAIN",
            diagnostic_code=error.reason,
            diagnostic_type=error.cause_type,
            diagnostic_detail=error.diagnostic_detail,
        )
        _audit(
            audit_log_path,
            status="delivery_uncertain",
            preview_id=preview_id,
            recipient=normalized_recipient,
            cc_count=len(normalized_cc),
            error_code="GMAIL_DELIVERY_UNCERTAIN",
            diagnostic_code=error.reason,
            diagnostic_type=error.cause_type,
            diagnostic_detail=error.diagnostic_detail,
        )
        raise EmailServiceError(
            "GMAIL_DELIVERY_UNCERTAIN",
            "Delivery is uncertain; do not retry automatically. "
            f"Diagnostic: {error.reason}.",
        ) from None

    result = TextEmailSendResult(
        status="sent",
        message_id=message_id,
        recipient=normalized_recipient,
        cc=normalized_cc,
    )
    store.update_send(
        idempotency_key,
        "SENT",
        message_id=message_id,
        result_json=canonical_json(asdict(result)),
    )
    _audit(
        audit_log_path,
        status="sent",
        preview_id=preview_id,
        recipient=normalized_recipient,
        cc_count=len(normalized_cc),
    )
    return result
