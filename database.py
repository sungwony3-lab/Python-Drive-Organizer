import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DATABASE_PATH = PROJECT_ROOT / "data" / "drive_index.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    file_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    extension TEXT,
    normalized_name TEXT,
    base_name TEXT,
    revision_type TEXT,
    revision_number INTEGER,
    copy_type TEXT,
    copy_number INTEGER,
    auto_action TEXT NOT NULL DEFAULT 'NONE',
    parser_version TEXT,
    size_bytes INTEGER,
    created_time TEXT,
    modified_time TEXT,
    parent_id TEXT,
    md5_checksum TEXT,
    trashed INTEGER NOT NULL DEFAULT 0,
    owned_by_me INTEGER,
    scan_id TEXT,
    last_seen_scan_id TEXT,
    indexed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_parent_id ON files(parent_id);
CREATE INDEX IF NOT EXISTS idx_files_modified_time ON files(modified_time);

CREATE TABLE IF NOT EXISTS folders (
    folder_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id TEXT,
    scan_id TEXT,
    last_seen_scan_id TEXT,
    indexed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_folders_parent_id ON folders(parent_id);

CREATE TABLE IF NOT EXISTS scan_state (
    scan_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    files_seen INTEGER NOT NULL DEFAULT 0,
    folders_seen INTEGER NOT NULL DEFAULT 0,
    files_inserted INTEGER NOT NULL DEFAULT 0,
    files_updated INTEGER NOT NULL DEFAULT 0,
    files_skipped INTEGER NOT NULL DEFAULT 0,
    files_deleted INTEGER NOT NULL DEFAULT 0,
    folders_inserted INTEGER NOT NULL DEFAULT 0,
    folders_updated INTEGER NOT NULL DEFAULT 0,
    folders_skipped INTEGER NOT NULL DEFAULT 0,
    folders_deleted INTEGER NOT NULL DEFAULT 0,
    scope_type TEXT,
    scope_id TEXT,
    message TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_groups (
    group_id TEXT PRIMARY KEY,
    parent_id TEXT,
    group_base_name TEXT NOT NULL,
    extension TEXT,
    member_count INTEGER NOT NULL,
    revision_count INTEGER NOT NULL,
    copy_count INTEGER NOT NULL,
    auto_delete_count INTEGER NOT NULL,
    latest_revision_number INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_file_groups_lookup
ON file_groups(parent_id, group_base_name, extension);

CREATE TABLE IF NOT EXISTS file_group_members (
    group_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    member_type TEXT NOT NULL,
    revision_number INTEGER,
    copy_number INTEGER,
    auto_action TEXT NOT NULL,
    PRIMARY KEY(group_id, file_id),
    UNIQUE(file_id)
);

CREATE INDEX IF NOT EXISTS idx_file_group_members_group_id
ON file_group_members(group_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_file_group_members_file_id
ON file_group_members(file_id);

CREATE TABLE IF NOT EXISTS contacts (
    contact_id TEXT PRIMARY KEY,
    organization TEXT,
    name TEXT NOT NULL,
    title TEXT,
    email TEXT,
    phone TEXT,
    normalized_organization TEXT,
    normalized_name TEXT NOT NULL,
    normalized_title TEXT,
    normalized_email TEXT,
    email_usable INTEGER NOT NULL DEFAULT 0,
    conflict_code TEXT,
    source_spreadsheet_id TEXT NOT NULL,
    source_sheet_id INTEGER NOT NULL,
    source_sheet_name TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    source_fingerprint TEXT NOT NULL,
    last_seen_sync_id TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_spreadsheet_id, source_sheet_id, source_row)
);

CREATE INDEX IF NOT EXISTS idx_contacts_normalized_name
ON contacts(normalized_name);

CREATE INDEX IF NOT EXISTS idx_contacts_normalized_email
ON contacts(normalized_email);

CREATE INDEX IF NOT EXISTS idx_contacts_organization
ON contacts(normalized_organization);

CREATE INDEX IF NOT EXISTS idx_contacts_title
ON contacts(normalized_title);

CREATE INDEX IF NOT EXISTS idx_contacts_last_seen_sync
ON contacts(last_seen_sync_id);

CREATE TABLE IF NOT EXISTS contacts_sync_state (
    sync_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    source_spreadsheet_id TEXT,
    source_sheet_id INTEGER,
    source_sheet_name TEXT,
    rows_seen INTEGER NOT NULL DEFAULT 0,
    valid_rows INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    invalid INTEGER NOT NULL DEFAULT 0,
    conflicts INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contacts_sync_started
ON contacts_sync_state(started_at);

CREATE TABLE IF NOT EXISTS contacts_sync_issues (
    sync_id TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    contact_id TEXT,
    issue_code TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(sync_id, source_row, issue_code)
);

CREATE INDEX IF NOT EXISTS idx_contacts_sync_issues_sync
ON contacts_sync_issues(sync_id);
"""


MIGRATION_COLUMNS = {
    "files": {
        "last_seen_scan_id": "TEXT",
        "normalized_name": "TEXT",
        "base_name": "TEXT",
        "revision_type": "TEXT",
        "revision_number": "INTEGER",
        "copy_type": "TEXT",
        "copy_number": "INTEGER",
        "auto_action": "TEXT NOT NULL DEFAULT 'NONE'",
        "parser_version": "TEXT",
    },
    "folders": {
        "last_seen_scan_id": "TEXT",
    },
    "scan_state": {
        "files_inserted": "INTEGER NOT NULL DEFAULT 0",
        "files_updated": "INTEGER NOT NULL DEFAULT 0",
        "files_skipped": "INTEGER NOT NULL DEFAULT 0",
        "files_deleted": "INTEGER NOT NULL DEFAULT 0",
        "folders_inserted": "INTEGER NOT NULL DEFAULT 0",
        "folders_updated": "INTEGER NOT NULL DEFAULT 0",
        "folders_skipped": "INTEGER NOT NULL DEFAULT 0",
        "folders_deleted": "INTEGER NOT NULL DEFAULT 0",
    },
}


def connect_database(
    path: Path = DATABASE_PATH, read_only: bool = False
) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True
        )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    migrate_schema(connection)
    connection.commit()


def migrate_schema(connection: sqlite3.Connection) -> None:
    for table, columns in MIGRATION_COLUMNS.items():
        existing_columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        for column, definition in columns.items():
            if column not in existing_columns:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )


def start_scan(
    connection: sqlite3.Connection,
    scan_id: str,
    started_at: str,
    scope_type: str = "USER_DRIVE",
    scope_id: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO scan_state (
            scan_id, status, started_at, scope_type, scope_id, created_at
        ) VALUES (?, 'RUNNING', ?, ?, ?, ?)
        """,
        (scan_id, started_at, scope_type, scope_id, started_at),
    )
    connection.commit()


def load_existing_files(connection: sqlite3.Connection) -> dict[str, dict]:
    rows = connection.execute(
        """
        SELECT file_id, name, mime_type, extension, size_bytes, created_time,
               modified_time, parent_id, md5_checksum, trashed, owned_by_me,
               scan_id, last_seen_scan_id, indexed_at, normalized_name,
               base_name, revision_type, revision_number, copy_type,
               copy_number, auto_action, parser_version
        FROM files
        """
    )
    return {row["file_id"]: dict(row) for row in rows}


def load_existing_folders(connection: sqlite3.Connection) -> dict[str, dict]:
    rows = connection.execute(
        """
        SELECT folder_id, name, parent_id, scan_id, last_seen_scan_id, indexed_at
        FROM folders
        """
    )
    return {row["folder_id"]: dict(row) for row in rows}


def insert_files(connection: sqlite3.Connection, records: list[dict]) -> None:
    if not records:
        return
    connection.executemany(
        """
        INSERT INTO files (
            file_id, name, mime_type, extension, normalized_name, base_name,
            revision_type, revision_number, copy_type, copy_number, auto_action,
            parser_version, size_bytes, created_time, modified_time, parent_id,
            md5_checksum, trashed, owned_by_me, scan_id, last_seen_scan_id,
            indexed_at
        ) VALUES (
            :file_id, :name, :mime_type, :extension, :normalized_name,
            :base_name, :revision_type, :revision_number, :copy_type,
            :copy_number, :auto_action, :parser_version, :size_bytes,
            :created_time, :modified_time, :parent_id, :md5_checksum,
            :trashed, :owned_by_me, :scan_id, :last_seen_scan_id, :indexed_at
        )
        """,
        records,
    )


def update_files(connection: sqlite3.Connection, records: list[dict]) -> None:
    if not records:
        return
    connection.executemany(
        """
        UPDATE files SET
            name = :name,
            mime_type = :mime_type,
            extension = :extension,
            normalized_name = :normalized_name,
            base_name = :base_name,
            revision_type = :revision_type,
            revision_number = :revision_number,
            copy_type = :copy_type,
            copy_number = :copy_number,
            auto_action = :auto_action,
            parser_version = :parser_version,
            size_bytes = :size_bytes,
            created_time = :created_time,
            modified_time = :modified_time,
            parent_id = :parent_id,
            md5_checksum = :md5_checksum,
            trashed = :trashed,
            owned_by_me = :owned_by_me,
            scan_id = :scan_id,
            last_seen_scan_id = :last_seen_scan_id,
            indexed_at = :indexed_at
        WHERE file_id = :file_id
        """,
        records,
    )


def load_files_needing_parser(
    connection: sqlite3.Connection, parser_version: str
) -> list[dict]:
    rows = connection.execute(
        """
        SELECT file_id, name, extension
        FROM files
        WHERE parser_version IS NULL OR parser_version <> ?
        """,
        (parser_version,),
    )
    return [dict(row) for row in rows]


def update_file_parser_results(
    connection: sqlite3.Connection, records: list[dict]
) -> None:
    if not records:
        return
    connection.executemany(
        """
        UPDATE files SET
            normalized_name = :normalized_name,
            base_name = :base_name,
            revision_type = :revision_type,
            revision_number = :revision_number,
            copy_type = :copy_type,
            copy_number = :copy_number,
            auto_action = :auto_action,
            parser_version = :parser_version
        WHERE file_id = :file_id
        """,
        records,
    )


def mark_files_seen(
    connection: sqlite3.Connection, file_ids: list[str], scan_id: str
) -> None:
    if not file_ids:
        return
    connection.executemany(
        "UPDATE files SET last_seen_scan_id = ? WHERE file_id = ?",
        ((scan_id, file_id) for file_id in file_ids),
    )


def insert_folders(connection: sqlite3.Connection, records: list[dict]) -> None:
    if not records:
        return
    connection.executemany(
        """
        INSERT INTO folders (
            folder_id, name, parent_id, scan_id, last_seen_scan_id, indexed_at
        ) VALUES (
            :folder_id, :name, :parent_id, :scan_id, :last_seen_scan_id,
            :indexed_at
        )
        """,
        records,
    )


def update_folders(connection: sqlite3.Connection, records: list[dict]) -> None:
    if not records:
        return
    connection.executemany(
        """
        UPDATE folders SET
            name = :name,
            parent_id = :parent_id,
            scan_id = :scan_id,
            last_seen_scan_id = :last_seen_scan_id,
            indexed_at = :indexed_at
        WHERE folder_id = :folder_id
        """,
        records,
    )


def mark_folders_seen(
    connection: sqlite3.Connection, folder_ids: list[str], scan_id: str
) -> None:
    if not folder_ids:
        return
    connection.executemany(
        "UPDATE folders SET last_seen_scan_id = ? WHERE folder_id = ?",
        ((scan_id, folder_id) for folder_id in folder_ids),
    )


def delete_unseen_files(connection: sqlite3.Connection, scan_id: str) -> int:
    cursor = connection.execute(
        """
        DELETE FROM files
        WHERE last_seen_scan_id IS NULL OR last_seen_scan_id <> ?
        """,
        (scan_id,),
    )
    return cursor.rowcount


def delete_unseen_folders(connection: sqlite3.Connection, scan_id: str) -> int:
    cursor = connection.execute(
        """
        DELETE FROM folders
        WHERE last_seen_scan_id IS NULL OR last_seen_scan_id <> ?
        """,
        (scan_id,),
    )
    return cursor.rowcount


def complete_scan(
    connection: sqlite3.Connection,
    scan_id: str,
    finished_at: str,
    statistics: dict[str, int],
) -> None:
    parameters = {
        "scan_id": scan_id,
        "finished_at": finished_at,
        **statistics,
    }
    connection.execute(
        """
        UPDATE scan_state
        SET status = 'COMPLETED',
            finished_at = :finished_at,
            files_seen = :files_seen,
            folders_seen = :folders_seen,
            files_inserted = :files_inserted,
            files_updated = :files_updated,
            files_skipped = :files_skipped,
            files_deleted = :files_deleted,
            folders_inserted = :folders_inserted,
            folders_updated = :folders_updated,
            folders_skipped = :folders_skipped,
            folders_deleted = :folders_deleted,
            message = NULL
        WHERE scan_id = :scan_id
        """,
        parameters,
    )
    connection.commit()


def fail_scan(
    connection: sqlite3.Connection,
    scan_id: str,
    finished_at: str,
    files_seen: int,
    folders_seen: int,
    message: str,
) -> None:
    connection.execute(
        """
        UPDATE scan_state
        SET status = 'FAILED',
            finished_at = ?,
            files_seen = ?,
            folders_seen = ?,
            message = ?
        WHERE scan_id = ?
        """,
        (finished_at, files_seen, folders_seen, message, scan_id),
    )
    connection.commit()
