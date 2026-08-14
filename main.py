import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from google.auth.exceptions import GoogleAuthError
from google_auth_oauthlib.flow import WSGITimeoutError
from googleapiclient.errors import HttpError

from database import (
    DATABASE_PATH,
    complete_scan,
    connect_database,
    fail_scan,
    initialize_schema,
    load_files_needing_parser,
    start_scan,
    update_file_parser_results,
)
from drive_client import authenticate
from file_grouping import rebuild_file_groups
from name_parser import PARSER_VERSION, parse_filename
from scanner import ScanStatistics, index_drive, utc_timestamp
from search_service import DEFAULT_LIMIT, SearchResult, SearchService


SEARCH_MODE_NAMES = (
    "search_name",
    "search_folder",
    "list_folder",
    "search_revisions",
    "search_copies",
    "search_auto_delete",
    "search_groups",
    "recent",
    "changed_in_scan",
    "tree",
)


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
        return f"입력 또는 데이터 설정 오류: {error}"
    if isinstance(error, OSError):
        return f"네트워크 또는 파일 시스템 오류: {error}"
    return f"예기치 않은 오류: {error}"


def backfill_parser_results(connection: sqlite3.Connection) -> int:
    rows = load_files_needing_parser(connection, PARSER_VERSION)
    records = [
        {
            "file_id": row["file_id"],
            **parse_filename(row["name"], row["extension"]),
        }
        for row in rows
    ]
    update_file_parser_results(connection, records)
    connection.commit()
    return len(records)


def positive_integer(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("1 이상의 정수를 입력하세요.")
    return number


def nonnegative_integer(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("0 이상의 정수를 입력하세요.")
    return number


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Google Drive SQLite index scanner and read-only search"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--parse-only", action="store_true")
    modes.add_argument("--group-only", action="store_true")
    modes.add_argument("--search-name", metavar="QUERY")
    modes.add_argument("--search-folder", metavar="QUERY")
    modes.add_argument("--list-folder", metavar="FOLDER_ID")
    modes.add_argument("--search-revisions", action="store_true")
    modes.add_argument("--search-copies", action="store_true")
    modes.add_argument("--search-auto-delete", action="store_true")
    modes.add_argument("--search-groups", action="store_true")
    modes.add_argument(
        "--recent",
        nargs="?",
        const=20,
        type=positive_integer,
        metavar="N",
    )
    modes.add_argument("--changed-in-scan", metavar="SCAN_ID")
    modes.add_argument("--tree", action="store_true")

    parser.add_argument("--limit", type=positive_integer)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--min-revision", type=nonnegative_integer)
    parser.add_argument("--min-members", type=positive_integer)
    parser.add_argument("--root-folder", metavar="FOLDER_ID")
    parser.add_argument("--max-depth", type=nonnegative_integer)
    parser.add_argument("--include-files", action="store_true")
    parser.add_argument("--output", metavar="PATH")

    parsed = parser.parse_args(arguments)
    search_mode = any(getattr(parsed, name) not in (None, False) for name in SEARCH_MODE_NAMES)

    limit_modes = (
        parsed.search_name is not None
        or parsed.search_folder is not None
        or parsed.list_folder is not None
        or parsed.search_revisions
        or parsed.search_copies
        or parsed.search_auto_delete
        or parsed.search_groups
        or parsed.changed_in_scan is not None
    )
    if parsed.limit is not None and not limit_modes:
        parser.error("--limit은 결과 목록 검색 모드에서만 사용할 수 있습니다.")
    if parsed.recursive and parsed.list_folder is None:
        parser.error("--recursive는 --list-folder와 함께 사용하세요.")
    if parsed.min_revision is not None and not parsed.search_revisions:
        parser.error("--min-revision은 --search-revisions와 함께 사용하세요.")
    if parsed.min_members is not None and not parsed.search_groups:
        parser.error("--min-members는 --search-groups와 함께 사용하세요.")
    tree_options = (
        parsed.root_folder is not None
        or parsed.max_depth is not None
        or parsed.include_files
        or parsed.output is not None
    )
    if tree_options and not parsed.tree:
        parser.error(
            "--root-folder, --max-depth, --include-files, --output은 "
            "--tree와 함께 사용하세요."
        )
    if parsed.search_name is not None and not parsed.search_name.strip():
        parser.error("--search-name 검색어는 비어 있을 수 없습니다.")
    if parsed.search_folder is not None and not parsed.search_folder.strip():
        parser.error("--search-folder 검색어는 비어 있을 수 없습니다.")
    if not search_mode and any(
        (
            parsed.limit,
            parsed.recursive,
            parsed.min_revision,
            parsed.min_members,
            parsed.root_folder,
            parsed.max_depth,
            parsed.include_files,
            parsed.output,
        )
    ):
        parser.error("검색 또는 Tree 모드를 지정하세요.")
    return parsed


def display_value(value: object) -> str:
    return "NULL" if value is None else str(value)


def print_search_result(result: SearchResult, fields: tuple[str, ...]) -> None:
    print(f"Total matched: {result.total}")
    print(f"Showing: {result.showing}")
    for index, item in enumerate(result.items, start=1):
        print()
        print(f"[{index}]")
        for field in fields:
            print(f"{field}: {display_value(item.get(field))}")


def print_group_result(result: SearchResult) -> None:
    print(f"Total matched: {result.total}")
    print(f"Showing: {result.showing}")
    for index, group in enumerate(result.items, start=1):
        print()
        print(f"[{index}] GROUP")
        for field in (
            "group_id",
            "folder_path",
            "group_base_name",
            "extension",
            "member_count",
            "revision_count",
            "copy_count",
            "auto_delete_count",
            "latest_revision_number",
        ):
            print(f"{field}: {display_value(group.get(field))}")
        print("members:")
        for member in group["members"]:
            print(
                "  - "
                f"name={member['name']} | member_type={member['member_type']} | "
                f"revision_number={display_value(member['revision_number'])} | "
                f"copy_number={display_value(member['copy_number'])} | "
                f"auto_action={member['auto_action']}"
            )


def run_search_mode(
    connection: sqlite3.Connection, arguments: argparse.Namespace
) -> None:
    service = SearchService(connection)
    limit = arguments.limit or DEFAULT_LIMIT

    if arguments.search_name is not None:
        print_search_result(
            service.search_name(arguments.search_name, limit),
            (
                "file_id",
                "name",
                "path",
                "extension",
                "modified_time",
                "revision_type",
                "revision_number",
                "copy_type",
                "copy_number",
                "auto_action",
                "group_id",
            ),
        )
    elif arguments.search_folder is not None:
        print_search_result(
            service.search_folders(arguments.search_folder, limit),
            ("folder_id", "name", "path", "parent_id"),
        )
    elif arguments.list_folder is not None:
        print_search_result(
            service.list_folder(
                arguments.list_folder, arguments.recursive, limit
            ),
            (
                "item_type",
                "item_id",
                "name",
                "path",
                "parent_id",
                "extension",
                "modified_time",
            ),
        )
    elif arguments.search_revisions:
        print_search_result(
            service.search_revisions(limit, arguments.min_revision),
            (
                "file_id",
                "name",
                "path",
                "revision_number",
                "group_id",
                "latest_revision_number",
            ),
        )
    elif arguments.search_copies:
        print_search_result(
            service.search_copies(limit),
            (
                "file_id",
                "name",
                "path",
                "copy_type",
                "copy_number",
                "auto_action",
                "group_id",
            ),
        )
    elif arguments.search_auto_delete:
        print("AUTO-DELETE CLASSIFICATION ONLY")
        print("NO DRIVE ACTION EXECUTED")
        print()
        print_search_result(
            service.search_auto_delete(limit),
            (
                "file_id",
                "name",
                "path",
                "copy_type",
                "copy_number",
                "group_id",
                "auto_action",
            ),
        )
    elif arguments.search_groups:
        print_group_result(
            service.search_groups(arguments.min_members or 1, limit)
        )
    elif arguments.recent is not None:
        print_search_result(
            service.recent(arguments.recent),
            ("name", "path", "modified_time", "file_id"),
        )
    elif arguments.changed_in_scan is not None:
        print("CURRENT ROWS ONLY; DELETED ITEM DETAILS ARE NOT RETAINED")
        print()
        print_search_result(
            service.changed_in_scan(arguments.changed_in_scan, limit),
            ("item_type", "item_id", "name", "path", "modified_time"),
        )
    elif arguments.tree:
        result = service.render_tree(
            arguments.root_folder,
            arguments.max_depth,
            arguments.include_files,
        )
        output = (
            f"{result.text}\n\n"
            f"Folders shown: {len(result.folder_ids)}\n"
            f"Files shown: {len(result.file_ids)}"
        )
        print(output)
        if arguments.output is not None:
            output_path = Path(arguments.output)
            output_path.write_text(f"{output}\n", encoding="utf-8")
            print(f"Tree saved: {output_path.resolve()}")


def main() -> int:
    configure_console_encoding()
    arguments = parse_arguments()
    search_mode = any(
        getattr(arguments, name) not in (None, False)
        for name in SEARCH_MODE_NAMES
    )

    if search_mode:
        connection = None
        try:
            connection = connect_database(read_only=True)
            run_search_mode(connection, arguments)
            return 0
        except (OSError, sqlite3.Error, ValueError) as error:
            print(error_message(error), file=sys.stderr)
            return 1
        finally:
            if connection is not None:
                connection.close()

    scan_id = generate_scan_id()
    started_at = utc_timestamp()
    connection = None
    statistics = ScanStatistics()

    try:
        connection = connect_database()
        initialize_schema(connection)
    except (OSError, sqlite3.Error) as error:
        print(error_message(error), file=sys.stderr)
        if connection is not None:
            connection.close()
        return 1

    if arguments.group_only:
        try:
            grouping = rebuild_file_groups(connection)
            print("File grouping: COMPLETED")
            print(f"Files: {grouping.files_count}")
            print(f"Groups: {grouping.groups_count}")
            print(f"Members: {grouping.members_count}")
            print(f"Database: {DATABASE_PATH}")
            return 0
        except Exception as error:
            print(error_message(error), file=sys.stderr)
            return 1
        finally:
            connection.close()

    try:
        parser_rows_updated = backfill_parser_results(connection)
    except (OSError, sqlite3.Error, ValueError) as error:
        print(error_message(error), file=sys.stderr)
        connection.close()
        return 1

    if arguments.parse_only:
        print(f"Parser version: {PARSER_VERSION}")
        print(f"Rows parsed: {parser_rows_updated}")
        print(f"Database: {DATABASE_PATH}")
        connection.close()
        return 0

    try:
        start_scan(connection, scan_id, started_at)
    except sqlite3.Error as error:
        print(error_message(error), file=sys.stderr)
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
