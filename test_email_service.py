import base64
import sqlite3
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import Mock, patch

import drive_download_client
import email_cli
import email_service
import gmail_client
from database import connect_database, initialize_schema


class FakeRequest:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def execute(self) -> dict:
        return self.payload


class FakeDriveFiles:
    def __init__(self, metadata: dict) -> None:
        self.metadata = metadata
        self.requested_file_ids: list[str] = []

    def get(self, **arguments):
        self.requested_file_ids.append(arguments["fileId"])
        return FakeRequest(self.metadata)


class FakeDriveService:
    def __init__(self, metadata: dict) -> None:
        self.file_resource = FakeDriveFiles(metadata)

    def files(self) -> FakeDriveFiles:
        return self.file_resource


def binary_metadata(**overrides) -> dict:
    metadata = {
        "id": "FILE-1",
        "name": "sample.pdf",
        "mimeType": "application/pdf",
        "size": "12",
        "trashed": False,
        "capabilities": {"canDownload": True},
    }
    metadata.update(overrides)
    return metadata


class EmailServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.database_path = root / "drive_index.db"
        self.state_path = root / "email_send_state.db"
        self.log_path = root / "email_send.log"
        connection = connect_database(self.database_path)
        initialize_schema(connection)
        connection.execute(
            """
            INSERT INTO files (
                file_id, name, mime_type, size_bytes, trashed, indexed_at
            ) VALUES ('FILE-1', 'sample.pdf', 'application/pdf', 12, 0, 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO folders (folder_id, name, indexed_at)
            VALUES ('FOLDER-1', 'Folder', 'now')
            """
        )
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def prepare(self, metadata: dict | None = None, **overrides):
        arguments = {
            "drive_service": FakeDriveService(metadata or binary_metadata()),
            "file_id": "FILE-1",
            "recipient": "alice.private@example.com",
            "subject": "Test subject",
            "body": "HIGHLY_PRIVATE_BODY",
            "idempotency_key": "EMAIL-TEST-0001",
            "database_path": self.database_path,
        }
        arguments.update(overrides)
        return email_service.prepare_email_file(**arguments)

    def assert_error_code(self, code: str, callback) -> None:
        with self.assertRaises(email_service.EmailServiceError) as context:
            callback()
        self.assertEqual(context.exception.code, code)

    def test_prepare_uses_exact_indexed_file_id(self) -> None:
        service = FakeDriveService(binary_metadata())
        prepared = self.prepare(drive_service=service)

        self.assertEqual(prepared.file_id, "FILE-1")
        self.assertEqual(prepared.file_name, "sample.pdf")
        self.assertEqual(prepared.size_bytes, 12)
        self.assertEqual(service.file_resource.requested_file_ids, ["FILE-1"])

    def test_not_indexed_and_indexed_folder_are_distinct(self) -> None:
        self.assert_error_code(
            "FILE_NOT_INDEXED",
            lambda: self.prepare(file_id="MISSING"),
        )
        self.assert_error_code(
            "UNSUPPORTED_FOLDER",
            lambda: self.prepare(file_id="FOLDER-1"),
        )

    def test_native_folder_and_shortcut_are_rejected(self) -> None:
        cases = (
            (
                "application/vnd.google-apps.document",
                "UNSUPPORTED_NATIVE_FILE",
            ),
            (email_service.FOLDER_MIME_TYPE, "UNSUPPORTED_FOLDER"),
            (email_service.SHORTCUT_MIME_TYPE, "UNSUPPORTED_SHORTCUT"),
        )
        for mime_type, expected_code in cases:
            with self.subTest(mime_type=mime_type):
                self.assert_error_code(
                    expected_code,
                    lambda mime_type=mime_type: self.prepare(
                        binary_metadata(mimeType=mime_type)
                    ),
                )

    def test_trashed_too_large_and_not_downloadable_are_rejected(self) -> None:
        cases = (
            (binary_metadata(trashed=True), "FILE_TRASHED"),
            (
                binary_metadata(size=str(email_service.MAX_ATTACHMENT_BYTES + 1)),
                "ATTACHMENT_TOO_LARGE",
            ),
            (
                binary_metadata(capabilities={"canDownload": False}),
                "DOWNLOAD_FAILED",
            ),
        )
        for metadata, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self.assert_error_code(
                    expected_code,
                    lambda metadata=metadata: self.prepare(metadata),
                )

    def test_recipient_rejects_multiple_blank_and_header_injection(self) -> None:
        invalid_values = (
            "",
            "one@example.com,two@example.com",
            "one@example.com;two@example.com",
            "one@example.com\r\nBcc: victim@example.com",
            "not-an-email",
        )
        for recipient in invalid_values:
            with self.subTest(recipient=recipient):
                self.assert_error_code(
                    "INVALID_RECIPIENT",
                    lambda recipient=recipient: self.prepare(recipient=recipient),
                )

    def test_mime_message_contains_one_plain_body_and_one_attachment(self) -> None:
        prepared = self.prepare()
        message_bytes = email_service.build_mime_message(prepared, b"PDF-CONTENT")
        message = BytesParser(policy=policy.default).parsebytes(message_bytes)
        attachments = list(message.iter_attachments())

        self.assertEqual(message["To"], "alice.private@example.com")
        self.assertEqual(message["Subject"], "Test subject")
        self.assertEqual(message.get_body(preferencelist=("plain",)).get_content().strip(), "HIGHLY_PRIVATE_BODY")
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "sample.pdf")
        self.assertEqual(attachments[0].get_payload(decode=True), b"PDF-CONTENT")

    def test_send_records_idempotency_and_masks_audit_data(self) -> None:
        prepared = self.prepare()
        drive_service = object()
        gmail_service = object()

        with patch.object(email_service, "download_file", return_value=b"123456789012") as download_mock, patch.object(
            email_service, "send_message", return_value="GMAIL-MESSAGE-1"
        ) as send_mock:
            first = email_service.send_prepared_email(
                prepared=prepared,
                drive_service=drive_service,
                gmail_service=gmail_service,
                state_database_path=self.state_path,
                audit_log_path=self.log_path,
            )
            second = email_service.send_prepared_email(
                prepared=prepared,
                drive_service=drive_service,
                gmail_service=gmail_service,
                state_database_path=self.state_path,
                audit_log_path=self.log_path,
            )

        self.assertEqual(first.message_id, "GMAIL-MESSAGE-1")
        self.assertFalse(first.idempotent_replay)
        self.assertTrue(second.idempotent_replay)
        download_mock.assert_called_once()
        send_mock.assert_called_once()

        log_text = self.log_path.read_text(encoding="utf-8")
        self.assertIn("a***@e***.com", log_text)
        self.assertNotIn("alice.private@example.com", log_text)
        self.assertNotIn("HIGHLY_PRIVATE_BODY", log_text)
        connection = sqlite3.connect(self.state_path)
        row_text = repr(connection.execute("SELECT * FROM email_send_state").fetchone())
        connection.close()
        self.assertNotIn("alice.private@example.com", row_text)
        self.assertNotIn("HIGHLY_PRIVATE_BODY", row_text)

    def test_idempotency_key_cannot_be_reused_for_changed_payload(self) -> None:
        first = self.prepare()
        changed = self.prepare(subject="Changed subject")
        store = email_service.EmailIdempotencyStore(self.state_path)
        store.begin(first)

        self.assert_error_code(
            "IDEMPOTENCY_CONFLICT",
            lambda: store.begin(changed),
        )

    def test_idempotent_replay_does_not_build_gmail_service(self) -> None:
        prepared = self.prepare()
        with patch.object(
            email_service, "download_file", return_value=b"123456789012"
        ), patch.object(
            email_service, "send_message", return_value="GMAIL-MESSAGE-1"
        ):
            email_service.send_prepared_email(
                prepared=prepared,
                drive_service=object(),
                gmail_service=object(),
                state_database_path=self.state_path,
                audit_log_path=self.log_path,
            )

        gmail_factory = Mock()
        replay = email_service.send_prepared_email(
            prepared=prepared,
            drive_service=object(),
            gmail_service_factory=gmail_factory,
            state_database_path=self.state_path,
            audit_log_path=self.log_path,
        )

        self.assertTrue(replay.idempotent_replay)
        gmail_factory.assert_not_called()

    def test_gmail_auth_failure_is_recorded_without_download(self) -> None:
        prepared = self.prepare()
        gmail_factory = Mock(
            side_effect=gmail_client.GmailAuthenticationError("private")
        )
        with patch.object(email_service, "download_file") as download_mock:
            with self.assertRaises(gmail_client.GmailAuthenticationError):
                email_service.send_prepared_email(
                    prepared=prepared,
                    drive_service=object(),
                    gmail_service_factory=gmail_factory,
                    state_database_path=self.state_path,
                    audit_log_path=self.log_path,
                )

        download_mock.assert_not_called()
        connection = sqlite3.connect(self.state_path)
        state = connection.execute(
            "SELECT status, error_code FROM email_send_state"
        ).fetchone()
        connection.close()
        self.assertEqual(state, ("FAILED", "GMAIL_AUTH_FAILED"))

    def test_missing_index_maps_to_index_unavailable(self) -> None:
        self.assert_error_code(
            "INDEX_UNAVAILABLE",
            lambda: self.prepare(
                database_path=Path(self.temporary_directory.name) / "missing.db"
            ),
        )

    def test_download_limit_buffer_never_stores_bytes_over_cap(self) -> None:
        stream = drive_download_client._LimitedBytesIO(5)
        stream.write(b"12345")
        with self.assertRaises(drive_download_client.DownloadSizeLimitExceeded):
            stream.write(b"6")
        self.assertEqual(stream.getvalue(), b"12345")

    def test_oauth_tokens_and_scopes_are_strictly_separate(self) -> None:
        self.assertEqual(
            drive_download_client.SCOPES,
            ["https://www.googleapis.com/auth/drive.readonly"],
        )
        self.assertEqual(
            gmail_client.SCOPES,
            ["https://www.googleapis.com/auth/gmail.send"],
        )
        self.assertNotEqual(
            drive_download_client.TOKEN_FILE,
            gmail_client.TOKEN_FILE,
        )
        self.assertEqual(drive_download_client.TOKEN_FILE.name, "drive_download_token.json")
        self.assertEqual(gmail_client.TOKEN_FILE.name, "gmail_send_token.json")


class GmailClientTests(unittest.TestCase):
    def test_send_uses_urlsafe_base64_and_disables_retries(self) -> None:
        execute = Mock(return_value={"id": "MESSAGE-123"})
        send = Mock(return_value=Mock(execute=execute))
        messages = Mock(return_value=Mock(send=send))
        users = Mock(return_value=Mock(messages=messages))
        service = Mock(users=users)

        message_id = gmail_client.send_message(service, b"mime bytes")

        self.assertEqual(message_id, "MESSAGE-123")
        request_body = send.call_args.kwargs["body"]
        self.assertEqual(
            base64.urlsafe_b64decode(request_body["raw"]),
            b"mime bytes",
        )
        execute.assert_called_once_with(num_retries=0)


class EmailCliTests(unittest.TestCase):
    def test_cancellation_does_not_start_gmail_oauth_or_send(self) -> None:
        prepared = email_service.PreparedEmailFile(
            file_id="FILE-1",
            recipient="alice.private@example.com",
            subject="Test",
            body="Body",
            file_name="sample.pdf",
            mime_type="application/pdf",
            size_bytes=12,
            idempotency_key="EMAIL-TEST-0001",
            payload_hash="hash",
        )
        with patch.object(email_cli, "build_drive_download_service", return_value=object()), patch.object(
            email_cli, "prepare_email_file", return_value=prepared
        ), patch("builtins.input", return_value="NO"), patch.object(
            email_cli, "build_gmail_service"
        ) as gmail_mock, patch.object(email_cli, "send_prepared_email") as send_mock:
            result = email_cli.run(
                [
                    "send",
                    "--file-id",
                    "FILE-1",
                    "--to",
                    "alice.private@example.com",
                    "--subject",
                    "Test",
                    "--body",
                    "Body",
                    "--idempotency-key",
                    "EMAIL-TEST-0001",
                ]
            )

        self.assertEqual(result, 2)
        gmail_mock.assert_not_called()
        send_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
