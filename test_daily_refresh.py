import io
import logging
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import daily_refresh
from contacts_sync import ContactSyncRunResult, ContactSyncStatistics


class DailyRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log_stream = io.StringIO()
        self.logger = logging.getLogger(f"daily-refresh-test-{id(self)}")
        self.logger.handlers.clear()
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        handler = logging.StreamHandler(self.log_stream)
        handler.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))
        self.logger.addHandler(handler)
        self.connection = Mock()
        self.events: list[str] = []

    def contact_result(
        self,
        status: str = "COMPLETED",
        *,
        exit_code: int = 0,
        error_code: str | None = None,
    ) -> ContactSyncRunResult:
        return ContactSyncRunResult(
            exit_code=exit_code,
            sync_id="CONTACTS-TEST-0001",
            status=status,
            statistics=ContactSyncStatistics(
                rows_seen=16,
                valid_rows=16,
                unchanged=16,
                invalid=int(status == "COMPLETED_WITH_WARNINGS"),
            ),
            error_code=error_code,
        )

    def patched_drive(self):
        return [
            patch.object(
                daily_refresh,
                "connect_database",
                return_value=self.connection,
            ),
            patch.object(
                daily_refresh,
                "initialize_schema",
                side_effect=lambda connection: self.events.append("schema"),
            ),
            patch.object(
                daily_refresh,
                "start_scan",
                side_effect=lambda *args: self.events.append("start_scan"),
            ),
            patch.object(
                daily_refresh,
                "authenticate",
                side_effect=lambda: self.events.append("authenticate") or object(),
            ),
            patch.object(
                daily_refresh,
                "index_drive",
                side_effect=self.fake_index_drive,
            ),
            patch.object(
                daily_refresh,
                "backfill_parser_results",
                side_effect=lambda connection: self.events.append("parser") or 3,
            ),
            patch.object(
                daily_refresh,
                "rebuild_file_groups",
                side_effect=lambda connection: self.events.append("grouping")
                or SimpleNamespace(
                    files_count=5,
                    groups_count=4,
                    members_count=5,
                ),
            ),
            patch.object(
                daily_refresh,
                "complete_scan",
                side_effect=lambda *args: self.events.append("complete_scan"),
            ),
            patch.object(
                daily_refresh,
                "fail_scan",
                side_effect=lambda *args: self.events.append("fail_scan"),
            ),
        ]

    def fake_index_drive(self, connection, credentials, scan_id, statistics):
        self.events.append("drive_sync")
        statistics.files_seen = 5
        statistics.folders_seen = 2
        statistics.files_inserted = 1
        statistics.files_updated = 1
        statistics.files_skipped = 3
        statistics.folders_skipped = 2
        return statistics

    def run_with_patches(
        self,
        *,
        contact_result: ContactSyncRunResult | None = None,
        drive_replacements: dict[int, object] | None = None,
        contacts_side_effect=None,
    ) -> int:
        patches = self.patched_drive()
        for index, replacement in (drive_replacements or {}).items():
            patches[index] = replacement
        result = contact_result or self.contact_result()

        def run_contacts(logger):
            self.events.append("contacts")
            if contacts_side_effect is not None:
                raise contacts_side_effect
            return result

        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            stack.enter_context(
                patch.object(
                    daily_refresh,
                    "run_contacts_pipeline",
                    side_effect=run_contacts,
                )
            )
            return daily_refresh.run_daily_refresh(self.logger)

    def test_both_pipelines_succeed_and_drive_order_is_unchanged(self) -> None:
        result = self.run_with_patches()

        self.assertEqual(result, 0)
        self.assertEqual(
            self.events,
            [
                "schema",
                "start_scan",
                "authenticate",
                "drive_sync",
                "parser",
                "grouping",
                "complete_scan",
                "contacts",
            ],
        )
        self.connection.close.assert_called_once()
        log = self.log_stream.getvalue()
        self.assertIn("DRIVE_PIPELINE_COMPLETED", log)
        self.assertIn("CONTACTS_PIPELINE_COMPLETED", log)
        self.assertIn("DAILY_REFRESH_COMPLETED", log)

    def test_drive_failure_stops_parser_but_contacts_still_runs(self) -> None:
        result = self.run_with_patches(
            drive_replacements={
                4: patch.object(
                    daily_refresh,
                    "index_drive",
                    side_effect=RuntimeError("drive failed"),
                )
            }
        )

        self.assertEqual(result, 1)
        self.assertNotIn("parser", self.events)
        self.assertNotIn("grouping", self.events)
        self.assertNotIn("complete_scan", self.events)
        self.assertIn("fail_scan", self.events)
        self.assertIn("contacts", self.events)
        self.assertIn("DRIVE_PIPELINE_FAILED", self.log_stream.getvalue())

    def test_parser_failure_stops_grouping_and_contacts_still_runs(self) -> None:
        result = self.run_with_patches(
            drive_replacements={
                5: patch.object(
                    daily_refresh,
                    "backfill_parser_results",
                    side_effect=ValueError("parser failed"),
                )
            }
        )

        self.assertEqual(result, 1)
        self.assertNotIn("grouping", self.events)
        self.assertNotIn("complete_scan", self.events)
        self.assertIn("fail_scan", self.events)
        self.assertIn("contacts", self.events)

    def test_grouping_failure_does_not_mark_drive_completed(self) -> None:
        result = self.run_with_patches(
            drive_replacements={
                6: patch.object(
                    daily_refresh,
                    "rebuild_file_groups",
                    side_effect=RuntimeError("grouping failed"),
                )
            }
        )

        self.assertEqual(result, 1)
        self.assertNotIn("complete_scan", self.events)
        self.assertIn("fail_scan", self.events)
        self.assertIn("contacts", self.events)

    def test_contacts_failure_keeps_drive_success_and_returns_nonzero(self) -> None:
        result = self.run_with_patches(
            contact_result=self.contact_result(
                "FAILED",
                exit_code=1,
                error_code="CONTACTS_READ_FAILED",
            )
        )

        self.assertEqual(result, 1)
        self.assertIn("complete_scan", self.events)
        log = self.log_stream.getvalue()
        self.assertIn("DRIVE_PIPELINE_COMPLETED", log)
        self.assertIn("CONTACTS_PIPELINE_FAILED", log)
        self.assertIn("DAILY_REFRESH_COMPLETED_WITH_ERRORS", log)

    def test_contacts_warning_is_successful_but_visible_in_summary(self) -> None:
        result = self.run_with_patches(
            contact_result=self.contact_result("COMPLETED_WITH_WARNINGS")
        )

        self.assertEqual(result, 0)
        log = self.log_stream.getvalue()
        self.assertIn("Contacts: COMPLETED_WITH_WARNINGS", log)
        self.assertIn("Exit: 0", log)

    def test_contacts_oauth_failure_is_isolated_from_drive(self) -> None:
        result = self.run_with_patches(
            contact_result=self.contact_result(
                "FAILED",
                exit_code=1,
                error_code="CONTACTS_OAUTH_INTERACTION_REQUIRED",
            )
        )

        self.assertEqual(result, 1)
        self.assertIn("complete_scan", self.events)
        self.assertIn(
            "CONTACTS_OAUTH_INTERACTION_REQUIRED",
            self.log_stream.getvalue(),
        )

    def test_unexpected_contacts_error_does_not_log_personal_data(self) -> None:
        result = self.run_with_patches(
            contacts_side_effect=RuntimeError(
                "홍길동 hong.private@example.com 010-1234-5678"
            )
        )

        self.assertEqual(result, 1)
        log = self.log_stream.getvalue()
        self.assertNotIn("홍길동", log)
        self.assertNotIn("hong.private@example.com", log)
        self.assertNotIn("010-1234-5678", log)
        self.assertIn("CONTACTS_UNEXPECTED_ERROR", log)

    def test_safe_log_message_redacts_known_secret_shapes(self) -> None:
        message = (
            "Authorization: Bearer top-secret-token "
            "PDO_API_KEY=another-secret "
            "refresh_token=refresh-secret"
        )
        sanitized = daily_refresh.safe_log_message(message)

        self.assertNotIn("top-secret-token", sanitized)
        self.assertNotIn("another-secret", sanitized)
        self.assertNotIn("refresh-secret", sanitized)
        self.assertEqual(sanitized.count("[REDACTED]"), 3)

    def test_logger_writes_utf8_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "daily_refresh.log"
            logger = daily_refresh.create_logger(log_path)
            logger.info("테스트 로그")
            for handler in logger.handlers:
                handler.flush()

            content = log_path.read_text(encoding="utf-8")
            self.assertIn("테스트 로그", content)
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()

    def test_other_working_directory_and_scheduled_command_are_supported(self) -> None:
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                result = self.run_with_patches()
            finally:
                os.chdir(original)

        self.assertEqual(result, 0)
        project_root = Path(daily_refresh.__file__).resolve().parent
        setup_script = (project_root / "setup_windows.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("-Arguments 'daily_refresh.py'", setup_script)
        self.assertTrue(daily_refresh.DATABASE_PATH.is_absolute())


if __name__ == "__main__":
    unittest.main()
