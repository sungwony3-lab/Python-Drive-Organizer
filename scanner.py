from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from google.oauth2.credentials import Credentials

from database import (
    delete_unseen_files,
    delete_unseen_folders,
    insert_files,
    insert_folders,
    load_existing_files,
    load_existing_folders,
    mark_files_seen,
    mark_folders_seen,
    update_files,
    update_folders,
)
from drive_client import iter_drive_items


FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
FILE_COMPARE_FIELDS = (
    "name",
    "mime_type",
    "extension",
    "size_bytes",
    "created_time",
    "modified_time",
    "parent_id",
    "md5_checksum",
    "trashed",
    "owned_by_me",
)
FOLDER_COMPARE_FIELDS = ("name", "parent_id")


@dataclass
class ScanStatistics:
    files_seen: int = 0
    folders_seen: int = 0
    files_inserted: int = 0
    files_updated: int = 0
    files_skipped: int = 0
    files_deleted: int = 0
    folders_inserted: int = 0
    folders_updated: int = 0
    folders_skipped: int = 0
    folders_deleted: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def first_parent(item: dict) -> str | None:
    parents = item.get("parents") or []
    return parents[0] if parents else None


def extract_extension(name: str) -> str | None:
    suffix = Path(name).suffix
    return suffix[1:].lower() if len(suffix) > 1 else None


def optional_integer(value: object) -> int | None:
    return int(value) if value not in (None, "") else None


def optional_boolean(value: object) -> int | None:
    return int(bool(value)) if value is not None else None


def normalize_file(item: dict) -> dict:
    return {
        "file_id": item["id"],
        "name": item["name"],
        "mime_type": item["mimeType"],
        "extension": extract_extension(item["name"]),
        "size_bytes": optional_integer(item.get("size")),
        "created_time": item.get("createdTime"),
        "modified_time": item.get("modifiedTime"),
        "parent_id": first_parent(item),
        "md5_checksum": item.get("md5Checksum"),
        "trashed": int(bool(item.get("trashed", False))),
        "owned_by_me": optional_boolean(item.get("ownedByMe")),
    }


def normalize_folder(item: dict) -> dict:
    return {
        "folder_id": item["id"],
        "name": item["name"],
        "parent_id": first_parent(item),
    }


def records_equal(existing: dict, current: dict, fields: tuple[str, ...]) -> bool:
    return all(existing.get(field) == current.get(field) for field in fields)


def index_drive(
    connection,
    credentials: Credentials,
    scan_id: str,
    statistics: ScanStatistics,
) -> ScanStatistics:
    existing_files = load_existing_files(connection)
    existing_folders = load_existing_folders(connection)
    current_files: dict[str, dict] = {}
    current_folders: dict[str, dict] = {}

    for item in iter_drive_items(credentials):
        if item["mimeType"] == FOLDER_MIME_TYPE:
            record = normalize_folder(item)
            current_folders[record["folder_id"]] = record
            statistics.folders_seen = len(current_folders)
        else:
            record = normalize_file(item)
            current_files[record["file_id"]] = record
            statistics.files_seen = len(current_files)

    indexed_at = utc_timestamp()
    file_inserts = []
    file_updates = []
    file_skips = []
    folder_inserts = []
    folder_updates = []
    folder_skips = []

    for file_id, current in current_files.items():
        existing = existing_files.get(file_id)
        if existing is None:
            file_inserts.append(
                {
                    **current,
                    "scan_id": scan_id,
                    "last_seen_scan_id": scan_id,
                    "indexed_at": indexed_at,
                }
            )
        elif records_equal(existing, current, FILE_COMPARE_FIELDS):
            file_skips.append(file_id)
        else:
            file_updates.append(
                {
                    **current,
                    "scan_id": scan_id,
                    "last_seen_scan_id": scan_id,
                    "indexed_at": indexed_at,
                }
            )

    for folder_id, current in current_folders.items():
        existing = existing_folders.get(folder_id)
        if existing is None:
            folder_inserts.append(
                {
                    **current,
                    "scan_id": scan_id,
                    "last_seen_scan_id": scan_id,
                    "indexed_at": indexed_at,
                }
            )
        elif records_equal(existing, current, FOLDER_COMPARE_FIELDS):
            folder_skips.append(folder_id)
        else:
            folder_updates.append(
                {
                    **current,
                    "scan_id": scan_id,
                    "last_seen_scan_id": scan_id,
                    "indexed_at": indexed_at,
                }
            )

    statistics.files_inserted = len(file_inserts)
    statistics.files_updated = len(file_updates)
    statistics.files_skipped = len(file_skips)
    statistics.folders_inserted = len(folder_inserts)
    statistics.folders_updated = len(folder_updates)
    statistics.folders_skipped = len(folder_skips)

    insert_files(connection, file_inserts)
    update_files(connection, file_updates)
    mark_files_seen(connection, file_skips, scan_id)
    insert_folders(connection, folder_inserts)
    update_folders(connection, folder_updates)
    mark_folders_seen(connection, folder_skips, scan_id)
    statistics.files_deleted = delete_unseen_files(connection, scan_id)
    statistics.folders_deleted = delete_unseen_folders(connection, scan_id)

    return statistics
