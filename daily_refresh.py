import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from database import (
    DATABASE_PATH,
    PROJECT_ROOT,
    complete_scan,
    connect_database,
    fail_scan,
    initialize_schema,
    start_scan,
)
from contacts_sync import ContactSyncRunResult, execute_contacts_sync
from drive_client import authenticate
from file_grouping import rebuild_file_groups
from main import (
    backfill_parser_results,
    configure_console_encoding,
    error_message,
    generate_scan_id,
)
from name_parser import PARSER_VERSION
from scanner import ScanStatistics, index_drive, utc_timestamp


LOG_PATH = PROJECT_ROOT / "logs" / "daily_refresh.log"
LOGGER_NAME = "python_drive_organizer.daily_refresh"
SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+"),
    re.compile(r"(?i)(PDO_API_KEY\s*[=:]\s*)\S+"),
    re.compile(
        r"(?i)((?:access|refresh|cloudflare)[_-]?token\s*[=:]\s*)\S+"
    ),
)


def safe_log_message(message: str) -> str:
    sanitized = message.replace("\r", " ").replace("\n", " ")
    for pattern in SECRET_PATTERNS:
        sanitized = pattern.sub(r"\1[REDACTED]", sanitized)
    return sanitized


def create_logger(log_path: Path = LOG_PATH) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s%(timezone)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    class LocalTimezoneFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.timezone = datetime.now().astimezone().strftime("%z")
            return True

    timezone_filter = LocalTimezoneFilter()
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(timezone_filter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(timezone_filter)
    logger.addHandler(console_handler)
    return logger


def log_drive_statistics(
    logger: logging.Logger, statistics: ScanStatistics
) -> None:
    logger.info(
        "Drive sync result | "
        "files_seen=%d inserted=%d updated=%d skipped=%d deleted=%d | "
        "folders_seen=%d inserted=%d updated=%d skipped=%d deleted=%d",
        statistics.files_seen,
        statistics.files_inserted,
        statistics.files_updated,
        statistics.files_skipped,
        statistics.files_deleted,
        statistics.folders_seen,
        statistics.folders_inserted,
        statistics.folders_updated,
        statistics.folders_skipped,
        statistics.folders_deleted,
    )


def run_drive_pipeline(logger: logging.Logger) -> str:
    scan_id = generate_scan_id()
    started_at = utc_timestamp()
    statistics = ScanStatistics()
    connection: sqlite3.Connection | None = None
    scan_started = False

    try:
        connection = connect_database()
        initialize_schema(connection)
        start_scan(connection, scan_id, started_at)
        scan_started = True

        logger.info("Stage 1/3 Drive metadata sync started | scan_id=%s", scan_id)
        credentials = authenticate()
        index_drive(connection, credentials, scan_id, statistics)
        log_drive_statistics(logger, statistics)

        logger.info("Stage 2/3 filename Parser started | scan_id=%s", scan_id)
        parser_rows_updated = backfill_parser_results(connection)
        logger.info(
            "Parser result | version=%s rows_updated=%d",
            PARSER_VERSION,
            parser_rows_updated,
        )

        logger.info("Stage 3/3 File Grouping started | scan_id=%s", scan_id)
        grouping = rebuild_file_groups(connection)
        logger.info(
            "Grouping result | files=%d groups=%d members=%d",
            grouping.files_count,
            grouping.groups_count,
            grouping.members_count,
        )

        complete_scan(
            connection,
            scan_id,
            utc_timestamp(),
            statistics.to_dict(),
        )
        return "COMPLETED"
    except Exception as error:
        if connection is not None:
            connection.rollback()
        message = safe_log_message(error_message(error))
        if connection is not None and scan_started:
            try:
                fail_scan(
                    connection,
                    scan_id,
                    utc_timestamp(),
                    statistics.files_seen,
                    statistics.folders_seen,
                    message,
                )
            except sqlite3.Error as database_error:
                logger.error(
                    "scan_state FAILED record error | scan_id=%s | %s",
                    scan_id,
                    safe_log_message(str(database_error)),
                )
        raise RuntimeError(message) from error
    finally:
        if connection is not None:
            connection.close()


def run_contacts_pipeline(logger: logging.Logger) -> ContactSyncRunResult:
    return execute_contacts_sync(logger)


def log_contacts_pipeline_result(
    logger: logging.Logger, result: ContactSyncRunResult
) -> None:
    statistics = result.statistics
    logger.info(
        "CONTACTS_PIPELINE_COMPLETED | sync_id=%s status=%s "
        "rows_seen=%d inserted=%d updated=%d deleted=%d unchanged=%d "
        "invalid=%d conflicts=%d",
        result.sync_id,
        result.status,
        statistics.rows_seen,
        statistics.inserted,
        statistics.updated,
        statistics.deleted,
        statistics.unchanged,
        statistics.invalid,
        statistics.conflicts,
    )


def run_daily_refresh(logger: logging.Logger) -> int:
    local_started_at = datetime.now().astimezone()
    drive_status = "FAILED"
    contacts_status = "FAILED"

    logger.info("DAILY_REFRESH_START | database=%s", DATABASE_PATH)
    logger.info("DRIVE_PIPELINE_START")
    try:
        drive_status = run_drive_pipeline(logger)
        logger.info("DRIVE_PIPELINE_COMPLETED")
    except Exception as error:
        logger.error(
            "DRIVE_PIPELINE_FAILED | %s",
            safe_log_message(error_message(error)),
        )

    logger.info("CONTACTS_PIPELINE_START")
    try:
        contacts_result = run_contacts_pipeline(logger)
        contacts_status = contacts_result.status
        if contacts_result.exit_code == 0:
            log_contacts_pipeline_result(logger, contacts_result)
        else:
            logger.error(
                "CONTACTS_PIPELINE_FAILED | sync_id=%s error_code=%s",
                contacts_result.sync_id,
                contacts_result.error_code or "CONTACTS_UNEXPECTED_ERROR",
            )
    except Exception:
        logger.error(
            "CONTACTS_PIPELINE_FAILED | error_code=CONTACTS_UNEXPECTED_ERROR",
        )

    successful_contacts_statuses = {
        "COMPLETED",
        "COMPLETED_WITH_WARNINGS",
    }
    exit_code = int(
        drive_status != "COMPLETED"
        or contacts_status not in successful_contacts_statuses
    )
    if exit_code:
        logger.error("DAILY_REFRESH_COMPLETED_WITH_ERRORS")
    else:
        logger.info("DAILY_REFRESH_COMPLETED")

    logger.info("Daily Refresh Summary")
    logger.info("Drive: %s", drive_status)
    logger.info("Contacts: %s", contacts_status)
    logger.info("Exit: %d", exit_code)

    try:
        return exit_code
    finally:
        local_finished_at = datetime.now().astimezone()
        elapsed_seconds = (
            local_finished_at - local_started_at
        ).total_seconds()
        logger.info(
            "Daily Refresh finished | elapsed_seconds=%.3f",
            elapsed_seconds,
        )


def main() -> int:
    configure_console_encoding()
    logger = create_logger()
    return run_daily_refresh(logger)


if __name__ == "__main__":
    raise SystemExit(main())
