import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

import api_server
from database import connect_database, initialize_schema
from test_search_service import seed_database


TABLES = (
    "files",
    "folders",
    "scan_state",
    "file_groups",
    "file_group_members",
)


def database_hash(path: Path) -> str:
    connection = connect_database(path, read_only=True)
    data = []
    for table in TABLES:
        rows = connection.execute(
            f"SELECT * FROM {table} ORDER BY rowid"
        ).fetchall()
        data.append([table, [list(row) for row in rows]])
    connection.close()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ApiServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.api_key = secrets.token_urlsafe(48)
        cls.environment_patcher = patch.dict(
            os.environ,
            {api_server.API_KEY_ENVIRONMENT_VARIABLE: cls.api_key},
        )
        cls.environment_patcher.start()
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.database_path = Path(cls.temporary_directory.name) / "api.db"
        connection = connect_database(cls.database_path)
        initialize_schema(connection)
        seed_database(connection)
        connection.execute(
            """
            INSERT INTO scan_state (
                scan_id, status, started_at, finished_at, files_seen,
                folders_seen, scope_type, message, created_at
            ) VALUES (
                'SCAN-API', 'COMPLETED', '2026-01-01T00:00:00Z',
                '2026-01-01T00:01:00Z', 6, 10, 'USER_DRIVE', NULL,
                '2026-01-01T00:00:00Z'
            )
            """
        )
        connection.commit()
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
    def authorization_headers(cls) -> dict[str, str]:
        return {"Authorization": f"Bearer {cls.api_key}"}

    def authorized_get(self, path: str, parameters: dict | None = None):
        return self.client.get(
            path,
            params=parameters,
            headers=self.authorization_headers(),
        )

    def test_health_and_status(self) -> None:
        health = self.client.get("/health")
        status = self.authorized_get("/status")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(
            health.json(),
            {"status": "ok", "service": "python-drive-organizer"},
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(
            status.json(),
            {
                "files_count": 6,
                "folders_count": 10,
                "groups_count": 4,
                "auto_delete_count": 1,
                "latest_scan_id": "SCAN-API",
                "latest_scan_status": "COMPLETED",
            },
        )

    def test_file_and_folder_search(self) -> None:
        files = self.authorized_get(
            "/files/search", {"q": "ABC", "limit": 2}
        )
        folders = self.authorized_get(
            "/folders/search", {"q": "same", "limit": 10}
        )

        self.assertEqual(files.status_code, 200)
        self.assertEqual(files.json()["query"], "ABC")
        self.assertEqual(files.json()["total"], 3)
        self.assertEqual(files.json()["showing"], 2)
        self.assertIn("path", files.json()["items"][0])
        self.assertEqual(folders.status_code, 200)
        self.assertEqual(folders.json()["total"], 2)
        self.assertEqual(
            {item["folder_id"] for item in folders.json()["items"]},
            {"S1", "S2"},
        )

    def test_folder_children_and_missing_folder(self) -> None:
        direct = self.authorized_get("/folders/A/children")
        recursive = self.authorized_get(
            "/folders/A/children",
            {"recursive": "true", "limit": 100},
        )
        missing = self.authorized_get("/folders/DOES-NOT-EXIST/children")

        self.assertEqual(direct.status_code, 200)
        self.assertFalse(direct.json()["recursive"])
        self.assertEqual(direct.json()["total"], 4)
        self.assertEqual(recursive.status_code, 200)
        self.assertGreater(recursive.json()["total"], direct.json()["total"])
        self.assertEqual(missing.status_code, 404)
        self.assertIn("detail", missing.json())

    def test_tree_root_depth_and_files(self) -> None:
        tree = self.authorized_get(
            "/folders/tree",
            {
                "root_folder": "A",
                "max_depth": 1,
                "include_files": "true",
            },
        )
        missing = self.authorized_get(
            "/folders/tree", {"root_folder": "UNKNOWN"}
        )

        self.assertEqual(tree.status_code, 200)
        self.assertEqual(tree.json()["folder_count"], 4)
        self.assertEqual(tree.json()["file_count"], 1)
        self.assertIn("[FILE] Alpha Plan.pdf", tree.json()["tree_text"])
        self.assertEqual(missing.status_code, 404)

    def test_revision_copy_auto_delete_group_and_recent(self) -> None:
        revisions = self.authorized_get("/revisions", {"limit": 10})
        copies = self.authorized_get("/copies", {"limit": 10})
        auto_delete = self.authorized_get("/auto-delete", {"limit": 10})
        groups = self.authorized_get(
            "/groups", {"min_members": 2, "limit": 10}
        )
        recent = self.authorized_get("/recent", {"limit": 3})

        self.assertEqual(revisions.json()["total"], 1)
        self.assertEqual(revisions.json()["items"][0]["revision_number"], 1)
        self.assertEqual(copies.json()["total"], 2)
        self.assertEqual(auto_delete.json()["total"], 1)
        self.assertTrue(auto_delete.json()["classification_only"])
        self.assertFalse(auto_delete.json()["drive_action_executed"])
        self.assertEqual(groups.json()["total"], 1)
        self.assertEqual(len(groups.json()["items"][0]["members"]), 3)
        self.assertEqual(recent.json()["showing"], 3)
        self.assertEqual(recent.json()["items"][0]["file_id"], "f6")

    def test_limit_and_query_validation(self) -> None:
        self.assertEqual(
            self.authorized_get(
                "/files/search", {"q": "ABC", "limit": 0}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.authorized_get(
                "/files/search", {"q": "ABC", "limit": 1001}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.authorized_get(
                "/files/search", {"q": "   ", "limit": 10}
            ).status_code,
            422,
        )

    def test_openapi_and_docs(self) -> None:
        openapi = self.client.get("/openapi.json")
        docs = self.client.get("/docs")

        self.assertEqual(openapi.status_code, 200)
        self.assertEqual(docs.status_code, 200)
        schema = openapi.json()
        self.assertEqual(schema["info"]["title"], api_server.API_TITLE)
        self.assertIn("/files/search", schema["paths"])
        self.assertIn("/folders/tree", schema["paths"])
        self.assertTrue(
            schema["paths"]["/auto-delete"]["get"]["summary"]
        )
        security_schemes = schema["components"]["securitySchemes"]
        self.assertEqual(security_schemes["BearerAuth"]["type"], "http")
        self.assertEqual(security_schemes["BearerAuth"]["scheme"], "bearer")
        self.assertNotIn("security", schema["paths"]["/health"]["get"])
        self.assertEqual(
            schema["paths"]["/status"]["get"]["security"],
            [{"BearerAuth": []}],
        )

    def test_missing_database_returns_json_503_without_traceback(self) -> None:
        missing_path = Path(self.temporary_directory.name) / "missing.db"
        with patch.object(api_server, "DATABASE_PATH", missing_path):
            response = self.authorized_get("/status")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"detail": "SQLite index is unavailable."}
        )
        self.assertNotIn("traceback", response.text.lower())

    def test_unreadable_schema_returns_json_503_without_traceback(self) -> None:
        invalid_path = Path(self.temporary_directory.name) / "invalid-schema.db"
        sqlite3.connect(invalid_path).close()
        with patch.object(api_server, "DATABASE_PATH", invalid_path):
            response = self.authorized_get("/status")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"detail": "SQLite index could not be read."}
        )
        self.assertNotIn("traceback", response.text.lower())

    def test_api_is_read_only_and_never_authenticates(self) -> None:
        before = database_hash(self.database_path)
        with patch(
            "drive_client.authenticate",
            side_effect=AssertionError("Drive authentication must not run"),
        ) as authenticate:
            endpoints = (
                ("/status", None),
                ("/files/search", {"q": "ABC", "limit": 5}),
                ("/folders/search", {"q": "Same", "limit": 5}),
                ("/folders/A/children", {"recursive": "true"}),
                ("/folders/tree", {"max_depth": 2}),
                ("/revisions", {"limit": 5}),
                ("/copies", {"limit": 5}),
                ("/auto-delete", {"limit": 5}),
                ("/groups", {"min_members": 2, "limit": 5}),
                ("/recent", {"limit": 5}),
            )
            for endpoint, parameters in endpoints:
                response = self.authorized_get(endpoint, parameters)
                self.assertEqual(response.status_code, 200, endpoint)
            authenticate.assert_not_called()

        self.assertEqual(before, database_hash(self.database_path))

    def test_protected_endpoints_require_valid_bearer_authentication(self) -> None:
        protected_endpoints = (
            "/status",
            "/files/search?q=ABC",
            "/folders/search?q=Same",
            "/folders/A/children",
            "/folders/tree",
            "/revisions",
            "/copies",
            "/auto-delete",
            "/groups",
            "/recent",
        )
        for endpoint in protected_endpoints:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn(self.api_key, response.text)

        wrong_scheme = self.client.get(
            "/status", headers={"Authorization": "Basic not-a-bearer-token"}
        )
        wrong_key = self.client.get(
            "/status", headers={"Authorization": "Bearer incorrect-key"}
        )
        valid = self.authorized_get("/status")

        self.assertEqual(wrong_scheme.status_code, 401)
        self.assertEqual(wrong_key.status_code, 401)
        self.assertEqual(valid.status_code, 200)
        self.assertNotIn(self.api_key, wrong_scheme.text)
        self.assertNotIn(self.api_key, wrong_key.text)
        self.assertNotIn(self.api_key, valid.text)

    def test_missing_environment_key_fails_application_startup(self) -> None:
        with (
            patch.dict(
                os.environ,
                {api_server.API_KEY_ENVIRONMENT_VARIABLE: ""},
            ),
            patch.object(api_server, "load_dotenv", return_value=False),
        ):
            with self.assertRaisesRegex(RuntimeError, "PDO_API_KEY"):
                with TestClient(api_server.app):
                    pass


if __name__ == "__main__":
    unittest.main()
