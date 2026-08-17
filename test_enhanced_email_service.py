import base64
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import Mock, patch

import drive_share_client
import enhanced_email_service as enhanced
from database import connect_database, initialize_schema
from email_service import EmailServiceError


class FakeRequest:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def execute(self, **kwargs) -> dict:
        return self.payload


class FakeFiles:
    def __init__(self, metadata: dict[str, dict]) -> None:
        self.metadata = metadata
        self.get_calls: list[dict] = []

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return FakeRequest(self.metadata[kwargs["fileId"]])


class FakePermissions:
    def __init__(
        self,
        values: dict[str, list[dict]] | None = None,
        create_responses: list[dict] | None = None,
    ) -> None:
        self.values = values or {}
        self.list_calls: list[dict] = []
        self.create_calls: list[dict] = []
        self.create_responses = create_responses or []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return FakeRequest(
            {"permissions": self.values.get(kwargs["fileId"], [])}
        )

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        response = (
            self.create_responses.pop(0)
            if self.create_responses
            else {
                "id": "PERMISSION-1",
                "type": "anyone",
                "role": "reader",
                "allowFileDiscovery": False,
            }
        )
        return FakeRequest(response)


class FakeDriveService:
    def __init__(
        self,
        metadata: dict[str, dict],
        permissions: dict[str, list[dict]] | None = None,
        create_responses: list[dict] | None = None,
    ) -> None:
        self.file_resource = FakeFiles(metadata)
        self.permission_resource = FakePermissions(
            permissions, create_responses=create_responses
        )

    def files(self):
        return self.file_resource

    def permissions(self):
        return self.permission_resource


def binary_metadata(file_id: str, name: str, size: int) -> dict:
    return {
        "id": file_id,
        "name": name,
        "mimeType": "application/octet-stream",
        "size": str(size),
        "trashed": False,
        "modifiedTime": "2026-08-14T00:00:00Z",
        "version": "1",
        "webViewLink": f"https://drive.google.com/open?id={file_id}&resourcekey=key",
        "capabilities": {"canDownload": True, "canShare": True},
    }


class EnhancedEmailServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.index_path = root / "drive_index.db"
        self.state_path = root / "enhanced_state.db"
        self.log_path = root / "enhanced.log"
        self.debug_log_path = root / "enhanced_debug.log"
        connection = connect_database(self.index_path)
        initialize_schema(connection)
        for file_id, name, mime_type, size in (
            ("F1", "one.bin", "application/octet-stream", 3),
            ("F2", "two.txt", "text/plain", 4),
            (
                "N1",
                "Native Document",
                "application/vnd.google-apps.document",
                100,
            ),
            (
                "L1",
                "large.bin",
                "application/octet-stream",
                enhanced.MAX_ATTACHMENT_BYTES + 1,
            ),
        ):
            connection.execute(
                """
                INSERT INTO files (
                    file_id, name, mime_type, size_bytes, trashed, indexed_at
                ) VALUES (?, ?, ?, ?, 0, 'now')
                """,
                (file_id, name, mime_type, size),
            )
        connection.execute(
            "INSERT INTO folders (folder_id, name, indexed_at) VALUES ('D1', 'Folder', 'now')"
        )
        connection.commit()
        connection.close()
        self.metadata = {
            "F1": binary_metadata("F1", "one.bin", 3),
            "F2": {
                **binary_metadata("F2", "two.txt", 4),
                "mimeType": "text/plain",
            },
            "N1": {
                **binary_metadata("N1", "Native Document", 100),
                "mimeType": "application/vnd.google-apps.document",
                "capabilities": {"canDownload": False, "canShare": True},
            },
            "L1": binary_metadata(
                "L1", "large.bin", enhanced.MAX_ATTACHMENT_BYTES + 1
            ),
        }

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def drive(self, permissions=None) -> FakeDriveService:
        return FakeDriveService(self.metadata, permissions)

    def preview(self, **overrides):
        arguments = {
            "drive_service": self.drive(),
            "file_ids": ["F1", "F2"],
            "recipient": "to.private@example.com",
            "cc": [],
            "subject": "Approved subject",
            "body": "PRIVATE_BODY",
            "mode": "attachment",
            "database_path": self.index_path,
            "state_database_path": self.state_path,
            "diagnostic_log_path": self.debug_log_path,
        }
        arguments.update(overrides)
        return enhanced.create_enhanced_preview(**arguments)

    def assert_code(self, code: str, callback) -> None:
        with self.assertRaises(EmailServiceError) as context:
            callback()
        self.assertEqual(context.exception.code, code)

    def test_cc_normalization_deduplicates_and_removes_to(self) -> None:
        preview = self.preview(
            cc=[
                "TO.PRIVATE@example.com",
                "cc@example.com",
                "CC@example.com",
                "second@example.com",
            ]
        )
        self.assertEqual(preview.cc, ("cc@example.com", "second@example.com"))

    def test_cc_validation_and_limit(self) -> None:
        for value in (
            ["bad\r\nBcc: victim@example.com"],
            ["a@example.com,b@example.com"],
            ["not-an-email"],
            "not-a-list",
        ):
            with self.subTest(value=value):
                self.assert_code("INVALID_CC", lambda value=value: self.preview(cc=value))
        self.assert_code(
            "TOO_MANY_CC",
            lambda: self.preview(cc=[f"u{i}@example.com" for i in range(6)]),
        )

    def test_file_id_validation_rejects_empty_duplicate_and_over_limit(self) -> None:
        self.assert_code(
            "INVALID_FILE_IDS", lambda: self.preview(file_ids=[])
        )
        self.assert_code(
            "DUPLICATE_FILE_ID", lambda: self.preview(file_ids=["F1", "F1"])
        )
        self.assert_code(
            "TOO_MANY_FILES",
            lambda: self.preview(file_ids=[f"F{i}" for i in range(6)]),
        )

    def test_every_file_must_be_exactly_indexed(self) -> None:
        self.assert_code(
            "FILE_NOT_INDEXED", lambda: self.preview(file_ids=["F1", "MISSING"])
        )
        self.assert_code(
            "UNSUPPORTED_FOLDER", lambda: self.preview(file_ids=["D1"])
        )

    def test_attachment_preview_is_read_only_and_preserves_order(self) -> None:
        service = self.drive()
        with patch.object(enhanced, "download_file") as download, patch.object(
            enhanced, "create_anyone_reader_permission"
        ) as create_permission, patch.object(enhanced, "send_message") as send:
            preview = self.preview(drive_service=service)

        self.assertEqual(preview.delivery_mode, "attachment")
        self.assertEqual(preview.sharing_mode, enhanced.SHARING_MODE_NONE)
        self.assertEqual(preview.total_size_bytes, 7)
        self.assertEqual([file.file_id for file in preview.files], ["F1", "F2"])
        self.assertEqual(service.permission_resource.list_calls, [])
        download.assert_not_called()
        create_permission.assert_not_called()
        send.assert_not_called()

    def test_attachment_mode_never_silently_changes_to_link(self) -> None:
        self.assert_code(
            "ATTACHMENT_MODE_UNSUPPORTED",
            lambda: self.preview(file_ids=["N1"], mode="attachment"),
        )
        self.assert_code(
            "TOTAL_ATTACHMENT_TOO_LARGE",
            lambda: self.preview(file_ids=["L1"], mode="attachment"),
        )

    def test_auto_uses_attachment_when_safe_and_link_for_native_or_large(self) -> None:
        safe = self.preview(file_ids=["F1"], mode="auto")
        native_service = self.drive({"N1": []})
        native = self.preview(
            drive_service=native_service, file_ids=["N1"], mode="auto"
        )
        large_service = self.drive({"L1": []})
        large = self.preview(
            drive_service=large_service, file_ids=["L1"], mode="auto"
        )

        self.assertEqual(safe.delivery_mode, "attachment")
        self.assertEqual(native.delivery_mode, "link")
        self.assertEqual(large.delivery_mode, "link")

    def test_link_preview_is_file_level_and_ignores_recipient_account_type(self) -> None:
        permissions = {
            "F1": [
                {
                    "id": "P1",
                    "type": "anyone",
                    "role": "reader",
                    "allowFileDiscovery": False,
                },
            ]
        }
        service = self.drive(permissions)
        with patch.object(enhanced, "download_file") as download, patch.object(
            enhanced, "create_anyone_reader_permission"
        ) as create_permission, patch.object(enhanced, "send_message") as send:
            preview = self.preview(
                drive_service=service,
                file_ids=["F1"],
                recipient="non-google@naver.com",
                cc=["office@company.example"],
                mode="link",
            )

        self.assertEqual(
            preview.sharing_mode,
            enhanced.SHARING_MODE_ANYONE_WITH_LINK_READER,
        )
        self.assertEqual(
            [(item.file_id, item.action) for item in preview.sharing_changes],
            [("F1", "existing")],
        )
        self.assertEqual(len(service.permission_resource.list_calls), 1)
        download.assert_not_called()
        create_permission.assert_not_called()
        send.assert_not_called()

    def test_link_preview_plans_one_anyone_permission_per_file(self) -> None:
        preview = self.preview(
            drive_service=self.drive(
                {
                    "F1": [
                        {
                            "type": "user",
                            "role": "writer",
                            "emailAddress": "recipient@naver.com",
                        }
                    ],
                    "F2": [],
                }
            ),
            recipient="recipient@naver.com",
            cc=["copy@company.example"],
            mode="link",
        )

        self.assertEqual(
            [(item.file_id, item.action) for item in preview.sharing_changes],
            [
                ("F1", "create_anyone_reader"),
                ("F2", "create_anyone_reader"),
            ],
        )
        self.assertTrue(
            all(item.permission_type == "anyone" for item in preview.sharing_changes)
        )
        self.assertTrue(
            all(item.allow_file_discovery is False for item in preview.sharing_changes)
        )

    def test_public_or_broader_anyone_permission_is_rejected(self) -> None:
        for permission in (
            {"type": "anyone", "role": "reader", "allowFileDiscovery": True},
            {"type": "anyone", "role": "writer", "allowFileDiscovery": False},
            {"type": "anyone", "role": "commenter", "allowFileDiscovery": False},
        ):
            with self.subTest(permission=permission):
                self.assert_code(
                    "LINK_PERMISSION_TOO_BROAD",
                    lambda permission=permission: self.preview(
                        drive_service=self.drive({"F1": [permission]}),
                        file_ids=["F1"],
                        mode="link",
                    ),
                )

    def test_link_requires_web_view_link_and_share_capability_for_create(self) -> None:
        original = self.metadata["F1"]
        self.metadata["F1"] = {**original, "webViewLink": None}
        self.assert_code(
            "LINK_UNAVAILABLE", lambda: self.preview(file_ids=["F1"], mode="link")
        )
        self.metadata["F1"] = {
            **original,
            "capabilities": {"canDownload": True, "canShare": False},
        }
        self.assert_code(
            "SHARING_FAILED", lambda: self.preview(file_ids=["F1"], mode="link")
        )

    def test_preview_store_has_ttl_hashes_and_no_body(self) -> None:
        preview = self.preview()
        connection = sqlite3.connect(self.state_path)
        row = connection.execute(
            "SELECT * FROM enhanced_email_previews WHERE preview_id = ?",
            (preview.preview_id,),
        ).fetchone()
        columns = [item[1] for item in connection.execute(
            "PRAGMA table_info(enhanced_email_previews)"
        )]
        connection.close()
        persisted = dict(zip(columns, row))

        self.assertEqual(persisted["status"], "PREVIEWED")
        self.assertEqual(persisted["plan_hash"], preview.plan_hash)
        self.assertNotIn("body", persisted)
        self.assertNotIn("PRIVATE_BODY", repr(row))
        self.assertLessEqual(
            enhanced.utc_now(),
            enhanced.datetime.fromisoformat(preview.expires_at),
        )

        diagnostic = json.loads(
            self.debug_log_path.read_text(encoding="utf-8").splitlines()[-1]
        )
        self.assertEqual(diagnostic["event"], "preview_persisted")
        self.assertEqual(diagnostic["preview_id"], preview.preview_id)
        self.assertEqual(
            diagnostic["state_database_path"], str(self.state_path.resolve())
        )
        self.assertTrue(diagnostic["preview_exists"])
        self.assertTrue(diagnostic["preview_id_matches_stored"])
        self.assertFalse(diagnostic["cleanup_configured"])

    def test_preview_survives_fresh_process_within_ttl(self) -> None:
        preview = self.preview(file_ids=["F1"])
        other_working_directory = Path(self.temporary_directory.name) / "other-cwd"
        other_working_directory.mkdir()
        project_root = Path(__file__).resolve().parent
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(project_root)
        script = (
            "import sys; from pathlib import Path; "
            "from enhanced_email_service import EnhancedEmailStore; "
            "row=EnhancedEmailStore(Path(sys.argv[1])).load_preview(sys.argv[2]); "
            "print(row['preview_id'])"
        )

        completed = subprocess.run(
            [sys.executable, "-c", script, str(self.state_path), preview.preview_id],
            cwd=other_working_directory,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.stdout.strip(), preview.preview_id)

    def test_default_state_path_is_absolute_and_cwd_independent(self) -> None:
        original_cwd = Path.cwd()
        other_working_directory = Path(self.temporary_directory.name) / "cwd-check"
        other_working_directory.mkdir()
        try:
            os.chdir(other_working_directory)
            resolved = enhanced.EnhancedEmailStore().path
        finally:
            os.chdir(original_cwd)

        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved, enhanced.ENHANCED_STATE_DATABASE_PATH.resolve())

    def test_expired_preview_stops_before_factories(self) -> None:
        preview = self.preview()
        connection = sqlite3.connect(self.state_path)
        connection.execute(
            "UPDATE enhanced_email_previews SET expires_at = ? WHERE preview_id = ?",
            (
                enhanced.utc_text(enhanced.utc_now() - timedelta(seconds=1)),
                preview.preview_id,
            ),
        )
        connection.commit()
        connection.close()
        drive_factory = Mock()

        self.assert_code(
            "PREVIEW_EXPIRED",
            lambda: self.send(preview, drive_service_factory=drive_factory),
        )
        drive_factory.assert_not_called()
        connection = sqlite3.connect(self.state_path)
        still_present = connection.execute(
            "SELECT COUNT(*) FROM enhanced_email_previews WHERE preview_id = ?",
            (preview.preview_id,),
        ).fetchone()[0]
        connection.close()
        self.assertEqual(still_present, 1)
        diagnostic = [
            json.loads(line)
            for line in self.debug_log_path.read_text(encoding="utf-8").splitlines()
        ][-1]
        self.assertEqual(diagnostic["event"], "preview_lookup")
        self.assertTrue(diagnostic["preview_exists"])
        self.assertTrue(diagnostic["preview_expired"])
        self.assertFalse(diagnostic["cleanup_configured"])

    def send(self, preview, **overrides):
        arguments = {
            "preview_id": preview.preview_id,
            "file_ids": [file.file_id for file in preview.files],
            "recipient": preview.recipient,
            "cc": list(preview.cc),
            "subject": "Approved subject",
            "body": "PRIVATE_BODY",
            "mode": preview.requested_mode,
            "idempotency_key": "EMAIL-ENHANCED-0001",
            "drive_service_factory": Mock(return_value=self.drive()),
            "gmail_service_factory": Mock(return_value=object()),
            "share_service_factory": Mock(return_value=object()),
            "database_path": self.index_path,
            "state_database_path": self.state_path,
            "audit_log_path": self.log_path,
            "diagnostic_log_path": self.debug_log_path,
        }
        arguments.update(overrides)
        return enhanced.send_enhanced_email(**arguments)

    def test_exact_preview_id_sends_and_one_character_change_is_not_found(self) -> None:
        preview = self.preview(file_ids=["F1"])
        replacement = "A" if preview.preview_id[-1] != "A" else "B"
        changed_preview_id = preview.preview_id[:-1] + replacement
        drive_factory = Mock(return_value=self.drive())

        self.assert_code(
            "PREVIEW_NOT_FOUND",
            lambda: self.send(
                preview,
                preview_id=changed_preview_id,
                drive_service_factory=drive_factory,
                idempotency_key="EMAIL-CHANGED-PREVIEW-1",
            ),
        )
        drive_factory.assert_not_called()

        with patch.object(enhanced, "download_file", return_value=b"111"), patch.object(
            enhanced, "send_message", return_value="MESSAGE-EXACT-PREVIEW"
        ):
            result = self.send(
                preview,
                idempotency_key="EMAIL-EXACT-PREVIEW-1",
            )

        self.assertEqual(result.status, "sent")
        diagnostics = [
            json.loads(line)
            for line in self.debug_log_path.read_text(encoding="utf-8").splitlines()
        ]
        changed_lookup = next(
            item
            for item in diagnostics
            if item["event"] == "preview_lookup"
            and item["preview_id"] == changed_preview_id
        )
        exact_lookup = next(
            item
            for item in diagnostics
            if item["event"] == "preview_lookup"
            and item["preview_id"] == preview.preview_id
        )
        self.assertFalse(changed_lookup["preview_exists"])
        self.assertFalse(changed_lookup["preview_id_matches_stored"])
        self.assertTrue(exact_lookup["preview_exists"])
        self.assertFalse(exact_lookup["preview_expired"])
        self.assertTrue(exact_lookup["preview_id_matches_stored"])
        self.assertEqual(
            changed_lookup["state_database_path"],
            exact_lookup["state_database_path"],
        )

    def test_multi_attachment_send_builds_cc_and_ordered_attachments(self) -> None:
        preview = self.preview(cc=["cc.private@example.com"])

        def download(_service, file_id, _maximum):
            return {"F1": b"111", "F2": b"2222"}[file_id]

        with patch.object(enhanced, "download_file", side_effect=download) as downloads, patch.object(
            enhanced, "send_message", return_value="MESSAGE-1"
        ) as sender:
            result = self.send(
                preview,
                drive_service_factory=Mock(
                    return_value=self.drive()
                ),
            )

        self.assertEqual(result.delivery_mode, "attachment")
        self.assertEqual(result.file_count, 2)
        self.assertEqual(downloads.call_count, 2)
        message = BytesParser(policy=policy.default).parsebytes(sender.call_args.args[1])
        self.assertEqual(message["To"], "to.private@example.com")
        self.assertEqual(message["Cc"], "cc.private@example.com")
        self.assertEqual(
            [item.get_filename() for item in message.iter_attachments()],
            ["one.bin", "two.txt"],
        )

    def test_link_send_creates_only_anyone_reader_and_preserves_body(self) -> None:
        read_service = self.drive({"F1": []})
        preview = self.preview(
            drive_service=read_service,
            file_ids=["F1"],
            mode="link",
        )
        send_read_service = self.drive({"F1": []})
        share_service = FakeDriveService(
            self.metadata,
            create_responses=[
                {
                    "id": "P-NEW",
                    "type": "anyone",
                    "role": "reader",
                    "allowFileDiscovery": False,
                }
            ],
        )
        pre_share_metadata = dict(self.metadata["F1"])
        post_share_metadata = {
            **self.metadata["F1"],
            "webViewLink": "https://drive.google.com/post-share-link-F1",
        }
        with patch.object(enhanced, "download_file") as download, patch.object(
            enhanced, "send_message", return_value="MESSAGE-LINK"
        ) as sender, patch.object(
            enhanced,
            "get_file_metadata",
            side_effect=[pre_share_metadata, post_share_metadata],
        ) as metadata_reader:
            result = self.send(
                preview,
                drive_service_factory=Mock(return_value=send_read_service),
                share_service_factory=Mock(return_value=share_service),
            )

        download.assert_not_called()
        self.assertEqual(result.delivery_mode, "link")
        self.assertEqual(len(result.sharing_changes), 1)
        self.assertNotIn("recipient", result.sharing_changes[0])
        create_call = share_service.permission_resource.create_calls[0]
        self.assertEqual(
            create_call["body"],
            {
                "type": "anyone",
                "role": "reader",
                "allowFileDiscovery": False,
            },
        )
        self.assertNotIn("sendNotificationEmail", create_call)
        self.assertEqual(result.sharing_mode, "anyone_with_link_reader")
        self.assertEqual(metadata_reader.call_count, 2)
        message = BytesParser(policy=policy.default).parsebytes(sender.call_args.args[1])
        text = message.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("PRIVATE_BODY", text)
        self.assertIn("첨부 파일 링크:", text)
        self.assertIn(post_share_metadata["webViewLink"], text)
        self.assertNotIn(pre_share_metadata["webViewLink"], text)

    def test_permission_partial_failure_never_calls_gmail(self) -> None:
        preview = self.preview(
            drive_service=self.drive({"F1": [], "F2": []}),
            file_ids=["F1", "F2"],
            cc=["cc.private@example.com"],
            mode="link",
        )
        sender = Mock()
        with patch.object(
            enhanced,
            "create_anyone_reader_permission",
            side_effect=["P1", drive_share_client.DriveSharingError("safe")],
        ), patch.object(enhanced, "send_message", sender):
            self.assert_code(
                "SHARING_PARTIAL",
                lambda: self.send(
                    preview,
                    drive_service_factory=Mock(
                        return_value=self.drive({"F1": [], "F2": []})
                    ),
                ),
            )
        sender.assert_not_called()
        connection = sqlite3.connect(self.state_path)
        status = connection.execute(
            "SELECT status FROM enhanced_email_sends"
        ).fetchone()[0]
        permission_rows = connection.execute(
            "SELECT file_id, status FROM enhanced_link_permission_events ORDER BY file_id"
        ).fetchall()
        permission_columns = [
            item[1]
            for item in connection.execute(
                "PRAGMA table_info(enhanced_link_permission_events)"
            )
        ]
        connection.close()
        self.assertEqual(status, "SHARING_PARTIAL")
        self.assertEqual(permission_rows, [("F1", "CREATED"), ("F2", "FAILED")])
        self.assertNotIn("recipient_hash", permission_columns)

    def test_post_share_link_refresh_failure_never_calls_gmail(self) -> None:
        preview = self.preview(
            drive_service=self.drive({"F1": []}),
            file_ids=["F1"],
            mode="link",
        )
        sender = Mock()
        with patch.object(
            enhanced,
            "get_file_metadata",
            side_effect=[dict(self.metadata["F1"]), OSError("safe")],
        ), patch.object(enhanced, "send_message", sender):
            self.assert_code(
                "SHARING_PARTIAL",
                lambda: self.send(
                    preview,
                    drive_service_factory=Mock(
                        return_value=self.drive({"F1": []})
                    ),
                    share_service_factory=Mock(
                        return_value=FakeDriveService(self.metadata)
                    ),
                ),
            )
        sender.assert_not_called()
        connection = sqlite3.connect(self.state_path)
        status, error_code = connection.execute(
            "SELECT status, error_code FROM enhanced_email_sends"
        ).fetchone()
        connection.close()
        self.assertEqual(status, "SHARING_PARTIAL")
        self.assertEqual(error_code, "LINK_UNAVAILABLE")

    def test_changed_anyone_plan_makes_preview_stale(self) -> None:
        preview = self.preview(
            drive_service=self.drive({"F1": []}),
            file_ids=["F1"],
            mode="link",
        )
        existing = {
            "F1": [
                {
                    "type": "anyone",
                    "role": "reader",
                    "allowFileDiscovery": False,
                }
            ]
        }
        share_factory = Mock()
        sender = Mock()
        with patch.object(enhanced, "send_message", sender):
            self.assert_code(
                "PREVIEW_STALE",
                lambda: self.send(
                    preview,
                    drive_service_factory=Mock(return_value=self.drive(existing)),
                    share_service_factory=share_factory,
                ),
            )
        share_factory.assert_not_called()
        sender.assert_not_called()

    def test_preview_stale_stops_before_permission_download_and_gmail(self) -> None:
        preview = self.preview()
        changed = {key: dict(value) for key, value in self.metadata.items()}
        changed["F1"] = {**changed["F1"], "version": "2"}
        download = Mock()
        sender = Mock()
        share_factory = Mock()
        with patch.object(enhanced, "download_file", download), patch.object(
            enhanced, "send_message", sender
        ):
            self.assert_code(
                "PREVIEW_STALE",
                lambda: self.send(
                    preview,
                    drive_service_factory=Mock(
                        return_value=FakeDriveService(changed)
                    ),
                    share_service_factory=share_factory,
                ),
            )
        download.assert_not_called()
        sender.assert_not_called()
        share_factory.assert_not_called()

    def test_sent_replay_calls_no_external_factories(self) -> None:
        preview = self.preview(file_ids=["F1"])
        with patch.object(enhanced, "download_file", return_value=b"111"), patch.object(
            enhanced, "send_message", return_value="MESSAGE-REPLAY"
        ):
            self.send(preview)
        drive_factory = Mock()
        gmail_factory = Mock()
        share_factory = Mock()
        replay = self.send(
            preview,
            drive_service_factory=drive_factory,
            gmail_service_factory=gmail_factory,
            share_service_factory=share_factory,
        )
        self.assertTrue(replay.idempotent_replay)
        drive_factory.assert_not_called()
        gmail_factory.assert_not_called()
        share_factory.assert_not_called()

    def test_raw_size_hard_cap_fails_without_send(self) -> None:
        preview = self.preview(file_ids=["F1"])
        sender = Mock()
        with patch.object(enhanced, "download_file", return_value=b"111"), patch.object(
            enhanced, "send_message", sender
        ), patch.object(enhanced, "MAX_GMAIL_RAW_BYTES", 10):
            self.assert_code("RAW_MESSAGE_TOO_LARGE", lambda: self.send(preview))
        sender.assert_not_called()

    def test_audit_masks_addresses_and_omits_body_and_raw(self) -> None:
        preview = self.preview(
            file_ids=["F1"], cc=["cc.private@example.com"]
        )
        with patch.object(enhanced, "download_file", return_value=b"111"), patch.object(
            enhanced, "send_message", return_value="MESSAGE-LOG"
        ):
            self.send(preview)
        text = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn("to.private@example.com", text)
        self.assertNotIn("cc.private@example.com", text)
        self.assertNotIn("PRIVATE_BODY", text)
        self.assertNotIn(base64.urlsafe_b64encode(b"111").decode(), text)
        diagnostic_text = self.debug_log_path.read_text(encoding="utf-8")
        self.assertNotIn("to.private@example.com", diagnostic_text)
        self.assertNotIn("cc.private@example.com", diagnostic_text)
        self.assertNotIn("PRIVATE_BODY", diagnostic_text)
        self.assertNotIn("EMAIL-ENHANCED-0001", diagnostic_text)
        self.assertNotIn(base64.urlsafe_b64encode(b"111").decode(), diagnostic_text)


class DriveShareClientTests(unittest.TestCase):
    def test_scope_and_token_are_isolated(self) -> None:
        self.assertEqual(
            drive_share_client.SCOPES,
            ["https://www.googleapis.com/auth/drive"],
        )
        self.assertEqual(
            drive_share_client.TOKEN_FILE.name, "drive_share_token.json"
        )

    def test_list_permissions_reads_all_pages(self) -> None:
        first = FakeRequest({"permissions": [{"id": "P1"}], "nextPageToken": "NEXT"})
        second = FakeRequest({"permissions": [{"id": "P2"}]})
        resource = Mock()
        resource.list.side_effect = [first, second]
        service = Mock()
        service.permissions.return_value = resource

        result = drive_share_client.list_permissions(service, "F1")

        self.assertEqual([item["id"] for item in result], ["P1", "P2"])
        self.assertEqual(resource.list.call_count, 2)
        self.assertEqual(resource.list.call_args_list[1].kwargs["pageToken"], "NEXT")

    def test_create_is_hard_coded_to_nondiscoverable_anyone_reader(self) -> None:
        service = FakeDriveService({}, create_responses=[
            {
                "id": "P1",
                "type": "anyone",
                "role": "reader",
                "allowFileDiscovery": False,
            }
        ])
        permission_id = drive_share_client.create_anyone_reader_permission(
            service, "F1"
        )
        call = service.permission_resource.create_calls[0]
        self.assertEqual(permission_id, "P1")
        self.assertEqual(
            call["body"],
            {
                "type": "anyone",
                "role": "reader",
                "allowFileDiscovery": False,
            },
        )
        self.assertNotIn("sendNotificationEmail", call)
        self.assertNotIn("emailAddress", call["body"])

    def test_source_contains_only_allowlisted_drive_write(self) -> None:
        project = Path(__file__).resolve().parent
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in project.glob("*.py")
            if not path.name.startswith("test_")
        )
        forbidden = (
            ".files().create(",
            ".files().update(",
            ".files().copy(",
            ".files().delete(",
            ".permissions().update(",
            ".permissions().delete(",
        )
        for pattern in forbidden:
            with self.subTest(pattern=pattern):
                self.assertNotIn(pattern, sources)
        self.assertEqual(sources.count(".permissions()\n            .create("), 1)
        share_source = (project / "drive_share_client.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"type": "anyone"', share_source)
        self.assertIn('"role": "reader"', share_source)
        self.assertIn('"allowFileDiscovery": False', share_source)
        self.assertNotIn('"type": "user"', share_source)
        self.assertNotIn('"type": "group"', share_source)
        self.assertNotIn('"type": "domain"', share_source)
        self.assertNotIn("sendNotificationEmail", share_source)
        self.assertNotIn("emailAddress", share_source)


if __name__ == "__main__":
    unittest.main()
