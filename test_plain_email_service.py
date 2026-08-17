import sqlite3
import tempfile
import unittest
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

import plain_email_service as plain
from email_service import EmailServiceError
from gmail_client import (
    GmailAuthenticationError,
    GmailDeliveryUncertainError,
    GmailSendError,
)


class PlainEmailServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.state_path = root / "plain_email_state.db"
        self.log_path = root / "plain_email.log"
        self.payload = {
            "recipient": "to@example.com",
            "cc": [],
            "subject": "Plain subject",
            "body": "Plain body",
        }

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def preview(self, **overrides) -> plain.TextEmailPreview:
        payload = {**self.payload, **overrides}
        return plain.create_text_email_preview(
            **payload,
            state_database_path=self.state_path,
        )

    def send(self, preview: plain.TextEmailPreview, **overrides):
        payload = {
            **self.payload,
            "preview_id": preview.preview_id,
            "idempotency_key": "PLAIN-EMAIL-0001",
            "gmail_service_factory": lambda: object(),
            "state_database_path": self.state_path,
            "audit_log_path": self.log_path,
            **overrides,
        }
        return plain.send_text_email(**payload)

    def test_preview_supports_to_only_and_does_not_store_body_or_subject(self) -> None:
        preview = self.preview()

        self.assertEqual(preview.recipient, "to@example.com")
        self.assertEqual(preview.cc, ())
        self.assertEqual(preview.subject, "Plain subject")
        self.assertEqual(preview.body, "Plain body")
        self.assertFalse(self.log_path.exists())

        connection = sqlite3.connect(self.state_path)
        try:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(plain_email_previews)"
                )
            }
            row = connection.execute(
                "SELECT payload_hash FROM plain_email_previews WHERE preview_id = ?",
                (preview.preview_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertNotIn("body", columns)
        self.assertNotIn("subject", columns)
        self.assertEqual(len(row[0]), 64)

    def test_cc_send_builds_plain_mime_without_attachment(self) -> None:
        preview = self.preview(
            cc=["cc1@example.com", "cc2@example.com"],
        )
        captured: dict[str, bytes] = {}

        def sender(_service, message_bytes: bytes) -> str:
            captured["message"] = message_bytes
            return "MESSAGE-PLAIN-CC"

        with patch.object(plain, "send_message", side_effect=sender) as send:
            result = self.send(
                preview,
                cc=["cc1@example.com", "cc2@example.com"],
            )

        self.assertEqual(result.status, "sent")
        self.assertEqual(result.cc, ("cc1@example.com", "cc2@example.com"))
        send.assert_called_once()
        message = BytesParser(policy=policy.default).parsebytes(captured["message"])
        self.assertEqual(message["To"], "to@example.com")
        self.assertEqual(message["Cc"], "cc1@example.com, cc2@example.com")
        self.assertEqual(message["Subject"], "Plain subject")
        self.assertEqual(message.get_content().rstrip(), "Plain body")
        self.assertFalse(message.is_multipart())
        self.assertEqual(list(message.iter_attachments()), [])

    def test_address_book_and_direct_email_mix_uses_normal_email_validation(self) -> None:
        preview = self.preview(
            recipient="directory.person@company.com",
            cc=["direct@example.com"],
        )
        self.assertEqual(preview.recipient, "directory.person@company.com")
        self.assertEqual(preview.cc, ("direct@example.com",))

    def test_invalid_to_or_cc_is_rejected(self) -> None:
        with self.assertRaisesRegex(EmailServiceError, "INVALID_RECIPIENT"):
            self.preview(recipient="not-an-email")
        with self.assertRaisesRegex(EmailServiceError, "INVALID_CC"):
            self.preview(cc=["not-an-email"])

    def test_wrong_preview_id_and_changed_payload_are_rejected(self) -> None:
        preview = self.preview()
        with self.assertRaisesRegex(EmailServiceError, "PREVIEW_NOT_FOUND"):
            self.send(preview, preview_id=preview.preview_id + "X")
        with self.assertRaisesRegex(EmailServiceError, "PREVIEW_STALE"):
            self.send(preview, body="Changed body")

    def test_expired_preview_is_rejected(self) -> None:
        preview = plain.create_text_email_preview(
            **self.payload,
            state_database_path=self.state_path,
            expires_at=plain.utc_text(plain.utc_now() - timedelta(seconds=1)),
        )
        with self.assertRaisesRegex(EmailServiceError, "PREVIEW_EXPIRED"):
            self.send(preview)

    def test_successful_idempotency_replay_does_not_send_twice(self) -> None:
        preview = self.preview()
        with patch.object(
            plain, "send_message", return_value="MESSAGE-PLAIN-REPLAY"
        ) as send:
            first = self.send(preview)
            replay = self.send(preview)

        self.assertFalse(first.idempotent_replay)
        self.assertTrue(replay.idempotent_replay)
        self.assertEqual(replay.message_id, "MESSAGE-PLAIN-REPLAY")
        send.assert_called_once()

    def test_gmail_failure_is_definite_and_audit_has_no_message_content(self) -> None:
        preview = self.preview()
        with patch.object(
            plain, "send_message", side_effect=GmailSendError("rejected")
        ):
            with self.assertRaisesRegex(EmailServiceError, "GMAIL_SEND_FAILED"):
                self.send(preview)

        connection = sqlite3.connect(self.state_path)
        try:
            row = connection.execute(
                "SELECT status, error_code FROM plain_email_sends"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("EMAIL_FAILED", "GMAIL_SEND_FAILED"))
        self.assertTrue(self.log_path.exists())
        log_text = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn("Plain subject", log_text)
        self.assertNotIn("Plain body", log_text)

    def test_uncertain_delivery_persists_safe_diagnostic_reason(self) -> None:
        preview = self.preview()
        uncertain = GmailDeliveryUncertainError(
            "transport failed",
            reason="TRANSPORT_TIMEOUT",
            cause_type="TimeoutError",
            diagnostic_detail="connection timed out",
        )
        with patch.object(plain, "send_message", side_effect=uncertain):
            with self.assertRaisesRegex(
                EmailServiceError, "Diagnostic: TRANSPORT_TIMEOUT"
            ):
                self.send(preview)

        connection = sqlite3.connect(self.state_path)
        try:
            row = connection.execute(
                """
                SELECT status, error_code, diagnostic_code,
                       diagnostic_type, diagnostic_detail
                FROM plain_email_sends
                """
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            row,
            (
                "DELIVERY_UNCERTAIN",
                "GMAIL_DELIVERY_UNCERTAIN",
                "TRANSPORT_TIMEOUT",
                "TimeoutError",
                "connection timed out",
            ),
        )
        log_text = self.log_path.read_text(encoding="utf-8")
        self.assertIn('"diagnostic_code":"TRANSPORT_TIMEOUT"', log_text)
        self.assertIn('"diagnostic_type":"TimeoutError"', log_text)
        self.assertNotIn("Plain subject", log_text)
        self.assertNotIn("Plain body", log_text)

    def test_auth_failure_persists_safe_diagnostic_reason(self) -> None:
        preview = self.preview()
        auth_error = GmailAuthenticationError(
            "refresh failed",
            reason="TOKEN_REFRESH_FAILED",
            cause_type="TransportError",
            diagnostic_detail="oauth endpoint unavailable",
        )
        with self.assertRaisesRegex(
            EmailServiceError, "Diagnostic: TOKEN_REFRESH_FAILED"
        ):
            self.send(
                preview,
                gmail_service_factory=lambda: (_ for _ in ()).throw(
                    auth_error
                ),
            )

        connection = sqlite3.connect(self.state_path)
        try:
            row = connection.execute(
                """
                SELECT status, error_code, diagnostic_code,
                       diagnostic_type, diagnostic_detail
                FROM plain_email_sends
                """
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            row,
            (
                "EMAIL_FAILED",
                "GMAIL_AUTH_FAILED",
                "TOKEN_REFRESH_FAILED",
                "TransportError",
                "oauth endpoint unavailable",
            ),
        )


if __name__ == "__main__":
    unittest.main()
