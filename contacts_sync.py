import hashlib
import json
import logging
import sqlite3
import sys
import unicodedata
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from contacts_sheet_client import (
    ContactsSettings,
    ContactsSheetError,
    ContactsSheetSnapshot,
    build_contacts_sheets_service,
    load_contacts_settings,
    read_contacts_sheet,
)
from database import DATABASE_PATH, PROJECT_ROOT, connect_database, initialize_schema
from email_service import EmailServiceError, validate_recipient


EXPECTED_HEADERS = ("소속", "성명", "직급", "이메일", "전화번호")
LOG_PATH = PROJECT_ROOT / "logs" / "contacts_sync.log"
LOCK_PATH = PROJECT_ROOT / "data" / "contacts_sync.lock"
LOGGER_NAME = "python_drive_organizer.contacts_sync"
CONTACT_COMPARE_FIELDS = (
    "organization",
    "name",
    "title",
    "email",
    "phone",
    "normalized_organization",
    "normalized_name",
    "normalized_title",
    "normalized_email",
    "email_usable",
    "conflict_code",
    "source_spreadsheet_id",
    "source_sheet_id",
    "source_sheet_name",
    "source_row",
    "source_fingerprint",
)


class ContactsSyncError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class ContactSyncStatistics:
    rows_seen: int = 0
    valid_rows: int = 0
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    invalid: int = 0
    conflicts: int = 0


@dataclass
class ContactSyncRunResult:
    exit_code: int
    sync_id: str
    status: str
    statistics: ContactSyncStatistics = field(
        default_factory=ContactSyncStatistics
    )
    error_code: str | None = None


@dataclass
class ContactIssue:
    source_row: int
    issue_code: str
    severity: str = "WARNING"
    contact_id: str | None = None


@dataclass
class StagedContact:
    organization: str | None
    name: str
    title: str | None
    email: str | None
    phone: str | None
    normalized_organization: str | None
    normalized_name: str
    normalized_title: str | None
    normalized_email: str | None
    email_usable: int
    conflict_code: str | None
    source_spreadsheet_id: str
    source_sheet_id: int
    source_sheet_name: str
    source_row: int
    source_fingerprint: str
    issue_codes: set[str] = field(default_factory=set)
    contact_id: str | None = None


def utc_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def generate_sync_id() -> str:
    return datetime.now().strftime("CONTACTS-%Y%m%d-%H%M%S")


@contextmanager
def contacts_sync_lock(lock_path: Path = LOCK_PATH):
    """Acquire one non-blocking process lock for contacts current-state writes."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    locked = False
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            raise ContactsSyncError(
                "CONTACTS_SYNC_ALREADY_RUNNING",
                "Another contacts synchronization is already running.",
            ) from error
        locked = True
        yield
    finally:
        if locked:
            try:
                handle.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def normalize_display(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_search(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def optional_display(value: str) -> str | None:
    return value if value else None


def content_fingerprint(values: tuple[str | None, ...]) -> str:
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_headers(header: tuple[str, ...]) -> None:
    normalized = tuple(normalize_display(value) for value in header)
    while normalized and not normalized[-1]:
        normalized = normalized[:-1]
    if normalized != EXPECTED_HEADERS:
        raise ContactsSyncError(
            "CONTACTS_HEADER_MISMATCH",
            "The contacts header must exactly match the five configured columns.",
        )


def stage_snapshot(
    snapshot: ContactsSheetSnapshot,
) -> tuple[list[StagedContact], list[ContactIssue], ContactSyncStatistics]:
    _validate_headers(snapshot.header)
    contacts: list[StagedContact] = []
    issues: list[ContactIssue] = []
    statistics = ContactSyncStatistics()

    for source_row, raw_row in enumerate(snapshot.rows, start=2):
        values = [normalize_display(value) for value in raw_row[:5]]
        values.extend([""] * (5 - len(values)))
        if not any(values):
            continue
        statistics.rows_seen += 1
        organization, name, title, email, phone = values
        if not name:
            statistics.invalid += 1
            issues.append(ContactIssue(source_row, "NAME_MISSING", "ERROR"))
            continue

        normalized_organization = normalize_search(organization) or None
        normalized_name = normalize_search(name)
        normalized_title = normalize_search(title) or None
        normalized_email = email.casefold() if email else None
        issue_codes: set[str] = set()
        email_valid = False
        if not email:
            issue_codes.add("EMAIL_MISSING")
        else:
            try:
                validated_email = validate_recipient(email)
            except EmailServiceError:
                issue_codes.add("EMAIL_INVALID")
            else:
                email = validated_email
                normalized_email = validated_email.casefold()
                email_valid = True

        fingerprint = content_fingerprint(
            (
                normalized_organization,
                normalized_name,
                normalized_title,
                normalized_email,
                unicodedata.normalize("NFKC", phone),
            )
        )
        contacts.append(
            StagedContact(
                organization=optional_display(organization),
                name=name,
                title=optional_display(title),
                email=optional_display(email),
                phone=optional_display(phone),
                normalized_organization=normalized_organization,
                normalized_name=normalized_name,
                normalized_title=normalized_title,
                normalized_email=normalized_email,
                email_usable=int(email_valid),
                conflict_code=(next(iter(issue_codes)) if issue_codes else None),
                source_spreadsheet_id=snapshot.spreadsheet_id,
                source_sheet_id=snapshot.sheet_id,
                source_sheet_name=snapshot.sheet_name,
                source_row=source_row,
                source_fingerprint=fingerprint,
                issue_codes=issue_codes,
            )
        )

    email_counts = Counter(
        contact.normalized_email
        for contact in contacts
        if contact.email_usable and contact.normalized_email
    )
    fingerprint_counts = Counter(contact.source_fingerprint for contact in contacts)

    for contact in contacts:
        if fingerprint_counts[contact.source_fingerprint] > 1:
            contact.issue_codes.add("DUPLICATE_ROW")
        if (
            contact.email_usable
            and contact.normalized_email
            and email_counts[contact.normalized_email] > 1
        ):
            contact.issue_codes.add("DUPLICATE_EMAIL")

        duplicate_codes = contact.issue_codes & {
            "DUPLICATE_ROW",
            "DUPLICATE_EMAIL",
        }
        invalid_codes = contact.issue_codes & {"EMAIL_MISSING", "EMAIL_INVALID"}
        if duplicate_codes:
            statistics.conflicts += 1
        if invalid_codes:
            statistics.invalid += 1
        if contact.issue_codes:
            contact.email_usable = 0
            for code in sorted(contact.issue_codes):
                issues.append(ContactIssue(contact.source_row, code))
            if "DUPLICATE_ROW" in contact.issue_codes:
                contact.conflict_code = "DUPLICATE_ROW"
            elif "DUPLICATE_EMAIL" in contact.issue_codes:
                contact.conflict_code = "DUPLICATE_EMAIL"
            elif "EMAIL_INVALID" in contact.issue_codes:
                contact.conflict_code = "EMAIL_INVALID"
            else:
                contact.conflict_code = "EMAIL_MISSING"

    statistics.valid_rows = len(contacts)
    return contacts, issues, statistics


def _load_existing_contacts(connection: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in connection.execute("SELECT * FROM contacts")]


def _group_by(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        value = row[key] if isinstance(row, dict) else getattr(row, key)
        if value:
            grouped[value].append(row)
    return grouped


def reconcile_contact_ids(
    contacts: list[StagedContact], existing: list[dict]
) -> None:
    existing_email = _group_by(existing, "normalized_email")
    snapshot_email = _group_by(contacts, "normalized_email")
    existing_fingerprint = _group_by(existing, "source_fingerprint")
    snapshot_fingerprint = _group_by(contacts, "source_fingerprint")
    used_ids: set[str] = set()

    for contact in contacts:
        email = contact.normalized_email
        candidates = existing_email.get(email, []) if email else []
        if email and len(snapshot_email[email]) == 1 and len(candidates) == 1:
            candidate_id = candidates[0]["contact_id"]
            if candidate_id not in used_ids:
                contact.contact_id = candidate_id
                used_ids.add(candidate_id)

    for contact in contacts:
        if contact.contact_id:
            continue
        fingerprint = contact.source_fingerprint
        candidates = [
            row
            for row in existing_fingerprint.get(fingerprint, [])
            if row["contact_id"] not in used_ids
        ]
        if len(snapshot_fingerprint[fingerprint]) == 1 and len(candidates) == 1:
            contact.contact_id = candidates[0]["contact_id"]
            used_ids.add(contact.contact_id)

    for fingerprint, staged_group in snapshot_fingerprint.items():
        unassigned = sorted(
            (contact for contact in staged_group if not contact.contact_id),
            key=lambda contact: contact.source_row,
        )
        candidates = sorted(
            (
                row
                for row in existing_fingerprint.get(fingerprint, [])
                if row["contact_id"] not in used_ids
            ),
            key=lambda row: (row["source_row"], row["contact_id"]),
        )
        for contact, row in zip(unassigned, candidates):
            contact.contact_id = row["contact_id"]
            used_ids.add(contact.contact_id)

    for contact in contacts:
        if not contact.contact_id:
            contact.contact_id = f"CONTACT-{uuid.uuid4()}"


def start_contacts_sync(
    connection: sqlite3.Connection,
    sync_id: str,
    started_at: str,
    settings: ContactsSettings,
) -> None:
    connection.execute(
        """
        INSERT INTO contacts_sync_state (
            sync_id, status, started_at, source_spreadsheet_id,
            source_sheet_name, created_at
        ) VALUES (?, 'RUNNING', ?, ?, ?, ?)
        """,
        (
            sync_id,
            started_at,
            settings.spreadsheet_id,
            settings.sheet_name,
            started_at,
        ),
    )
    connection.commit()


def _record_failed_sync(
    connection: sqlite3.Connection,
    sync_id: str,
    error_code: str,
) -> None:
    connection.execute(
        """
        UPDATE contacts_sync_state
        SET status = 'FAILED', finished_at = ?, message = ?
        WHERE sync_id = ?
        """,
        (utc_text(), error_code, sync_id),
    )
    connection.commit()


def apply_contacts_snapshot(
    connection: sqlite3.Connection,
    snapshot: ContactsSheetSnapshot,
    sync_id: str,
    contacts: list[StagedContact],
    issues: list[ContactIssue],
    statistics: ContactSyncStatistics,
) -> ContactSyncStatistics:
    existing = _load_existing_contacts(connection)
    existing_by_id = {row["contact_id"]: row for row in existing}
    reconcile_contact_ids(contacts, existing)
    now = utc_text()
    current_ids = {contact.contact_id for contact in contacts}
    statistics.deleted = len(set(existing_by_id) - current_ids)
    records = []

    for contact in contacts:
        assert contact.contact_id is not None
        old = existing_by_id.get(contact.contact_id)
        record = {
            key: value
            for key, value in asdict(contact).items()
            if key != "issue_codes"
        }
        record.update(
            {
                "last_seen_sync_id": sync_id,
                "synced_at": now,
                "created_at": old["created_at"] if old else now,
                "updated_at": now,
            }
        )
        if old is None:
            statistics.inserted += 1
        elif any(old[field] != record[field] for field in CONTACT_COMPARE_FIELDS):
            statistics.updated += 1
        else:
            statistics.unchanged += 1
            record["updated_at"] = old["updated_at"]
        records.append(record)

    status = (
        "COMPLETED_WITH_WARNINGS"
        if statistics.invalid or statistics.conflicts
        else "COMPLETED"
    )
    try:
        connection.execute("BEGIN")
        connection.execute("DELETE FROM contacts")
        connection.executemany(
            """
            INSERT INTO contacts (
                contact_id, organization, name, title, email, phone,
                normalized_organization, normalized_name, normalized_title,
                normalized_email, email_usable, conflict_code,
                source_spreadsheet_id, source_sheet_id, source_sheet_name,
                source_row, source_fingerprint, last_seen_sync_id, synced_at,
                created_at, updated_at
            ) VALUES (
                :contact_id, :organization, :name, :title, :email, :phone,
                :normalized_organization, :normalized_name, :normalized_title,
                :normalized_email, :email_usable, :conflict_code,
                :source_spreadsheet_id, :source_sheet_id, :source_sheet_name,
                :source_row, :source_fingerprint, :last_seen_sync_id, :synced_at,
                :created_at, :updated_at
            )
            """,
            records,
        )
        for issue in issues:
            contact = next(
                (
                    item
                    for item in contacts
                    if item.source_row == issue.source_row
                ),
                None,
            )
            issue.contact_id = contact.contact_id if contact else None
        connection.executemany(
            """
            INSERT INTO contacts_sync_issues (
                sync_id, source_row, contact_id, issue_code,
                severity, message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    sync_id,
                    issue.source_row,
                    issue.contact_id,
                    issue.issue_code,
                    issue.severity,
                    f"Row {issue.source_row} has {issue.issue_code}",
                    now,
                )
                for issue in issues
            ],
        )
        connection.execute(
            """
            UPDATE contacts_sync_state
            SET status = ?, finished_at = ?, source_sheet_id = ?,
                rows_seen = ?, valid_rows = ?, inserted = ?, updated = ?,
                deleted = ?, unchanged = ?, invalid = ?, conflicts = ?,
                message = NULL
            WHERE sync_id = ?
            """,
            (
                status,
                now,
                snapshot.sheet_id,
                statistics.rows_seen,
                statistics.valid_rows,
                statistics.inserted,
                statistics.updated,
                statistics.deleted,
                statistics.unchanged,
                statistics.invalid,
                statistics.conflicts,
                sync_id,
            ),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    return statistics


def create_logger(log_path: Path = LOG_PATH) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s%(timezone)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    class LocalTimezoneFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.timezone = datetime.now().astimezone().strftime("%z")
            return True

    timezone_filter = LocalTimezoneFilter()
    for handler in (
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ):
        handler.setFormatter(formatter)
        handler.addFilter(timezone_filter)
        logger.addHandler(handler)
    return logger


def execute_contacts_sync(
    logger: logging.Logger,
    *,
    database_path: Path = DATABASE_PATH,
    lock_path: Path = LOCK_PATH,
) -> ContactSyncRunResult:
    sync_id = generate_sync_id()
    connection: sqlite3.Connection | None = None
    started = False
    error_code: str | None = None
    result = 1
    status = "FAILED"
    statistics = ContactSyncStatistics()
    try:
        with contacts_sync_lock(lock_path):
            settings = load_contacts_settings()
            connection = connect_database(database_path)
            initialize_schema(connection)
            start_contacts_sync(connection, sync_id, utc_text(), settings)
            started = True
            logger.info("Contacts sync started | sync_id=%s", sync_id)
            service = build_contacts_sheets_service()
            snapshot = read_contacts_sheet(service, settings)
            contacts, issues, statistics = stage_snapshot(snapshot)
            apply_contacts_snapshot(
                connection,
                snapshot,
                sync_id,
                contacts,
                issues,
                statistics,
            )
            status = (
                "COMPLETED_WITH_WARNINGS"
                if statistics.invalid or statistics.conflicts
                else "COMPLETED"
            )
            logger.info(
                "Contacts sync %s | sync_id=%s | rows_seen=%d valid_rows=%d "
                "inserted=%d updated=%d deleted=%d unchanged=%d invalid=%d conflicts=%d",
                status,
                sync_id,
                statistics.rows_seen,
                statistics.valid_rows,
                statistics.inserted,
                statistics.updated,
                statistics.deleted,
                statistics.unchanged,
                statistics.invalid,
                statistics.conflicts,
            )
            result = 0
    except (ContactsSheetError, ContactsSyncError) as error:
        error_code = error.code
    except sqlite3.Error:
        error_code = "CONTACTS_DATABASE_FAILED"
    except Exception:
        error_code = "CONTACTS_UNEXPECTED_ERROR"
    finally:
        if connection is not None:
            if result:
                connection.rollback()
                if started and error_code:
                    try:
                        _record_failed_sync(connection, sync_id, error_code)
                    except sqlite3.Error:
                        pass
            connection.close()

    if result:
        logger.error(
            "Contacts sync FAILED | sync_id=%s | error_code=%s",
            sync_id,
            error_code or "CONTACTS_UNEXPECTED_ERROR",
        )
    return ContactSyncRunResult(
        exit_code=result,
        sync_id=sync_id,
        status=status,
        statistics=statistics,
        error_code=error_code,
    )


def run_contacts_sync(
    logger: logging.Logger,
    *,
    database_path: Path = DATABASE_PATH,
    lock_path: Path = LOCK_PATH,
) -> int:
    return execute_contacts_sync(
        logger,
        database_path=database_path,
        lock_path=lock_path,
    ).exit_code


def main() -> int:
    return run_contacts_sync(create_logger())


if __name__ == "__main__":
    raise SystemExit(main())
