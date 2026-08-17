import base64
import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.testclient import TestClient

import api_server
import enhanced_email_service as enhanced
import plain_email_service as plain
from database import connect_database, initialize_schema
from test_search_service import seed_database


TABLES = (
    "files",
    "folders",
    "scan_state",
    "file_groups",
    "file_group_members",
    "contacts",
    "contacts_sync_state",
    "contacts_sync_issues",
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
        cls.original_enhanced_state_path = api_server.ENHANCED_STATE_DATABASE_PATH
        cls.original_enhanced_diagnostic_path = (
            api_server.ENHANCED_DIAGNOSTIC_LOG_PATH
        )
        cls.original_plain_state_path = api_server.PLAIN_EMAIL_STATE_DATABASE_PATH
        cls.original_plain_audit_path = api_server.PLAIN_EMAIL_AUDIT_LOG_PATH
        cls.original_export_directory = api_server.EXPORT_DIRECTORY
        cls.original_export_audit_path = api_server.EXPORT_AUDIT_LOG_PATH
        api_server.DATABASE_PATH = cls.database_path
        api_server.ENHANCED_STATE_DATABASE_PATH = (
            Path(cls.temporary_directory.name) / "enhanced-email-state.db"
        )
        api_server.ENHANCED_DIAGNOSTIC_LOG_PATH = (
            Path(cls.temporary_directory.name) / "enhanced-email-debug.log"
        )
        api_server.PLAIN_EMAIL_STATE_DATABASE_PATH = (
            Path(cls.temporary_directory.name) / "plain-email-state.db"
        )
        api_server.PLAIN_EMAIL_AUDIT_LOG_PATH = (
            Path(cls.temporary_directory.name) / "plain-email-audit.log"
        )
        api_server.EXPORT_DIRECTORY = (
            Path(cls.temporary_directory.name) / "exports"
        )
        api_server.EXPORT_AUDIT_LOG_PATH = (
            Path(cls.temporary_directory.name) / "tree-export.log"
        )
        cls.client = TestClient(api_server.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        api_server.DATABASE_PATH = cls.original_database_path
        api_server.ENHANCED_STATE_DATABASE_PATH = cls.original_enhanced_state_path
        api_server.ENHANCED_DIAGNOSTIC_LOG_PATH = (
            cls.original_enhanced_diagnostic_path
        )
        api_server.PLAIN_EMAIL_STATE_DATABASE_PATH = cls.original_plain_state_path
        api_server.PLAIN_EMAIL_AUDIT_LOG_PATH = cls.original_plain_audit_path
        api_server.EXPORT_DIRECTORY = cls.original_export_directory
        api_server.EXPORT_AUDIT_LOG_PATH = cls.original_export_audit_path
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

    def authorized_post(self, path: str, payload: dict):
        return self.client.post(
            path,
            json=payload,
            headers=self.authorization_headers(),
        )

    @staticmethod
    def email_payload(**overrides) -> dict:
        payload = {
            "file_id": "f1",
            "to": "recipient@example.com",
            "subject": "Approved subject",
            "body": "Approved plain-text body",
            "confirmed": True,
            "idempotency_key": "EMAIL-API-TEST-0001",
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def enhanced_email_payload(**overrides) -> dict:
        payload = {
            "preview_id": "PREVIEW-API-TEST-0001",
            "file_ids": ["f1", "f2"],
            "to": "recipient@example.com",
            "cc": ["copy@example.com"],
            "subject": "Approved enhanced subject",
            "body": "Approved enhanced plain-text body",
            "mode": "attachment",
            "confirmed": True,
            "idempotency_key": "EMAIL-ENHANCED-API-TEST-0001",
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def text_email_payload(**overrides) -> dict:
        payload = {
            "preview_id": "TEXT-PREVIEW-API-0001",
            "to": "recipient@example.com",
            "cc": ["copy@example.com"],
            "subject": "Approved text subject",
            "body": "Approved text body",
            "confirmed": True,
            "idempotency_key": "TEXT-EMAIL-API-0001",
        }
        payload.update(overrides)
        return payload

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

    def test_tree_pagination_has_no_missing_or_duplicate_nodes(self) -> None:
        cursor = None
        collected = []
        while True:
            response = self.authorized_post(
                "/folders/tree/page",
                {
                    "root_folder": None,
                    "include_files": True,
                    "max_depth": None,
                    "page_size": 3,
                    "cursor": cursor,
                },
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            collected.extend(item["id"] for item in data["items"])
            cursor = data["next_cursor"]
            if cursor is None:
                self.assertFalse(data["has_more"])
                self.assertEqual(data["total_nodes"], 16)
                break
        self.assertEqual(len(collected), 16)
        self.assertEqual(len(collected), len(set(collected)))

    def test_tree_export_and_authenticated_download(self) -> None:
        before = database_hash(self.database_path)
        export = self.authorized_post(
            "/exports/drive-tree",
            {
                "format": "txt",
                "root_folder": None,
                "include_files": True,
                "max_depth": None,
            },
        )
        self.assertEqual(export.status_code, 200)
        data = export.json()
        self.assertEqual(data["node_count"], 16)
        self.assertEqual(data["folder_count"], 10)
        self.assertEqual(data["file_count"], 6)
        self.assertEqual(data["latest_scan_id"], "SCAN-API")

        unauthorized = self.client.get(data["download_endpoint"])
        downloaded = self.authorized_get(data["download_endpoint"])
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.headers["content-type"], "text/plain; charset=utf-8")
        self.assertIn("Google Drive Tree", downloaded.text)
        self.assertEqual(before, database_hash(self.database_path))

        openai_endpoint = f"/exports/{data['export_id']}/openai-file"
        unauthorized_openai = self.client.post(openai_endpoint)
        returned = self.authorized_post(openai_endpoint, {})
        self.assertEqual(unauthorized_openai.status_code, 401)
        self.assertEqual(returned.status_code, 200)
        item = returned.json()["openaiFileResponse"][0]
        self.assertEqual(item["name"], data["filename"])
        self.assertEqual(item["mime_type"], "text/plain")
        self.assertEqual(base64.b64decode(item["content"]), downloaded.content)

    def test_openai_file_errors_are_safe_and_raw_download_still_works(self) -> None:
        unknown_id = "U" * 32
        unknown = self.authorized_post(
            f"/exports/{unknown_id}/openai-file", {}
        )
        traversal = self.authorized_post(
            "/exports/%2E%2E%2Fsecret/openai-file", {}
        )
        self.assertEqual(unknown.status_code, 404)
        self.assertIn(traversal.status_code, {404, 422})

        oversized_id = "Z" * 32
        api_server.EXPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        oversized_path = (
            api_server.EXPORT_DIRECTORY
            / f"drive_tree_test_{oversized_id}.docx"
        )
        with oversized_path.open("wb") as handle:
            handle.truncate(10_000_001)
        oversized = self.authorized_post(
            f"/exports/{oversized_id}/openai-file", {}
        )
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(
            oversized.json()["detail"]["code"], "GPT_FILE_TOO_LARGE"
        )
        log_text = api_server.EXPORT_AUDIT_LOG_PATH.read_text(encoding="utf-8")
        self.assertNotIn("openaiFileResponse", log_text)
        self.assertNotIn("content", log_text)

        raw = self.authorized_get(f"/exports/{oversized_id}")
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(len(raw.content), 10_000_001)

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
        self.assertIn("/folders/tree/page", schema["paths"])
        self.assertIn("/exports/drive-tree", schema["paths"])
        self.assertIn("/exports/{export_id}", schema["paths"])
        self.assertIn("/exports/{export_id}/openai-file", schema["paths"])
        self.assertIn("/email/send-file", schema["paths"])
        self.assertIn("/email/send-text/preview", schema["paths"])
        self.assertIn("/email/send-text", schema["paths"])
        self.assertIn("/email/send-files/preview", schema["paths"])
        self.assertIn("/email/send-files", schema["paths"])
        self.assertIn("/contacts/search", schema["paths"])
        self.assertIn("/contacts/status", schema["paths"])
        self.assertIn("/contacts/{contact_id}", schema["paths"])
        self.assertTrue(
            schema["paths"]["/auto-delete"]["get"]["summary"]
        )
        email_operation = schema["paths"]["/email/send-file"]["post"]
        self.assertTrue(email_operation["x-openai-isConsequential"])
        preview_operation = schema["paths"]["/email/send-files/preview"]["post"]
        enhanced_send_operation = schema["paths"]["/email/send-files"]["post"]
        text_preview_operation = schema["paths"]["/email/send-text/preview"]["post"]
        text_send_operation = schema["paths"]["/email/send-text"]["post"]
        self.assertFalse(preview_operation["x-openai-isConsequential"])
        self.assertTrue(enhanced_send_operation["x-openai-isConsequential"])
        self.assertFalse(text_preview_operation["x-openai-isConsequential"])
        self.assertTrue(text_send_operation["x-openai-isConsequential"])
        self.assertFalse(
            schema["paths"]["/folders/tree/page"]["post"][
                "x-openai-isConsequential"
            ]
        )
        self.assertFalse(
            schema["paths"]["/exports/drive-tree"]["post"][
                "x-openai-isConsequential"
            ]
        )
        self.assertFalse(
            schema["paths"]["/exports/{export_id}/openai-file"]["post"][
                "x-openai-isConsequential"
            ]
        )
        security_schemes = schema["components"]["securitySchemes"]
        self.assertEqual(security_schemes["BearerAuth"]["type"], "http")
        self.assertEqual(security_schemes["BearerAuth"]["scheme"], "bearer")
        self.assertNotIn("security", schema["paths"]["/health"]["get"])
        self.assertEqual(
            schema["paths"]["/status"]["get"]["security"],
            [{"BearerAuth": []}],
        )
        protected_gets = {
            path
            for path, methods in schema["paths"].items()
            if "get" in methods and path != "/health"
        }
        self.assertEqual(
            protected_gets,
            {
                "/status",
                "/files/search",
                "/folders/search",
                "/folders/{folder_id}/children",
                "/folders/tree",
                "/revisions",
                "/copies",
                "/auto-delete",
                "/groups",
                "/recent",
                "/contacts/status",
                "/contacts/{contact_id}",
                "/exports/{export_id}",
            },
        )

    def test_email_send_requires_bearer_and_exact_confirmation(self) -> None:
        with patch.object(api_server, "build_drive_download_service") as drive:
            missing_auth = self.client.post(
                "/email/send-file", json=self.email_payload()
            )
            unconfirmed = self.authorized_post(
                "/email/send-file", self.email_payload(confirmed=False)
            )
            non_boolean = self.authorized_post(
                "/email/send-file", self.email_payload(confirmed="true")
            )

        self.assertEqual(missing_auth.status_code, 401)
        self.assertEqual(unconfirmed.status_code, 400)
        self.assertEqual(
            unconfirmed.json()["detail"]["code"],
            "CONFIRMATION_REQUIRED",
        )
        self.assertEqual(non_boolean.status_code, 422)
        drive.assert_not_called()

    def test_text_email_preview_is_read_only_and_send_requires_confirmation(self) -> None:
        preview_payload = self.text_email_payload()
        preview_payload.pop("preview_id")
        preview_payload.pop("confirmed")
        preview_payload.pop("idempotency_key")

        with patch.object(api_server, "build_drive_download_service") as drive, patch.object(
            api_server, "build_drive_share_service"
        ) as share, patch.object(api_server, "build_gmail_service") as gmail:
            missing_auth = self.client.post(
                "/email/send-text/preview", json=preview_payload
            )
            preview_response = self.authorized_post(
                "/email/send-text/preview", preview_payload
            )
            unconfirmed = self.authorized_post(
                "/email/send-text",
                self.text_email_payload(confirmed=False),
            )
            non_boolean = self.authorized_post(
                "/email/send-text",
                self.text_email_payload(confirmed="true"),
            )

        self.assertEqual(missing_auth.status_code, 401)
        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(preview_response.json()["attachment_count"], 0)
        self.assertEqual(preview_response.json()["drive_link_count"], 0)
        self.assertEqual(preview_response.json()["body"], "Approved text body")
        self.assertEqual(unconfirmed.status_code, 400)
        self.assertEqual(
            unconfirmed.json()["detail"]["code"], "CONFIRMATION_REQUIRED"
        )
        self.assertEqual(non_boolean.status_code, 422)
        drive.assert_not_called()
        share.assert_not_called()
        gmail.assert_not_called()

    def test_text_email_preview_and_send_across_separate_http_clients(self) -> None:
        preview_payload = self.text_email_payload()
        preview_payload.pop("preview_id")
        preview_payload.pop("confirmed")
        preview_payload.pop("idempotency_key")

        with patch.object(api_server, "build_drive_download_service") as drive, patch.object(
            api_server, "build_drive_share_service"
        ) as share, patch.object(
            api_server, "build_gmail_service", return_value=object()
        ) as gmail, patch.object(
            plain, "send_message", return_value="MESSAGE-TEXT-HTTP-TURN"
        ) as send:
            preview_client = TestClient(api_server.app)
            preview_response = preview_client.post(
                "/email/send-text/preview",
                json=preview_payload,
                headers=self.authorization_headers(),
            )
            preview_client.close()

            self.assertEqual(preview_response.status_code, 200)
            exact_preview_id = preview_response.json()["preview_id"]
            send_client = TestClient(api_server.app)
            send_response = send_client.post(
                "/email/send-text",
                json=self.text_email_payload(
                    preview_id=exact_preview_id,
                    idempotency_key="TEXT-SEPARATE-HTTP-TURN-1",
                ),
                headers=self.authorization_headers(),
            )
            send_client.close()

        self.assertEqual(send_response.status_code, 200)
        self.assertEqual(send_response.json()["status"], "sent")
        self.assertEqual(
            send_response.json()["message_id"], "MESSAGE-TEXT-HTTP-TURN"
        )
        self.assertEqual(send_response.json()["attachment_count"], 0)
        self.assertEqual(send_response.json()["drive_link_count"], 0)
        drive.assert_not_called()
        share.assert_not_called()
        gmail.assert_called_once_with()
        send.assert_called_once()

    def test_enhanced_preview_requires_bearer_and_has_no_write_factory(self) -> None:
        payload = self.enhanced_email_payload()
        payload.pop("preview_id")
        payload.pop("confirmed")
        payload.pop("idempotency_key")
        file = SimpleNamespace(
            file_id="f1",
            name="Alpha Plan.pdf",
            mime_type="application/pdf",
            size_bytes=10,
        )
        preview = SimpleNamespace(
            preview_id="PREVIEW-1",
            expires_at="2026-08-14T00:10:00+00:00",
            requested_mode="attachment",
            delivery_mode="attachment",
            sharing_mode="none",
            file_count=1,
            total_size_bytes=10,
            files=(file,),
            recipient="recipient@example.com",
            cc=("copy@example.com",),
            sharing_changes=(),
        )
        drive_service = object()
        with patch.object(
            api_server, "build_drive_download_service", return_value=drive_service
        ) as drive, patch.object(
            api_server, "create_enhanced_preview", return_value=preview
        ) as create, patch.object(
            api_server, "build_drive_share_service"
        ) as share, patch.object(api_server, "build_gmail_service") as gmail:
            missing_auth = self.client.post(
                "/email/send-files/preview", json=payload
            )
            response = self.authorized_post(
                "/email/send-files/preview", payload
            )

        self.assertEqual(missing_auth.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["preview_id"], "PREVIEW-1")
        self.assertEqual(response.json()["delivery_mode"], "attachment")
        self.assertEqual(response.json()["sharing_mode"], "none")
        drive.assert_called_once_with()
        create.assert_called_once_with(
            drive_service=drive_service,
            file_ids=["f1", "f2"],
            recipient="recipient@example.com",
            cc=["copy@example.com"],
            subject="Approved enhanced subject",
            body="Approved enhanced plain-text body",
            mode="attachment",
            database_path=self.database_path,
            state_database_path=api_server.ENHANCED_STATE_DATABASE_PATH,
            diagnostic_log_path=api_server.ENHANCED_DIAGNOSTIC_LOG_PATH,
        )
        share.assert_not_called()
        gmail.assert_not_called()

    def test_enhanced_send_requires_exact_confirmation_and_returns_success(self) -> None:
        result = SimpleNamespace(
            status="sent",
            delivery_mode="attachment",
            sharing_mode="none",
            file_count=2,
            files=(
                {"file_id": "f1", "name": "Alpha Plan.pdf", "delivery": "attachment"},
                {"file_id": "f2", "name": "Beta Plan.txt", "delivery": "attachment"},
            ),
            recipient="recipient@example.com",
            cc=("copy@example.com",),
            message_id="MESSAGE-ENHANCED-1",
            sharing_changes=(),
            idempotent_replay=False,
        )
        with patch.object(api_server, "send_enhanced_email", return_value=result) as send:
            unconfirmed = self.authorized_post(
                "/email/send-files",
                self.enhanced_email_payload(confirmed=False),
            )
            non_boolean = self.authorized_post(
                "/email/send-files",
                self.enhanced_email_payload(confirmed="true"),
            )
            response = self.authorized_post(
                "/email/send-files", self.enhanced_email_payload()
            )

        self.assertEqual(unconfirmed.status_code, 400)
        self.assertEqual(
            unconfirmed.json()["detail"]["code"], "CONFIRMATION_REQUIRED"
        )
        self.assertEqual(non_boolean.status_code, 422)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message_id"], "MESSAGE-ENHANCED-1")
        self.assertEqual(response.json()["file_count"], 2)
        send.assert_called_once_with(
            preview_id="PREVIEW-API-TEST-0001",
            file_ids=["f1", "f2"],
            recipient="recipient@example.com",
            cc=["copy@example.com"],
            subject="Approved enhanced subject",
            body="Approved enhanced plain-text body",
            mode="attachment",
            idempotency_key="EMAIL-ENHANCED-API-TEST-0001",
            drive_service_factory=api_server.build_drive_download_service,
            gmail_service_factory=api_server.build_gmail_service,
            share_service_factory=api_server.build_drive_share_service,
            database_path=self.database_path,
            state_database_path=api_server.ENHANCED_STATE_DATABASE_PATH,
            diagnostic_log_path=api_server.ENHANCED_DIAGNOSTIC_LOG_PATH,
        )

    def test_enhanced_preview_and_send_across_separate_http_clients(self) -> None:
        metadata = {
            "f1": {
                "id": "f1",
                "name": "ABC.pdf",
                "mimeType": "application/pdf",
                "size": "3",
                "trashed": False,
                "modifiedTime": "2026-01-01T00:00:00Z",
                "version": "1",
                "webViewLink": "https://drive.google.com/f1",
                "capabilities": {"canDownload": True, "canShare": True},
            },
            "f2": {
                "id": "f2",
                "name": "ABC R1.pdf",
                "mimeType": "application/pdf",
                "size": "4",
                "trashed": False,
                "modifiedTime": "2026-02-01T00:00:00Z",
                "version": "1",
                "webViewLink": "https://drive.google.com/f2",
                "capabilities": {"canDownload": True, "canShare": True},
            },
        }
        preview_payload = self.enhanced_email_payload()
        preview_payload.pop("preview_id")
        preview_payload.pop("confirmed")
        preview_payload.pop("idempotency_key")

        with patch.object(
            api_server, "build_drive_download_service", return_value=object()
        ), patch.object(
            enhanced,
            "get_file_metadata",
            side_effect=lambda _service, file_id: metadata[file_id],
        ), patch.object(
            enhanced,
            "download_file",
            side_effect=lambda _service, file_id, _maximum: {
                "f1": b"111",
                "f2": b"2222",
            }[file_id],
        ), patch.object(
            enhanced, "send_message", return_value="MESSAGE-HTTP-TURN"
        ), patch.object(
            api_server, "build_gmail_service", return_value=object()
        ):
            preview_client = TestClient(api_server.app)
            preview_response = preview_client.post(
                "/email/send-files/preview",
                json=preview_payload,
                headers=self.authorization_headers(),
            )
            preview_client.close()

            self.assertEqual(preview_response.status_code, 200)
            exact_preview_id = preview_response.json()["preview_id"]
            send_payload = self.enhanced_email_payload(
                preview_id=exact_preview_id,
                idempotency_key="EMAIL-SEPARATE-HTTP-TURN-1",
            )
            send_client = TestClient(api_server.app)
            send_response = send_client.post(
                "/email/send-files",
                json=send_payload,
                headers=self.authorization_headers(),
            )
            send_client.close()

        self.assertEqual(send_response.status_code, 200)
        self.assertEqual(send_response.json()["status"], "sent")
        self.assertEqual(send_response.json()["message_id"], "MESSAGE-HTTP-TURN")

    def test_enhanced_link_preview_uses_file_level_anyone_sharing(self) -> None:
        file = SimpleNamespace(
            file_id="f1",
            name="Drawing.dwg",
            mime_type="image/vnd.dwg",
            size_bytes=20_000_000,
        )
        change = SimpleNamespace(
            file_id="f1",
            action="create_anyone_reader",
            permission_type="anyone",
            role="reader",
            allow_file_discovery=False,
        )
        preview = SimpleNamespace(
            preview_id="PREVIEW-LINK-1",
            expires_at="2026-08-17T00:10:00+00:00",
            requested_mode="auto",
            delivery_mode="link",
            sharing_mode="anyone_with_link_reader",
            file_count=1,
            total_size_bytes=20_000_000,
            files=(file,),
            recipient="recipient@naver.com",
            cc=("copy@company.example",),
            sharing_changes=(change,),
        )

        response = api_server.enhanced_preview_response(preview)

        self.assertEqual(response["sharing_mode"], "anyone_with_link_reader")
        self.assertEqual(
            response["sharing_changes"],
            [
                {
                    "file_id": "f1",
                    "action": "create_anyone_reader",
                    "permission_type": "anyone",
                    "role": "reader",
                    "allow_file_discovery": False,
                }
            ],
        )
        self.assertNotIn("recipient", response["sharing_changes"][0])

    def test_email_send_reuses_service_and_returns_success(self) -> None:
        prepared = object()
        result = SimpleNamespace(
            status="sent",
            message_id="MESSAGE-1",
            file_id="f1",
            file_name="Alpha Plan.pdf",
            recipient="recipient@example.com",
            idempotent_replay=False,
        )
        drive_service = object()
        with patch.object(
            api_server,
            "build_drive_download_service",
            return_value=drive_service,
        ), patch.object(
            api_server, "prepare_email_file", return_value=prepared
        ) as prepare, patch.object(
            api_server, "build_gmail_service"
        ) as gmail_factory, patch.object(
            api_server, "send_prepared_email", return_value=result
        ) as send:
            response = self.authorized_post(
                "/email/send-file", self.email_payload()
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "sent",
                "message_id": "MESSAGE-1",
                "file_id": "f1",
                "file_name": "Alpha Plan.pdf",
                "recipient": "recipient@example.com",
                "idempotent_replay": False,
            },
        )
        prepare.assert_called_once_with(
            drive_service=drive_service,
            file_id="f1",
            recipient="recipient@example.com",
            subject="Approved subject",
            body="Approved plain-text body",
            idempotency_key="EMAIL-API-TEST-0001",
            database_path=self.database_path,
        )
        send.assert_called_once_with(
            prepared=prepared,
            drive_service=drive_service,
            gmail_service_factory=gmail_factory,
        )
        gmail_factory.assert_not_called()

    def test_email_idempotent_success_is_returned_without_api_duplication(self) -> None:
        result = SimpleNamespace(
            status="sent",
            message_id="EXISTING-MESSAGE",
            file_id="f1",
            file_name="Alpha Plan.pdf",
            recipient="recipient@example.com",
            idempotent_replay=True,
        )
        with patch.object(
            api_server, "build_drive_download_service", return_value=object()
        ), patch.object(
            api_server, "prepare_email_file", return_value=object()
        ), patch.object(
            api_server, "send_prepared_email", return_value=result
        ):
            response = self.authorized_post(
                "/email/send-file", self.email_payload()
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["idempotent_replay"])
        self.assertEqual(response.json()["message_id"], "EXISTING-MESSAGE")

    def test_email_service_error_codes_map_to_http_statuses(self) -> None:
        cases = {
            "INVALID_RECIPIENT": 400,
            "UNSUPPORTED_NATIVE_FILE": 400,
            "ATTACHMENT_TOO_LARGE": 400,
            "FILE_NOT_INDEXED": 404,
            "DRIVE_FILE_NOT_FOUND": 404,
            "IDEMPOTENCY_CONFLICT": 409,
            "IDEMPOTENCY_IN_PROGRESS": 409,
            "GMAIL_DELIVERY_UNCERTAIN": 409,
            "LINK_PERMISSION_TOO_BROAD": 409,
            "INDEX_UNAVAILABLE": 503,
            "GMAIL_API_NOT_ENABLED": 503,
            "DOWNLOAD_FAILED": 502,
            "GMAIL_SEND_FAILED": 502,
        }
        for code, expected_status in cases.items():
            with self.subTest(code=code), patch.object(
                api_server, "build_drive_download_service", return_value=object()
            ), patch.object(
                api_server,
                "prepare_email_file",
                side_effect=api_server.EmailServiceError(code, "safe message"),
            ):
                response = self.authorized_post(
                    "/email/send-file", self.email_payload()
                )
            self.assertEqual(response.status_code, expected_status)
            self.assertEqual(response.json()["detail"]["code"], code)
            self.assertNotIn(self.api_key, response.text)

    def test_email_oauth_errors_are_sanitized_as_503(self) -> None:
        with patch.object(
            api_server,
            "build_drive_download_service",
            side_effect=api_server.DriveDownloadAuthenticationError("private"),
        ):
            drive_response = self.authorized_post(
                "/email/send-file", self.email_payload()
            )

        with patch.object(
            api_server, "build_drive_download_service", return_value=object()
        ), patch.object(
            api_server, "prepare_email_file", return_value=object()
        ), patch.object(
            api_server,
            "send_prepared_email",
            side_effect=api_server.GmailAuthenticationError("private"),
        ):
            gmail_response = self.authorized_post(
                "/email/send-file", self.email_payload()
            )

        self.assertEqual(drive_response.status_code, 503)
        self.assertEqual(
            drive_response.json()["detail"]["code"], "DRIVE_AUTH_FAILED"
        )
        self.assertEqual(gmail_response.status_code, 503)
        self.assertEqual(
            gmail_response.json()["detail"]["code"], "GMAIL_AUTH_FAILED"
        )
        self.assertNotIn("private", drive_response.text)
        self.assertNotIn("private", gmail_response.text)

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

    def test_read_only_endpoints_never_authenticate_or_modify_index(self) -> None:
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
            page = self.authorized_post(
                "/folders/tree/page",
                {"include_files": True, "page_size": 5},
            )
            self.assertEqual(page.status_code, 200)
            export = self.authorized_post(
                "/exports/drive-tree",
                {"format": "txt", "include_files": False},
            )
            self.assertEqual(export.status_code, 200)
            returned = self.authorized_post(
                f"/exports/{export.json()['export_id']}/openai-file", {}
            )
            self.assertEqual(returned.status_code, 200)
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
            "/contacts/status",
            "/contacts/CONTACT-UNKNOWN",
            "/exports/" + ("A" * 32),
        )
        for endpoint in protected_endpoints:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn(self.api_key, response.text)

        protected_posts = (
            ("/folders/tree/page", {"page_size": 5}),
            ("/exports/drive-tree", {"format": "txt"}),
            (f"/exports/{'A' * 32}/openai-file", {}),
        )
        for endpoint, payload in protected_posts:
            with self.subTest(endpoint=endpoint):
                response = self.client.post(endpoint, json=payload)
                self.assertEqual(response.status_code, 401)

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
