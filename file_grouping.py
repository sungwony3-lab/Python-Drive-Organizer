import hashlib
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from name_parser import PARSER_VERSION


MULTIPLE_SPACES = re.compile(r"\s+")
COPY_TYPES = frozenset(
    {"SINGLE_PAREN_COPY", "KOREAN_COPY", "ENGLISH_COPY"}
)
GROUP_COMPARE_FIELDS = (
    "parent_id",
    "group_base_name",
    "extension",
    "member_count",
    "revision_count",
    "copy_count",
    "auto_delete_count",
    "latest_revision_number",
)


@dataclass
class GroupingStatistics:
    files_count: int = 0
    groups_count: int = 0
    members_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def normalize_group_base_name(base_name: str) -> str:
    return MULTIPLE_SPACES.sub(" ", base_name.strip()).lower()


def canonical_component(value: str | None) -> str:
    if value is None:
        return "N"
    return f"S{len(value)}:{value}"


def make_group_id(
    parent_id: str | None,
    group_base_name: str,
    extension: str | None,
) -> str:
    payload = "|".join(
        canonical_component(value)
        for value in (parent_id, group_base_name, extension)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_revision(file_record: dict) -> bool:
    return (
        file_record.get("revision_number") is not None
        or file_record.get("revision_type") == "REVISION"
    )


def is_copy(file_record: dict) -> bool:
    return file_record.get("copy_type") in COPY_TYPES


def member_type(file_record: dict) -> str:
    if file_record.get("auto_action") == "DELETE":
        return "AUTO_DELETE_COPY"
    if file_record.get("revision_type") == "REVISION":
        return "REVISION"
    if is_copy(file_record):
        return "COPY"
    return "NORMAL"


def load_grouping_files(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """
        SELECT file_id, parent_id, extension, base_name, revision_type,
               revision_number, copy_type, copy_number, auto_action,
               parser_version
        FROM files
        ORDER BY file_id
        """
    )
    return [dict(row) for row in rows]


def load_existing_group_state(
    connection: sqlite3.Connection,
) -> tuple[dict[str, dict], dict[str, tuple]]:
    groups = {
        row["group_id"]: dict(row)
        for row in connection.execute("SELECT * FROM file_groups")
    }
    member_rows: dict[str, list[tuple]] = {}
    for row in connection.execute(
        """
        SELECT group_id, file_id, member_type, revision_number, copy_number,
               auto_action
        FROM file_group_members
        ORDER BY group_id, file_id
        """
    ):
        member_rows.setdefault(row["group_id"], []).append(
            (
                row["file_id"],
                row["member_type"],
                row["revision_number"],
                row["copy_number"],
                row["auto_action"],
            )
        )
    return groups, {
        group_id: tuple(rows) for group_id, rows in member_rows.items()
    }


def build_group_records(
    files: list[dict],
    existing_groups: dict[str, dict],
    existing_members: dict[str, tuple],
    generated_at: str,
) -> tuple[list[dict], list[dict]]:
    grouped: dict[str, dict] = {}

    for file_record in files:
        if file_record.get("parser_version") != PARSER_VERSION:
            raise ValueError(
                f"file_id={file_record['file_id']}의 Parser 버전이 최신이 아닙니다. "
                "먼저 --parse-only를 실행하세요."
            )
        base_name = file_record.get("base_name")
        if base_name is None:
            raise ValueError(
                f"file_id={file_record['file_id']}의 base_name이 없습니다. "
                "먼저 --parse-only를 실행하세요."
            )

        group_base_name = normalize_group_base_name(base_name)
        if not group_base_name:
            raise ValueError(
                f"file_id={file_record['file_id']}의 group_base_name이 비어 있습니다."
            )

        parent_id = file_record.get("parent_id")
        extension = file_record.get("extension")
        group_id = make_group_id(parent_id, group_base_name, extension)
        identity = (parent_id, group_base_name, extension)

        group = grouped.setdefault(
            group_id,
            {
                "identity": identity,
                "parent_id": parent_id,
                "group_base_name": group_base_name,
                "extension": extension,
                "members": [],
            },
        )
        if group["identity"] != identity:
            raise RuntimeError(f"group_id 충돌이 감지되었습니다: {group_id}")

        group["members"].append(
            {
                "group_id": group_id,
                "file_id": file_record["file_id"],
                "member_type": member_type(file_record),
                "revision_number": file_record.get("revision_number"),
                "copy_number": file_record.get("copy_number"),
                "auto_action": file_record.get("auto_action") or "NONE",
                "is_revision": is_revision(file_record),
                "is_copy": is_copy(file_record),
            }
        )

    group_records = []
    member_records = []
    for group_id in sorted(grouped):
        group = grouped[group_id]
        members = sorted(group["members"], key=lambda item: item["file_id"])
        revision_numbers = [
            item["revision_number"]
            for item in members
            if item["revision_number"] is not None
        ]
        record = {
            "group_id": group_id,
            "parent_id": group["parent_id"],
            "group_base_name": group["group_base_name"],
            "extension": group["extension"],
            "member_count": len(members),
            "revision_count": sum(item["is_revision"] for item in members),
            "copy_count": sum(item["is_copy"] for item in members),
            "auto_delete_count": sum(
                item["auto_action"] == "DELETE" for item in members
            ),
            "latest_revision_number": (
                max(revision_numbers) if revision_numbers else None
            ),
        }

        old_group = existing_groups.get(group_id)
        new_member_state = tuple(
            (
                item["file_id"],
                item["member_type"],
                item["revision_number"],
                item["copy_number"],
                item["auto_action"],
            )
            for item in members
        )
        unchanged = (
            old_group is not None
            and all(old_group[field] == record[field] for field in GROUP_COMPARE_FIELDS)
            and existing_members.get(group_id, ()) == new_member_state
        )
        record["created_at"] = (
            old_group["created_at"] if old_group is not None else generated_at
        )
        record["updated_at"] = (
            old_group["updated_at"] if unchanged else generated_at
        )
        group_records.append(record)

        for item in members:
            member_records.append(
                {
                    key: item[key]
                    for key in (
                        "group_id",
                        "file_id",
                        "member_type",
                        "revision_number",
                        "copy_number",
                        "auto_action",
                    )
                }
            )

    return group_records, member_records


def rebuild_file_groups(
    connection: sqlite3.Connection, generated_at: str | None = None
) -> GroupingStatistics:
    if connection.in_transaction:
        raise sqlite3.OperationalError(
            "grouping 시작 전에 열린 transaction을 먼저 완료해야 합니다."
        )

    files = load_grouping_files(connection)
    existing_groups, existing_members = load_existing_group_state(connection)
    group_records, member_records = build_group_records(
        files,
        existing_groups,
        existing_members,
        generated_at or utc_timestamp(),
    )

    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM file_group_members")
        connection.execute("DELETE FROM file_groups")
        connection.executemany(
            """
            INSERT INTO file_groups (
                group_id, parent_id, group_base_name, extension, member_count,
                revision_count, copy_count, auto_delete_count,
                latest_revision_number, created_at, updated_at
            ) VALUES (
                :group_id, :parent_id, :group_base_name, :extension,
                :member_count, :revision_count, :copy_count,
                :auto_delete_count, :latest_revision_number, :created_at,
                :updated_at
            )
            """,
            group_records,
        )
        connection.executemany(
            """
            INSERT INTO file_group_members (
                group_id, file_id, member_type, revision_number, copy_number,
                auto_action
            ) VALUES (
                :group_id, :file_id, :member_type, :revision_number,
                :copy_number, :auto_action
            )
            """,
            member_records,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    return GroupingStatistics(
        files_count=len(files),
        groups_count=len(group_records),
        members_count=len(member_records),
    )
