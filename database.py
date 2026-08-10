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
"""


MIGRATION_COLUMNS = {
    "files": {
        "last_seen_scan_id": "TEXT",
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


def connect_database(path: Path = DATABASE_PATH) -> sqlite3.Connection:
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
               scan_id, last_seen_scan_id, indexed_at
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
            file_id, name, mime_type, extension, size_bytes, created_time,
            modified_time, parent_id, md5_checksum, trashed, owned_by_me,
            scan_id, last_seen_scan_id, indexed_at
        ) VALUES (
            :file_id, :name, :mime_type, :extension, :size_bytes, :created_time,
            :modified_time, :parent_id, :md5_checksum, :trashed, :owned_by_me,
            :scan_id, :last_seen_scan_id, :indexed_at
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
