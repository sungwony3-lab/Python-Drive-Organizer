import hashlib
import io
import json
import logging
import os
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

import api_server
import contacts_sheet_client
from contacts_service import ContactsService
from database import connect_database, initialize_schema


CONTACT_ROWS = (
    (
        "CONTACT-001",
        "Beta",
        "홍길동",
        "과장",
        "hong.beta@example.com",
        "010-0000-0001",
        "beta",
        "홍길동",
        "과장",
        "hong.beta@example.com",
        1,
        None,
        2,
    ),
    (
        "CONTACT-002",
        "Alpha",
        "홍길동",
        "부장",
        "hong.alpha@example.com",
        "010-0000-0002",
        "alpha",
        "홍길동",
        "부장",
        "hong.alpha@example.com",
        1,
        None,
        3,
    ),
    (
        "CONTACT-003",
        "Alpha",
        "홍길동 과장",
        "대리",
        "prefix@example.com",
        "010-0000-0003",
        "alpha",
        "홍길동 과장",
        "대리",
        "prefix@example.com",
        1,
        None,
        4,
    ),
    (
        "CONTACT-004",
        "Gamma",
        "김홍길동",
        "사원",
        "contains@example.com",
        "010-0000-0004",
        "gamma",
        "김홍길동",
        "사원",
        "contains@example.com",
        1,
        None,
        5,
    ),
    (
        "CONTACT-005",
        "Email Org",
        "Email Person",
        "Director",
        "person@example.com",
        None,
        "email org",
        "email person",
        "director",
        "person@example.com",
        1,
        None,
        6,
    ),
    (
        "CONTACT-006",
        "Conflict Org",
        "발송불가",
        "담당",
        None,
        "010-0000-0006",
        "conflict org",
        "발송불가",
        "담당",
        None,
        0,
        "EMAIL_MISSING",
        7,
    ),
    (
        "CONTACT-007",
        "Conflict Org",
        "중복연락처",
        "담당",
        "duplicate@example.com",
        None,
        "conflict org",
        "중복연락처",
        "담당",
        "duplicate@example.com",
        0,
        "DUPLICATE_EMAIL",
        8,
    ),
)


def seed_contacts(connection) -> None:
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
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            'TEST-SHEET', 1, '주소록', ?, 'TEST-FINGERPRINT',
            'CONTACTS-SUCCESS', '2026-08-17T00:01:00+00:00',
            '2026-08-17T00:00:00+00:00', '2026-08-17T00:01:00+00:00'
        )
        """,
        CONTACT_ROWS,
    )
    connection.execute(
        """
        INSERT INTO contacts_sync_state (
            sync_id, status, started_at, finished_at, source_sheet_name,
            rows_seen, valid_rows, inserted, updated, deleted, unchanged,
            invalid, conflicts, created_at
        ) VALUES (
            'CONTACTS-SUCCESS', 'COMPLETED',
            '2026-08-17T00:00:00+00:00', '2026-08-17T00:01:00+00:00',
            '주소록', 7, 7, 7, 0, 0, 0, 0, 0,
            '2026-08-17T00:00:00+00:00'
        )
        """
    )
    connection.commit()


def contacts_database_hash(path: Path) -> str:
    connection = connect_database(path, read_only=True)
    data = []
    for table in ("contacts", "contacts_sync_state", "contacts_sync_issues"):
        rows = connection.execute(
            f"SELECT * FROM {table} ORDER BY rowid"
        ).fetchall()
        data.append([table, [list(row) for row in rows]])
    connection.close()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ContactsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "contacts.db"
        self.connection = connect_database(self.database_path)
        initialize_schema(self.connection)
        seed_contacts(self.connection)
        self.service = ContactsService(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def test_name_exact_prefix_contains_ranking_and_determinism(self) -> None:
        result = self.service.search(q="  홍길동  ", limit=100)

        self.assertEqual(result.total, 4)
        self.assertEqual(
            [item["contact_id"] for item in result.items],
            ["CONTACT-002", "CONTACT-001", "CONTACT-003", "CONTACT-004"],
        )

    def test_exact_filters_and_q_are_combined_with_and(self) -> None:
        organization = self.service.search(organization=" ALPHA ")
        title = self.service.search(title="부장")
        combined = self.service.search(q="홍길동", organization="Beta")

        self.assertEqual(organization.total, 2)
        self.assertEqual(title.items[0]["contact_id"], "CONTACT-002")
        self.assertEqual(
            [item["contact_id"] for item in combined.items],
            ["CONTACT-001"],
        )

    def test_email_exact_is_case_insensitive(self) -> None:
        result = self.service.search(email=" PERSON@EXAMPLE.COM ")
        q_result = self.service.search(q="PERSON@EXAMPLE.COM")

        self.assertEqual(result.total, 1)
        self.assertEqual(result.items[0]["contact_id"], "CONTACT-005")
        self.assertEqual(q_result.items[0]["contact_id"], "CONTACT-005")

    def test_zero_limit_and_public_fields(self) -> None:
        zero = self.service.search(q="not-found")
        limited = self.service.search(organization="Alpha", limit=1)

        self.assertEqual(zero.total, 0)
        self.assertEqual(zero.items, [])
        self.assertEqual(limited.total, 2)
        self.assertEqual(limited.showing, 1)
        self.assertEqual(
            set(limited.items[0]),
            {
                "contact_id",
                "organization",
                "name",
                "title",
                "email",
                "phone",
                "email_usable",
                "conflict_code",
            },
        )

    def test_unusable_and_conflict_contacts_remain_visible(self) -> None:
        missing = self.service.search(name="발송불가")
        duplicate = self.service.search(name="중복연락처")

        self.assertFalse(missing.items[0]["email_usable"])
        self.assertEqual(missing.items[0]["conflict_code"], "EMAIL_MISSING")
        self.assertFalse(duplicate.items[0]["email_usable"])
        self.assertEqual(
            duplicate.items[0]["conflict_code"], "DUPLICATE_EMAIL"
        )

    def test_exact_contact_id_has_no_name_fallback(self) -> None:
        self.assertEqual(
            self.service.get_contact("CONTACT-001")["name"],
            "홍길동",
        )
        self.assertIsNone(self.service.get_contact("홍길동"))

    def test_latest_failed_keeps_last_success_time(self) -> None:
        self.connection.execute(
            """
            INSERT INTO contacts_sync_state (
                sync_id, status, started_at, finished_at, rows_seen,
                valid_rows, inserted, updated, deleted, unchanged, invalid,
                conflicts, message, created_at
            ) VALUES (
                'CONTACTS-FAILED', 'FAILED',
                '2026-08-17T01:00:00+00:00', '2026-08-17T01:01:00+00:00',
                0, 0, 0, 0, 0, 0, 0, 0, 'CONTACTS_READ_FAILED',
                '2026-08-17T01:00:00+00:00'
            )
            """
        )
        self.connection.commit()

        result = self.service.status()

        self.assertEqual(result["latest_sync_status"], "FAILED")
        self.assertEqual(result["latest_sync_id"], "CONTACTS-FAILED")
        self.assertEqual(
            result["last_success_at"], "2026-08-17T00:01:00+00:00"
        )

    def test_latest_warning_counts_as_success(self) -> None:
        self.connection.execute(
            """
            INSERT INTO contacts_sync_state (
                sync_id, status, started_at, finished_at, rows_seen,
                valid_rows, inserted, updated, deleted, unchanged, invalid,
                conflicts, created_at
            ) VALUES (
                'CONTACTS-WARNING', 'COMPLETED_WITH_WARNINGS',
                '2026-08-17T02:00:00+00:00', '2026-08-17T02:01:00+00:00',
                8, 8, 1, 2, 3, 2, 1, 1,
                '2026-08-17T02:00:00+00:00'
            )
            """
        )
        self.connection.commit()

        result = self.service.status()

        self.assertEqual(result["latest_sync_status"], "COMPLETED_WITH_WARNINGS")
        self.assertEqual(result["last_success_at"], "2026-08-17T02:01:00+00:00")
        self.assertEqual(result["rows_seen"], 8)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["updated"], 2)
        self.assertEqual(result["deleted"], 3)
        self.assertEqual(result["unchanged"], 2)
        self.assertEqual(result["invalid"], 1)
        self.assertEqual(result["conflicts"], 1)

    def test_status_without_any_sync_is_empty(self) -> None:
        self.connection.execute("DELETE FROM contacts_sync_state")
        self.connection.commit()

        result = self.service.status()

        self.assertIsNone(result["latest_sync_id"])
        self.assertIsNone(result["latest_sync_status"])
        self.assertIsNone(result["last_success_at"])
        self.assertEqual(result["rows_seen"], 0)


class ContactsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.api_key = secrets.token_urlsafe(48)
        cls.environment_patcher = patch.dict(
            os.environ,
            {api_server.API_KEY_ENVIRONMENT_VARIABLE: cls.api_key},
        )
        cls.environment_patcher.start()
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.database_path = Path(cls.temporary_directory.name) / "contacts-api.db"
        connection = connect_database(cls.database_path)
        initialize_schema(connection)
        seed_contacts(connection)
        connection.close()
        cls.original_database_path = api_server.DATABASE_PATH
        api_server.DATABASE_PATH = cls.database_path
        cls.client = TestClient(api_server.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        api_server.DATABASE_PATH = cls.original_database_path
        cls.temporary_directory.cleanup()
        cls.environment_patcher.stop()

    @classmethod
    def headers(cls) -> dict[str, str]:
        return {"Authorization": f"Bearer {cls.api_key}"}

    def post_search(self, payload: dict, headers: dict | None = None):
        return self.client.post(
            "/contacts/search",
            json=payload,
            headers=self.headers() if headers is None else headers,
        )

    def test_all_contacts_routes_require_bearer(self) -> None:
        requests = (
            self.client.post("/contacts/search", json={"q": "홍길동"}),
            self.client.get("/contacts/status"),
            self.client.get("/contacts/CONTACT-001"),
        )
        for response in requests:
            self.assertEqual(response.status_code, 401)

        wrong = self.post_search(
            {"q": "홍길동"},
            {"Authorization": "Bearer wrong-key"},
        )
        valid = self.post_search({"q": "홍길동"})
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(valid.status_code, 200)

    def test_search_request_validation_and_limit(self) -> None:
        self.assertEqual(self.post_search({}).status_code, 400)
        self.assertEqual(self.post_search({"q": "   "}).status_code, 400)
        self.assertEqual(
            self.post_search({"q": "홍길동", "limit": 0}).status_code,
            422,
        )
        self.assertEqual(
            self.post_search({"q": "홍길동", "limit": 101}).status_code,
            422,
        )
        response = self.post_search({"organization": "Alpha", "limit": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 2)
        self.assertEqual(response.json()["showing"], 1)

    def test_search_and_exact_contact_responses_exclude_internal_fields(self) -> None:
        search = self.post_search({"q": "홍길동"})
        detail = self.client.get(
            "/contacts/CONTACT-001", headers=self.headers()
        )
        unknown = self.client.get(
            "/contacts/CONTACT-UNKNOWN", headers=self.headers()
        )

        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["total"], 4)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["contact_id"], "CONTACT-001")
        self.assertEqual(unknown.status_code, 404)
        serialized = json.dumps(search.json(), ensure_ascii=False)
        for internal in (
            "source_spreadsheet_id",
            "source_sheet_id",
            "source_row",
            "source_fingerprint",
            "normalized_name",
            "last_seen_sync_id",
        ):
            self.assertNotIn(internal, serialized)

    def test_status_static_route_wins_over_dynamic_contact_route(self) -> None:
        response = self.client.get("/contacts/status", headers=self.headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["latest_sync_id"], "CONTACTS-SUCCESS")
        self.assertEqual(response.json()["latest_sync_status"], "COMPLETED")
        self.assertEqual(response.json()["last_success_at"], "2026-08-17T00:01:00+00:00")
        self.assertEqual(response.json()["rows_seen"], 7)

    def test_unavailable_sqlite_returns_sanitized_503(self) -> None:
        missing = Path(self.temporary_directory.name) / "missing.db"
        with patch.object(api_server, "DATABASE_PATH", missing):
            response = self.post_search({"q": "홍길동"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "SQLite index is unavailable."},
        )
        self.assertNotIn(str(missing), response.text)

    def test_endpoints_are_sqlite_read_only_and_never_call_sheets(self) -> None:
        before = contacts_database_hash(self.database_path)
        with patch.object(
            contacts_sheet_client,
            "build_contacts_sheets_service",
            side_effect=AssertionError("Sheets API must not be called"),
        ) as sheets:
            responses = (
                self.post_search({"q": "홍길동"}),
                self.client.get("/contacts/status", headers=self.headers()),
                self.client.get(
                    "/contacts/CONTACT-001", headers=self.headers()
                ),
            )
            for response in responses:
                self.assertEqual(response.status_code, 200)
            sheets.assert_not_called()

        self.assertEqual(before, contacts_database_hash(self.database_path))

    def test_search_payload_and_results_are_not_logged(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            response = self.post_search(
                {
                    "q": "private-query",
                    "email": "private.person@example.com",
                }
            )
        finally:
            root.removeHandler(handler)
            handler.close()

        self.assertEqual(response.status_code, 200)
        log = stream.getvalue()
        self.assertNotIn("private-query", log)
        self.assertNotIn("private.person@example.com", log)


if __name__ == "__main__":
    unittest.main()
