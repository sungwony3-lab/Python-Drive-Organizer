import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import main
from database import (
    connect_database,
    initialize_schema,
    insert_files,
    insert_folders,
)
from file_grouping import rebuild_file_groups
from name_parser import parse_filename
from scanner import extract_extension
from search_service import SearchService


def make_file(
    file_id: str,
    name: str,
    parent_id: str | None,
    modified_time: str,
) -> dict:
    extension = extract_extension(name)
    return {
        "file_id": file_id,
        "name": name,
        "mime_type": "application/octet-stream",
        "extension": extension,
        **parse_filename(name, extension),
        "size_bytes": None,
        "created_time": "2026-01-01T00:00:00Z",
        "modified_time": modified_time,
        "parent_id": parent_id,
        "md5_checksum": None,
        "trashed": 0,
        "owned_by_me": 1,
        "scan_id": "SCAN-SEARCH",
        "last_seen_scan_id": "SCAN-SEARCH",
        "indexed_at": "2026-01-01T00:00:00Z",
    }


def make_folder(
    folder_id: str, name: str, parent_id: str | None
) -> dict:
    return {
        "folder_id": folder_id,
        "name": name,
        "parent_id": parent_id,
        "scan_id": "SCAN-SEARCH",
        "last_seen_scan_id": "SCAN-SEARCH",
        "indexed_at": "2026-01-01T00:00:00Z",
    }


def seed_database(connection: sqlite3.Connection) -> None:
    insert_folders(
        connection,
        [
            make_folder("A", "A", None),
            make_folder("B", "B", "A"),
            make_folder("C", "C", "B"),
            make_folder("D", "D", "A"),
            make_folder("S1", "Same", "A"),
            make_folder("S2", "Same", "D"),
            make_folder("O", "Orphan", "MISSING"),
            make_folder("X", "Cycle-X", "Y"),
            make_folder("Y", "Cycle-Y", "X"),
            make_folder("SELF", "Self", "SELF"),
        ],
    )
    insert_files(
        connection,
        [
            make_file("f1", "ABC.pdf", "C", "2026-01-01T00:00:00Z"),
            make_file("f2", "ABC R1.pdf", "C", "2026-02-01T00:00:00Z"),
            make_file("f3", "ABC (1).pdf", "C", "2026-03-01T00:00:00Z"),
            make_file("f4", "한글 보고서.pdf", "D", "2026-04-01T00:00:00Z"),
            make_file("f5", "Alpha Plan.pdf", "A", "2026-05-01T00:00:00Z"),
            make_file("f6", "Alpha Latest copy.pdf", "B", "2026-06-01T00:00:00Z"),
        ],
    )
    connection.commit()
    rebuild_file_groups(connection, "2026-01-01T00:00:00Z")


def database_hash(connection: sqlite3.Connection) -> str:
    payload = []
    for table in (
        "files",
        "folders",
        "scan_state",
        "file_groups",
        "file_group_members",
    ):
        rows = connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        payload.append(
            [table, [list(row) for row in rows]]
        )
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class SearchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        initialize_schema(self.connection)
        seed_database(self.connection)
        self.service = SearchService(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_file_name_partial_case_and_korean_search(self) -> None:
        lower = self.service.search_name("abc")
        upper = self.service.search_name("ABC")
        korean = self.service.search_name("한글")

        self.assertEqual(lower.total, 3)
        self.assertEqual(
            [item["file_id"] for item in lower.items],
            [item["file_id"] for item in upper.items],
        )
        self.assertEqual(korean.total, 1)
        self.assertEqual(korean.items[0]["file_id"], "f4")

    def test_limit_total_and_showing(self) -> None:
        result = self.service.search_name("abc", limit=2)
        self.assertEqual(result.total, 3)
        self.assertEqual(result.showing, 2)

    def test_folder_paths_missing_parent_and_cycles(self) -> None:
        self.assertEqual(self.service.folder_index.folder_path("C"), "/A/B/C")
        self.assertEqual(
            self.service.folder_index.folder_path("O"),
            "/[MISSING_PARENT:MISSING]/Orphan",
        )
        self.assertIn("[CYCLE:", self.service.folder_index.folder_path("X"))
        self.assertIn("[CYCLE:", self.service.folder_index.folder_path("SELF"))

    def test_folder_search_preserves_same_name_different_ids(self) -> None:
        result = self.service.search_folders("same")
        self.assertEqual(result.total, 2)
        self.assertEqual({item["folder_id"] for item in result.items}, {"S1", "S2"})

    def test_direct_and_recursive_folder_listing(self) -> None:
        direct = self.service.list_folder("A", recursive=False)
        recursive = self.service.list_folder("A", recursive=True)

        direct_folders = {
            item["item_id"]
            for item in direct.items
            if item["item_type"] == "FOLDER"
        }
        recursive_files = {
            item["item_id"]
            for item in recursive.items
            if item["item_type"] == "FILE"
        }
        self.assertEqual(direct_folders, {"B", "D", "S1"})
        self.assertEqual(recursive_files, {"f1", "f2", "f3", "f4", "f5", "f6"})

    def test_revision_copy_auto_delete_and_group_searches(self) -> None:
        revisions = self.service.search_revisions()
        copies = self.service.search_copies()
        auto_delete = self.service.search_auto_delete()
        groups = self.service.search_groups(min_members=2)

        self.assertEqual(revisions.total, 1)
        self.assertEqual(revisions.items[0]["revision_number"], 1)
        self.assertEqual(revisions.items[0]["latest_revision_number"], 1)
        self.assertEqual(copies.total, 2)
        self.assertEqual(auto_delete.total, 1)
        self.assertEqual(auto_delete.items[0]["auto_action"], "DELETE")
        self.assertEqual(groups.total, 1)
        self.assertEqual(groups.items[0]["member_count"], 3)
        self.assertEqual(len(groups.items[0]["members"]), 3)

    def test_recent_sorting(self) -> None:
        result = self.service.recent(3)
        self.assertEqual(result.showing, 3)
        self.assertEqual(
            [item["file_id"] for item in result.items],
            ["f6", "f5", "f4"],
        )

    def test_changed_in_scan_current_rows(self) -> None:
        result = self.service.changed_in_scan("SCAN-SEARCH", limit=100)
        self.assertEqual(result.total, 16)
        self.assertEqual(result.showing, 16)

    def test_searches_do_not_modify_database(self) -> None:
        before = database_hash(self.connection)
        self.service.search_name("ABC")
        self.service.search_folders("A")
        self.service.list_folder("A", recursive=True)
        self.service.search_revisions()
        self.service.search_copies()
        self.service.search_auto_delete()
        self.service.search_groups(2)
        self.service.recent(3)
        self.service.changed_in_scan("SCAN-SEARCH")
        self.service.render_tree(include_files=True)
        self.assertEqual(before, database_hash(self.connection))

    def test_tree_hierarchy_root_depth_files_and_determinism(self) -> None:
        first = self.service.render_tree()
        second = self.service.render_tree()
        rooted = self.service.render_tree(root_folder_id="B")
        shallow = self.service.render_tree(root_folder_id="A", max_depth=1)
        shallow_full = self.service.render_tree(max_depth=1)
        with_files = self.service.render_tree(
            root_folder_id="A", include_files=True
        )

        self.assertEqual(first.text, second.text)
        self.assertEqual(first.folder_ids, set(self.service.folder_index.folders))
        self.assertIn("B\n└─ C", rooted.text)
        self.assertIn("├─ B", shallow.text)
        self.assertNotIn("C", shallow.folder_ids)
        self.assertNotIn("C", shallow_full.folder_ids)
        self.assertLess(
            len(shallow_full.folder_ids),
            len(self.service.folder_index.folders),
        )
        self.assertIn("[FILE] ABC.pdf", with_files.text)
        self.assertEqual(with_files.file_ids, {"f1", "f2", "f3", "f4", "f5", "f6"})
        self.assertEqual(first.text.count("Same"), 2)
        self.assertIn("[MISSING PARENT: MISSING]", first.text)
        self.assertIn("[CYCLES / UNRESOLVED]", first.text)

    def test_missing_tree_root_is_an_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "찾을 수 없습니다"):
            self.service.render_tree(root_folder_id="DOES-NOT-EXIST")

    def test_very_deep_tree_does_not_use_python_recursion(self) -> None:
        folders = []
        parent_id = None
        for index in range(1100):
            folder_id = f"DEEP-{index}"
            folders.append(make_folder(folder_id, folder_id, parent_id))
            parent_id = folder_id
        insert_folders(self.connection, folders)
        self.connection.commit()

        service = SearchService(self.connection)
        result = service.render_tree(root_folder_id="DEEP-0")
        self.assertEqual(len(result.folder_ids), 1100)


class SearchCliTests(unittest.TestCase):
    def test_conflicting_modes_and_invalid_modifiers_are_rejected(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as conflict:
                main.parse_arguments(
                    ["--search-name", "ABC", "--search-copies"]
                )
            with self.assertRaises(SystemExit) as modifier:
                main.parse_arguments(["--recursive", "--search-name", "ABC"])
        self.assertEqual(conflict.exception.code, 2)
        self.assertEqual(modifier.exception.code, 2)

    def test_search_cli_is_read_only_and_does_not_authenticate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "search.db"
            connection = connect_database(database_path)
            initialize_schema(connection)
            seed_database(connection)
            before = database_hash(connection)
            connection.close()

            output = io.StringIO()
            with (
                patch.object(
                    main,
                    "connect_database",
                    side_effect=lambda read_only=False: connect_database(
                        database_path, read_only=read_only
                    ),
                ) as connect,
                patch.object(
                    main,
                    "authenticate",
                    side_effect=AssertionError("Drive API must not be called"),
                ) as authenticate,
                patch.object(sys, "argv", ["main.py", "--search-name", "ABC", "--limit", "2"]),
                redirect_stdout(output),
            ):
                self.assertEqual(main.main(), 0)

            authenticate.assert_not_called()
            self.assertTrue(connect.call_args.kwargs["read_only"])
            self.assertIn("Total matched: 3", output.getvalue())
            self.assertIn("Showing: 2", output.getvalue())

            verification = connect_database(database_path, read_only=True)
            self.assertEqual(before, database_hash(verification))
            verification.close()

    def test_tree_cli_output_file_is_utf8_and_does_not_authenticate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "tree.db"
            output_path = Path(directory) / "tree.txt"
            connection = connect_database(database_path)
            initialize_schema(connection)
            seed_database(connection)
            connection.close()

            console = io.StringIO()
            with (
                patch.object(
                    main,
                    "connect_database",
                    side_effect=lambda read_only=False: connect_database(
                        database_path, read_only=read_only
                    ),
                ),
                patch.object(
                    main,
                    "authenticate",
                    side_effect=AssertionError("Drive API must not be called"),
                ) as authenticate,
                patch.object(
                    sys,
                    "argv",
                    [
                        "main.py",
                        "--tree",
                        "--root-folder",
                        "A",
                        "--include-files",
                        "--output",
                        str(output_path),
                    ],
                ),
                redirect_stdout(console),
            ):
                self.assertEqual(main.main(), 0)

            authenticate.assert_not_called()
            saved = output_path.read_text(encoding="utf-8")
            self.assertIn("[FILE] 한글 보고서.pdf", saved)
            self.assertIn(saved.strip(), console.getvalue())


if __name__ == "__main__":
    unittest.main()
