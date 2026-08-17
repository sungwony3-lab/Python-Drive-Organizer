import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import contacts_sheet_client as sheet_client
import contacts_sync
from contacts_sheet_client import (
    ContactsSettings,
    ContactsSheetError,
    ContactsSheetSnapshot,
)
from database import DATABASE_PATH, connect_database, initialize_schema
from googleapiclient.errors import HttpError


class FakeRequest:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def execute(self, **kwargs) -> dict:
        return self.payload


class FakeValues:
    def __init__(self, header: list, rows: list) -> None:
        self.header = header
        self.rows = rows
        self.calls: list[dict] = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        payload = {"values": [self.header]} if "1:" in kwargs["range"] else {"values": self.rows}
        return FakeRequest(payload)


class FakeSpreadsheets:
    def __init__(self, metadata: dict, header: list, rows: list) -> None:
        self.metadata = metadata
        self.metadata_calls: list[dict] = []
        self.value_resource = FakeValues(header, rows)

    def get(self, **kwargs):
        self.metadata_calls.append(kwargs)
        return FakeRequest(self.metadata)

    def values(self):
        return self.value_resource


class FakeSheetsService:
    def __init__(self, metadata: dict, header: list, rows: list) -> None:
        self.resource = FakeSpreadsheets(metadata, header, rows)

    def spreadsheets(self):
        return self.resource


def metadata_with_tabs(*titles: str) -> dict:
    return {
        "spreadsheetId": "SPREADSHEET-1",
        "properties": {"title": "Address Book"},
        "sheets": [
            {
                "properties": {
                    "sheetId": index + 10,
                    "title": title,
                    "index": index,
                    "hidden": False,
                    "gridProperties": {
                        "rowCount": 100,
                        "columnCount": 8,
                        "frozenRowCount": 1,
                    },
                }
            }
            for index, title in enumerate(titles)
        ],
    }


class ContactsSheetClientTests(unittest.TestCase):
    def test_reads_exact_named_tab_after_metadata_with_bounded_ranges(self) -> None:
        service = FakeSheetsService(
            metadata_with_tabs("Other", "주소록"),
            list(contacts_sync.EXPECTED_HEADERS),
            [["HLB", "홍길동", "부장", "hong@example.com", "010-0000-0000"]],
        )
        snapshot = sheet_client.read_contacts_sheet(
            service, ContactsSettings("SPREADSHEET-1", "주소록")
        )

        self.assertEqual(snapshot.spreadsheet_id, "SPREADSHEET-1")
        self.assertEqual(snapshot.sheet_id, 11)
        self.assertEqual(snapshot.sheet_name, "주소록")
        ranges = [call["range"] for call in service.resource.value_resource.calls]
        self.assertEqual(ranges, ["'주소록'!A1:H1", "'주소록'!A2:E"])
        self.assertTrue(
            all(call["valueRenderOption"] == "FORMATTED_VALUE" for call in service.resource.value_resource.calls)
        )

    def test_missing_named_tab_never_falls_back_to_first_tab(self) -> None:
        service = FakeSheetsService(
            metadata_with_tabs("Sheet1"),
            list(contacts_sync.EXPECTED_HEADERS),
            [],
        )
        with self.assertRaises(ContactsSheetError) as context:
            sheet_client.read_contacts_sheet(
                service, ContactsSettings("SPREADSHEET-1", "주소록")
            )
        self.assertEqual(context.exception.code, "CONTACTS_TAB_NOT_FOUND")
        self.assertEqual(service.resource.value_resource.calls, [])

    def test_scope_token_and_paths_are_isolated_and_absolute(self) -> None:
        self.assertEqual(
            sheet_client.SCOPES,
            ["https://www.googleapis.com/auth/spreadsheets.readonly"],
        )
        self.assertEqual(sheet_client.TOKEN_FILE.name, "contacts_sheet_token.json")
        self.assertTrue(sheet_client.TOKEN_FILE.is_absolute())
        self.assertNotIn(sheet_client.TOKEN_FILE.name, {
            "token.json",
            "drive_download_token.json",
            "gmail_send_token.json",
            "drive_share_token.json",
        })
        source = Path(sheet_client.__file__).read_text(encoding="utf-8")
        for forbidden in ("values().update", "values().append", "batchUpdate", "permissions().create"):
            self.assertNotIn(forbidden, source)

    def test_service_disabled_error_is_classified_without_raw_message(self) -> None:
        payload = {
            "error": {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "SERVICE_DISABLED",
                    }
                ]
            }
        }
        error = HttpError(
            SimpleNamespace(status=403, reason="Forbidden"),
            json.dumps(payload).encode("utf-8"),
        )
        self.assertEqual(
            sheet_client._http_error_code(
                error, not_found_code="CONTACTS_SPREADSHEET_NOT_FOUND"
            ),
            "CONTACTS_SHEETS_API_DISABLED",
        )


class ContactsSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.database_path = root / "drive_index.db"
        self.log_path = root / "contacts_sync.log"
        self.connection = connect_database(self.database_path)
        initialize_schema(self.connection)
        self.settings = ContactsSettings("SPREADSHEET-1", "주소록")
        self.sync_number = 0

    def tearDown(self) -> None:
        self.connection.close()
        logger = contacts_sync.logging.getLogger(contacts_sync.LOGGER_NAME)
        for handler in logger.handlers:
            handler.close()
        logger.handlers.clear()
        self.temporary_directory.cleanup()

    def snapshot(self, rows, header=None) -> ContactsSheetSnapshot:
        return ContactsSheetSnapshot(
            spreadsheet_id="SPREADSHEET-1",
            spreadsheet_title="Address Book",
            sheet_id=11,
            sheet_name="주소록",
            header=tuple(header or contacts_sync.EXPECTED_HEADERS),
            rows=tuple(tuple(row) for row in rows),
        )

    def sync(self, rows, header=None):
        self.sync_number += 1
        sync_id = f"CONTACTS-TEST-{self.sync_number:04d}"
        contacts_sync.start_contacts_sync(
            self.connection, sync_id, contacts_sync.utc_text(), self.settings
        )
        snapshot = self.snapshot(rows, header)
        contacts, issues, statistics = contacts_sync.stage_snapshot(snapshot)
        contacts_sync.apply_contacts_snapshot(
            self.connection,
            snapshot,
            sync_id,
            contacts,
            issues,
            statistics,
        )
        return sync_id, statistics

    def contact_rows(self) -> list[dict]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM contacts ORDER BY source_row, contact_id"
            )
        ]

    def test_exact_header_succeeds_and_mismatch_preserves_existing_contacts(self) -> None:
        self.sync([["HLB", "홍길동", "부장", "hong@example.com", "010"]])
        before = self.contact_rows()
        bad_snapshot = self.snapshot(
            [["HLB", "김철수", "과장", "kim@example.com", "011"]],
            header=("성명", "소속", "직급", "이메일", "전화번호"),
        )

        with self.assertRaises(contacts_sync.ContactsSyncError) as context:
            contacts_sync.stage_snapshot(bad_snapshot)

        self.assertEqual(context.exception.code, "CONTACTS_HEADER_MISMATCH")
        self.assertEqual(self.contact_rows(), before)

    def test_blank_rows_normalization_casefold_and_phone_zero_preservation(self) -> None:
        _, statistics = self.sync(
            [
                [],
                ["  HLB   Korea  ", "  홍길동   부장 ", "  부장 ", "Hong@Example.COM", "010-0123-4567"],
                ["", "", "", "", ""],
            ]
        )
        row = self.contact_rows()[0]

        self.assertEqual(statistics.rows_seen, 1)
        self.assertEqual(row["organization"], "HLB   Korea")
        self.assertEqual(row["normalized_organization"], "hlb korea")
        self.assertEqual(row["normalized_name"], "홍길동 부장")
        self.assertEqual(row["normalized_email"], "hong@example.com")
        self.assertEqual(row["phone"], "010-0123-4567")
        self.assertEqual(row["email_usable"], 1)

    def test_row_move_preserves_contact_id_and_updates_source_row(self) -> None:
        self.sync([["HLB", "홍길동", "부장", "hong@example.com", "010"]])
        before = self.contact_rows()[0]
        _, statistics = self.sync(
            [[], ["HLB", "홍길동", "부장", "HONG@example.com", "010"]]
        )
        after = self.contact_rows()[0]

        self.assertEqual(after["contact_id"], before["contact_id"])
        self.assertEqual(after["source_row"], 3)
        self.assertEqual(statistics.updated, 1)
        self.assertEqual(statistics.inserted, 0)
        self.assertEqual(statistics.deleted, 0)

    def test_email_change_creates_new_identity_and_deletes_old_contact(self) -> None:
        self.sync([["HLB", "홍길동", "부장", "old@example.com", "010"]])
        old_id = self.contact_rows()[0]["contact_id"]
        _, statistics = self.sync(
            [["HLB", "홍길동", "부장", "new@example.com", "010"]]
        )
        new_id = self.contact_rows()[0]["contact_id"]

        self.assertNotEqual(new_id, old_id)
        self.assertEqual(statistics.inserted, 1)
        self.assertEqual(statistics.deleted, 1)

    def test_duplicate_email_marks_all_conflicted_and_unusable(self) -> None:
        sync_id, statistics = self.sync(
            [
                ["A", "홍길동", "부장", "same@example.com", "010"],
                ["B", "김철수", "과장", "SAME@example.com", "011"],
            ]
        )
        rows = self.contact_rows()
        state = self.connection.execute(
            "SELECT status FROM contacts_sync_state WHERE sync_id = ?", (sync_id,)
        ).fetchone()[0]

        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["conflict_code"] == "DUPLICATE_EMAIL" for row in rows))
        self.assertTrue(all(row["email_usable"] == 0 for row in rows))
        self.assertEqual(statistics.conflicts, 2)
        self.assertEqual(state, "COMPLETED_WITH_WARNINGS")

    def test_same_name_title_and_organization_with_different_email_is_allowed(self) -> None:
        self.sync(
            [
                ["HLB", "홍길동", "부장", "one@example.com", "010"],
                ["HLB", "홍길동", "부장", "two@example.com", "011"],
            ]
        )
        rows = self.contact_rows()
        self.assertEqual(len({row["contact_id"] for row in rows}), 2)
        self.assertTrue(all(row["email_usable"] == 1 for row in rows))
        self.assertTrue(all(row["conflict_code"] is None for row in rows))

    def test_missing_and_invalid_email_rows_are_preserved_but_unusable(self) -> None:
        _, statistics = self.sync(
            [
                ["HLB", "홍길동", "부장", "", "010"],
                ["HLB", "김철수", "과장", "Name <bad@example.com>", "011"],
            ]
        )
        rows = self.contact_rows()
        self.assertEqual(
            {row["conflict_code"] for row in rows},
            {"EMAIL_MISSING", "EMAIL_INVALID"},
        )
        self.assertTrue(all(row["email_usable"] == 0 for row in rows))
        self.assertEqual(statistics.invalid, 2)

    def test_duplicate_rows_are_both_preserved_and_unusable(self) -> None:
        row = ["HLB", "홍길동", "부장", "same@example.com", "010"]
        _, statistics = self.sync([row, list(row)])
        rows = self.contact_rows()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(item["conflict_code"] == "DUPLICATE_ROW" for item in rows))
        self.assertTrue(all(item["email_usable"] == 0 for item in rows))
        self.assertEqual(statistics.conflicts, 2)

    def test_complete_snapshot_hard_deletes_unseen_contact(self) -> None:
        self.sync(
            [
                ["HLB", "홍길동", "부장", "hong@example.com", "010"],
                ["HLB", "김철수", "과장", "kim@example.com", "011"],
            ]
        )
        _, statistics = self.sync(
            [["HLB", "홍길동", "부장", "hong@example.com", "010"]]
        )
        self.assertEqual(len(self.contact_rows()), 1)
        self.assertEqual(statistics.deleted, 1)

    def test_transaction_failure_rolls_back_delete_and_insert(self) -> None:
        self.sync([["HLB", "기존", "부장", "old@example.com", "010"]])
        before = self.contact_rows()
        sync_id = "CONTACTS-ROLLBACK"
        contacts_sync.start_contacts_sync(
            self.connection, sync_id, contacts_sync.utc_text(), self.settings
        )
        snapshot = self.snapshot(
            [
                ["A", "신규1", "과장", "one@example.com", "010"],
                ["B", "신규2", "대리", "two@example.com", "011"],
            ]
        )
        staged, issues, statistics = contacts_sync.stage_snapshot(snapshot)

        with patch.object(contacts_sync.uuid, "uuid4", return_value="SAME-ID"):
            with self.assertRaises(sqlite3.IntegrityError):
                contacts_sync.apply_contacts_snapshot(
                    self.connection,
                    snapshot,
                    sync_id,
                    staged,
                    issues,
                    statistics,
                )

        self.assertEqual(self.contact_rows(), before)
        state = self.connection.execute(
            "SELECT status FROM contacts_sync_state WHERE sync_id = ?", (sync_id,)
        ).fetchone()[0]
        self.assertEqual(state, "RUNNING")

    def test_sync_state_statistics_and_issue_messages_contain_no_pii(self) -> None:
        sync_id, statistics = self.sync(
            [["HLB", "홍길동", "부장", "", "010-1234-5678"]]
        )
        state = dict(
            self.connection.execute(
                "SELECT * FROM contacts_sync_state WHERE sync_id = ?", (sync_id,)
            ).fetchone()
        )
        issue = dict(
            self.connection.execute(
                "SELECT * FROM contacts_sync_issues WHERE sync_id = ?", (sync_id,)
            ).fetchone()
        )

        self.assertEqual(state["rows_seen"], statistics.rows_seen)
        self.assertEqual(state["valid_rows"], 1)
        self.assertEqual(state["invalid"], 1)
        self.assertEqual(issue["message"], "Row 2 has EMAIL_MISSING")
        serialized = json.dumps(issue, ensure_ascii=False)
        self.assertNotIn("홍길동", serialized)
        self.assertNotIn("010-1234-5678", serialized)

    def test_default_database_and_token_paths_ignore_working_directory(self) -> None:
        original = Path.cwd()
        other = Path(self.temporary_directory.name) / "other-cwd"
        other.mkdir()
        try:
            os.chdir(other)
            database_path = DATABASE_PATH.resolve()
            token_path = sheet_client.TOKEN_FILE.resolve()
        finally:
            os.chdir(original)

        project_root = Path(contacts_sync.__file__).resolve().parent
        self.assertEqual(database_path, project_root / "data" / "drive_index.db")
        self.assertEqual(token_path, project_root / "contacts_sheet_token.json")

    def test_contacts_lock_rejects_overlapping_sync_without_waiting(self) -> None:
        lock_path = Path(self.temporary_directory.name) / "contacts.lock"

        with contacts_sync.contacts_sync_lock(lock_path):
            with self.assertRaises(contacts_sync.ContactsSyncError) as raised:
                with contacts_sync.contacts_sync_lock(lock_path):
                    self.fail("The second contacts lock must not be acquired.")

        self.assertEqual(
            raised.exception.code,
            "CONTACTS_SYNC_ALREADY_RUNNING",
        )

    def test_overlapping_run_fails_before_settings_database_or_oauth(self) -> None:
        lock_path = Path(self.temporary_directory.name) / "contacts-run.lock"
        logger = contacts_sync.create_logger(
            Path(self.temporary_directory.name) / "contacts-run.log"
        )

        with contacts_sync.contacts_sync_lock(lock_path):
            with patch.object(
                contacts_sync, "load_contacts_settings"
            ) as load_settings, patch.object(
                contacts_sync, "connect_database"
            ) as connect, patch.object(
                contacts_sync, "build_contacts_sheets_service"
            ) as build_service:
                result = contacts_sync.execute_contacts_sync(
                    logger,
                    database_path=self.database_path,
                    lock_path=lock_path,
                )

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.error_code, "CONTACTS_SYNC_ALREADY_RUNNING")
        load_settings.assert_not_called()
        connect.assert_not_called()
        build_service.assert_not_called()

    def test_run_logs_only_counts_and_error_codes(self) -> None:
        logger = contacts_sync.create_logger(self.log_path)
        snapshot = self.snapshot(
            [["HLB", "홍길동", "부장", "hong@example.com", "010-1234-5678"]]
        )
        with patch.object(
            contacts_sync, "load_contacts_settings", return_value=self.settings
        ), patch.object(
            contacts_sync, "build_contacts_sheets_service", return_value=object()
        ), patch.object(
            contacts_sync, "read_contacts_sheet", return_value=snapshot
        ), patch.object(
            contacts_sync, "generate_sync_id", return_value="CONTACTS-LOG-TEST"
        ):
            result = contacts_sync.run_contacts_sync(
                logger, database_path=self.database_path
            )

        self.assertEqual(result, 0)
        text = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn("홍길동", text)
        self.assertNotIn("hong@example.com", text)
        self.assertNotIn("010-1234-5678", text)
        self.assertNotIn("SPREADSHEET-1", text)


if __name__ == "__main__":
    unittest.main()
