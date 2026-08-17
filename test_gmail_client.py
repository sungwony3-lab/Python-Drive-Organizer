import unittest

from gmail_client import GmailDeliveryUncertainError, send_message


class _ExecutableRequest:
    def __init__(self, *, response=None, error=None) -> None:
        self.response = response
        self.error = error

    def execute(self, *, num_retries):
        if self.error is not None:
            raise self.error
        return self.response


class _Messages:
    def __init__(self, request: _ExecutableRequest) -> None:
        self.request = request

    def send(self, *, userId, body):
        return self.request


class _Users:
    def __init__(self, request: _ExecutableRequest) -> None:
        self.request = request

    def messages(self):
        return _Messages(self.request)


class _Service:
    def __init__(self, request: _ExecutableRequest) -> None:
        self.request = request

    def users(self):
        return _Users(self.request)


class GmailClientDiagnosticTests(unittest.TestCase):
    def call_send(self, *, response=None, error=None):
        service = _Service(_ExecutableRequest(response=response, error=error))
        return send_message(service, b"safe test message")

    def test_timeout_has_specific_diagnostic_reason(self) -> None:
        with self.assertRaises(GmailDeliveryUncertainError) as raised:
            self.call_send(error=TimeoutError("connection timed out"))

        self.assertEqual(raised.exception.reason, "TRANSPORT_TIMEOUT")
        self.assertEqual(raised.exception.cause_type, "TimeoutError")
        self.assertIn("connection timed out", raised.exception.diagnostic_detail)

    def test_os_error_has_specific_diagnostic_reason_and_error_number(self) -> None:
        with self.assertRaises(GmailDeliveryUncertainError) as raised:
            self.call_send(error=OSError(10054, "connection reset"))

        self.assertEqual(raised.exception.reason, "TRANSPORT_OS_ERROR")
        self.assertEqual(raised.exception.cause_type, "ConnectionResetError")
        self.assertIn("errno=10054", raised.exception.diagnostic_detail)

    def test_response_without_id_records_only_shape(self) -> None:
        with self.assertRaises(GmailDeliveryUncertainError) as raised:
            self.call_send(response={"threadId": "secret-thread-id"})

        self.assertEqual(raised.exception.reason, "RESPONSE_MISSING_ID")
        self.assertEqual(raised.exception.cause_type, "dict")
        self.assertIn("threadId", raised.exception.diagnostic_detail)
        self.assertNotIn("secret-thread-id", raised.exception.diagnostic_detail)

    def test_success_returns_message_id(self) -> None:
        self.assertEqual(
            self.call_send(response={"id": "MESSAGE-ID"}),
            "MESSAGE-ID",
        )


if __name__ == "__main__":
    unittest.main()
