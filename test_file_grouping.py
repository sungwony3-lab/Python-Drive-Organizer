import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from database import connect_database, initialize_schema, insert_files
from file_grouping import (
    make_group_id,
    normalize_group_base_name,
    rebuild_file_groups,
)
from name_parser import parse_filename
from scanner import extract_extension


def file_record(file_id: str, name: str, parent_id: str | None) -> dict:
    extension = extract_extension(name)
    return {
        "file_id": file_id,
        "name": name,
        "mime_type": "application/octet-stream",
        "extension": extension,
        **parse_filename(name, extension),
        "size_bytes": None,
        "created_time": "2026-01-01T00:00:00Z",
        "modified_time": "2026-01-01T00:00:00Z",
        "parent_id": parent_id,
        "md5_checksum": None,
        "trashed": 0,
        "owned_by_me": 1,
        "scan_id": "SCAN-TEST",
        "last_seen_scan_id": "SCAN-TEST",
        "indexed_at": "2026-01-01T00:00:00Z",
    }


def add_files(connection: sqlite3.Connection, records: list[dict]) -> None:
    insert_files(connection, records)
    connection.commit()


def table_snapshot(connection: sqlite3.Connection) -> tuple[list[tuple], list[tuple]]:
    groups = [
        tuple(row)
        for row in connection.execute("SELECT * FROM file_groups ORDER BY group_id")
    ]
    members = [
        tuple(row)
        for row in connection.execute(
            "SELECT * FROM file_group_members ORDER BY group_id, file_id"
        )
    ]
    return groups, members


def files_hash(connection: sqlite3.Connection) -> str:
    rows = connection.execute("SELECT * FROM files ORDER BY file_id").fetchall()
    payload = "\n".join(
        "|".join("" if value is None else str(value) for value in row)
        for row in rows
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class FileGroupingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        initialize_schema(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_schema_is_idempotent(self) -> None:
        initialize_schema(self.connection)
        initialize_schema(self.connection)
        tables = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertIn("file_groups", tables)
        self.assertIn("file_group_members", tables)

    def test_required_fixture_groups_and_statistics(self) -> None:
        names = (
            "ABC.pdf",
            "ABC R1.pdf",
            "ABC R2.pdf",
            "ABC (1).pdf",
            "ABC (2).pdf",
        )
        add_files(
            self.connection,
            [file_record(f"f{index}", name, "parent-A") for index, name in enumerate(names)],
        )

        statistics = rebuild_file_groups(
            self.connection, "2026-01-01T00:00:00Z"
        )
        group = self.connection.execute("SELECT * FROM file_groups").fetchone()

        self.assertEqual(statistics.to_dict(), {
            "files_count": 5,
            "groups_count": 1,
            "members_count": 5,
        })
        self.assertEqual(group["group_base_name"], "abc")
        self.assertEqual(group["member_count"], 5)
        self.assertEqual(group["revision_count"], 2)
        self.assertEqual(group["copy_count"], 2)
        self.assertEqual(group["auto_delete_count"], 2)
        self.assertEqual(group["latest_revision_number"], 2)

    def test_different_parent_and_extension_are_separate(self) -> None:
        add_files(
            self.connection,
            [
                file_record("f1", "ABC R1.pdf", "parent-A"),
                file_record("f2", "ABC R2.pdf", "parent-B"),
                file_record("f3", "ABC R2.dwg", "parent-A"),
            ],
        )
        rebuild_file_groups(self.connection, "2026-01-01T00:00:00Z")

        groups = self.connection.execute(
            "SELECT parent_id, extension, group_id FROM file_groups"
        ).fetchall()
        self.assertEqual(len(groups), 3)
        self.assertEqual(len({row["group_id"] for row in groups}), 3)

    def test_group_id_is_deterministic_and_null_safe(self) -> None:
        normalized = normalize_group_base_name("  ABC   도면  ")
        self.assertEqual(normalized, "abc 도면")
        first = make_group_id(None, normalized, None)
        second = make_group_id(None, normalized, None)
        self.assertEqual(first, second)
        self.assertNotEqual(first, make_group_id("", normalized, ""))

    def test_stale_parser_version_is_rejected_without_file_changes(self) -> None:
        record = file_record("f1", "ABC.pdf", "parent-A")
        record["parser_version"] = "OLD-PARSER"
        add_files(self.connection, [record])
        before = files_hash(self.connection)

        with self.assertRaisesRegex(ValueError, "--parse-only"):
            rebuild_file_groups(self.connection, "2026-01-01T00:00:00Z")

        self.assertEqual(before, files_hash(self.connection))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM file_groups"
            ).fetchone()[0],
            0,
        )

    def test_multiple_parentheses_are_not_forced_into_base_group(self) -> None:
        add_files(
            self.connection,
            [
                file_record("f1", "ABC.pdf", "parent-A"),
                file_record("f2", "ABC (1)(2).pdf", "parent-A"),
            ],
        )
        rebuild_file_groups(self.connection, "2026-01-01T00:00:00Z")

        groups = self.connection.execute(
            "SELECT group_base_name, member_count FROM file_groups "
            "ORDER BY group_base_name"
        ).fetchall()
        self.assertEqual(
            [(row["group_base_name"], row["member_count"]) for row in groups],
            [("abc", 1), ("abc (1)(2)", 1)],
        )

    def test_revision_copy_priority_preserves_numbers(self) -> None:
        add_files(
            self.connection,
            [file_record("f1", "ABC R2 (1).pdf", "parent-A")],
        )
        rebuild_file_groups(self.connection, "2026-01-01T00:00:00Z")
        member = self.connection.execute(
            "SELECT * FROM file_group_members"
        ).fetchone()
        group = self.connection.execute("SELECT * FROM file_groups").fetchone()

        self.assertEqual(member["member_type"], "AUTO_DELETE_COPY")
        self.assertEqual(member["revision_number"], 2)
        self.assertEqual(member["copy_number"], 1)
        self.assertEqual(member["auto_action"], "DELETE")
        self.assertEqual(group["revision_count"], 1)
        self.assertEqual(group["copy_count"], 1)
        self.assertEqual(group["auto_delete_count"], 1)
        self.assertEqual(group["latest_revision_number"], 2)

    def test_copy_word_uses_copy_member_type_without_auto_delete(self) -> None:
        add_files(
            self.connection,
            [file_record("f1", "ABC copy.pdf", "parent-A")],
        )
        rebuild_file_groups(self.connection, "2026-01-01T00:00:00Z")
        member = self.connection.execute(
            "SELECT member_type, auto_action FROM file_group_members"
        ).fetchone()
        group = self.connection.execute("SELECT * FROM file_groups").fetchone()

        self.assertEqual(tuple(member), ("COPY", "NONE"))
        self.assertEqual(group["copy_count"], 1)
        self.assertEqual(group["auto_delete_count"], 0)

    def test_second_rebuild_is_identical_and_preserves_files(self) -> None:
        add_files(
            self.connection,
            [
                file_record("f1", "ABC.pdf", None),
                file_record("f2", "ABC R1.pdf", None),
            ],
        )
        before_files = files_hash(self.connection)
        rebuild_file_groups(self.connection, "2026-01-01T00:00:00Z")
        first = table_snapshot(self.connection)
        rebuild_file_groups(self.connection, "2026-02-01T00:00:00Z")
        second = table_snapshot(self.connection)

        self.assertEqual(first, second)
        self.assertEqual(before_files, files_hash(self.connection))

    def test_file_id_cannot_belong_to_multiple_groups(self) -> None:
        add_files(
            self.connection,
            [file_record("f1", "ABC.pdf", "parent-A")],
        )
        rebuild_file_groups(self.connection, "2026-01-01T00:00:00Z")
        self.connection.execute(
            """
            INSERT INTO file_groups VALUES (
                'other', 'parent-B', 'abc', 'pdf', 1, 0, 0, 0, NULL,
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
            )
            """
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO file_group_members VALUES (
                    'other', 'f1', 'NORMAL', NULL, NULL, 'NONE'
                )
                """
            )
        self.connection.rollback()

    def test_failed_rebuild_rolls_back_to_previous_groups(self) -> None:
        add_files(
            self.connection,
            [file_record("f1", "ABC.pdf", "parent-A")],
        )
        rebuild_file_groups(self.connection, "2026-01-01T00:00:00Z")
        before = table_snapshot(self.connection)
        self.connection.execute(
            """
            CREATE TRIGGER block_group_delete
            BEFORE DELETE ON file_groups
            BEGIN
                SELECT RAISE(ABORT, 'forced grouping failure');
            END
            """
        )
        self.connection.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            rebuild_file_groups(self.connection, "2026-02-01T00:00:00Z")

        self.assertEqual(before, table_snapshot(self.connection))

    def test_group_only_does_not_authenticate_or_call_drive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "group-only.db"
            connection = connect_database(database_path)
            initialize_schema(connection)
            add_files(
                connection,
                [file_record("f1", "ABC.pdf", "parent-A")],
            )
            connection.close()

            with (
                patch.object(
                    main,
                    "connect_database",
                    side_effect=lambda: connect_database(database_path),
                ),
                patch.object(
                    main,
                    "authenticate",
                    side_effect=AssertionError("Drive API must not be called"),
                ) as authenticate,
                patch.object(sys, "argv", ["main.py", "--group-only"]),
            ):
                self.assertEqual(main.main(), 0)
                authenticate.assert_not_called()

            verification = connect_database(database_path)
            self.assertEqual(
                verification.execute(
                    "SELECT COUNT(*) FROM file_groups"
                ).fetchone()[0],
                1,
            )
            verification.close()


if __name__ == "__main__":
    unittest.main()
