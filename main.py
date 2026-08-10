import sqlite3
import sys
from datetime import datetime

from google.auth.exceptions import GoogleAuthError
from google_auth_oauthlib.flow import WSGITimeoutError
from googleapiclient.errors import HttpError

from database import (
    DATABASE_PATH,
    complete_scan,
    connect_database,
    fail_scan,
    initialize_schema,
    start_scan,
)
from drive_client import authenticate
from scanner import ScanStatistics, index_drive, utc_timestamp


def configure_console_encoding() -> None:
    """Prevent Windows console encoding errors for Drive item names."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def generate_scan_id() -> str:
    return datetime.now().astimezone().strftime("SCAN-%Y%m%d-%H%M%S")


def error_message(error: Exception) -> str:
    if isinstance(error, FileNotFoundError):
        return f"파일 오류: {error}"
    if isinstance(error, WSGITimeoutError):
        return "OAuth 인증이 5분 안에 완료되지 않았습니다."
    if isinstance(error, GoogleAuthError):
        return f"Google OAuth 인증 오류: {error}"
    if isinstance(error, HttpError):
        return f"Google Drive API 오류: {error}"
    if isinstance(error, RuntimeError):
        return f"Drive 스캔 오류: {error}"
    if isinstance(error, sqlite3.Error):
        return f"SQLite 오류: {error}"
    if isinstance(error, ValueError):
        return f"데이터 또는 OAuth 설정 오류: {error}"
    if isinstance(error, OSError):
        return f"네트워크 또는 파일 시스템 오류: {error}"
    return f"예기치 않은 오류: {error}"


def main() -> int:
    configure_console_encoding()
    scan_id = generate_scan_id()
    started_at = utc_timestamp()
    connection = None
    statistics = ScanStatistics()

    try:
        connection = connect_database()
        initialize_schema(connection)
        start_scan(connection, scan_id, started_at)
    except (OSError, sqlite3.Error) as error:
        print(error_message(error), file=sys.stderr)
        if connection is not None:
            connection.close()
        return 1

    print(f"Drive 인덱스 스캔 시작: {scan_id}")

    try:
        credentials = authenticate()
        index_drive(connection, credentials, scan_id, statistics)
        complete_scan(
            connection,
            scan_id,
            utc_timestamp(),
            statistics.to_dict(),
        )
        print(f"Scan ID: {scan_id}")
        print("Status: COMPLETED")
        print()
        print(f"Files seen: {statistics.files_seen}")
        print(f"  inserted: {statistics.files_inserted}")
        print(f"  updated: {statistics.files_updated}")
        print(f"  skipped: {statistics.files_skipped}")
        print(f"  deleted: {statistics.files_deleted}")
        print()
        print(f"Folders seen: {statistics.folders_seen}")
        print(f"  inserted: {statistics.folders_inserted}")
        print(f"  updated: {statistics.folders_updated}")
        print(f"  skipped: {statistics.folders_skipped}")
        print(f"  deleted: {statistics.folders_deleted}")
        print()
        print(f"Database: {DATABASE_PATH}")
        return 0
    except Exception as error:
        connection.rollback()
        message = error_message(error)
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
            print(
                f"scan_state FAILED 기록 오류: {database_error}",
                file=sys.stderr,
            )
        print(message, file=sys.stderr)
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
