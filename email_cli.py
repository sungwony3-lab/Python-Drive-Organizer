import argparse
import json
import sys
import uuid
from pathlib import Path

from drive_download_client import (
    TOKEN_FILE as DRIVE_DOWNLOAD_TOKEN_FILE,
    DriveDownloadAuthenticationError,
    build_drive_download_service,
)
from email_service import EmailServiceError, prepare_email_file, send_prepared_email
from gmail_client import (
    TOKEN_FILE as GMAIL_SEND_TOKEN_FILE,
    GmailAuthenticationError,
    build_gmail_service,
)


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send one indexed Drive blob file through Gmail after confirmation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "authorize-drive",
        help="Create or refresh the dedicated drive.readonly token.",
    )
    subparsers.add_parser(
        "authorize-gmail",
        help="Create or refresh the dedicated gmail.send token.",
    )

    send_parser = subparsers.add_parser(
        "send",
        help="Validate, preview, confirm, then send one attachment.",
    )
    send_parser.add_argument("--file-id", required=True)
    send_parser.add_argument("--to", required=True)
    send_parser.add_argument("--subject", required=True)
    body_group = send_parser.add_mutually_exclusive_group()
    body_group.add_argument("--body")
    body_group.add_argument("--body-file", type=Path)
    send_parser.add_argument("--idempotency-key")
    return parser


def read_body(arguments: argparse.Namespace) -> str:
    if arguments.body is not None:
        return arguments.body
    if arguments.body_file is not None:
        return arguments.body_file.read_text(encoding="utf-8")
    return input("Plain-text body: ")


def attachment_size_text(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "unknown (hard 18 MiB download cap will apply)"
    return f"{size_bytes:,} bytes ({size_bytes / 1024 / 1024:.2f} MiB)"


def run(arguments: list[str] | None = None) -> int:
    configure_console_encoding()
    parsed = build_parser().parse_args(arguments)

    try:
        if parsed.command == "authorize-drive":
            build_drive_download_service()
            print(
                "Drive download OAuth ready: "
                f"{DRIVE_DOWNLOAD_TOKEN_FILE.name} (drive.readonly)"
            )
            return 0

        if parsed.command == "authorize-gmail":
            build_gmail_service()
            print(
                "Gmail send OAuth ready: "
                f"{GMAIL_SEND_TOKEN_FILE.name} (gmail.send)"
            )
            print("Gmail API activation is confirmed only by an actual send response.")
            return 0

        body = read_body(parsed)
        idempotency_key = parsed.idempotency_key or f"EMAIL-{uuid.uuid4()}"
        drive_service = build_drive_download_service()
        prepared = prepare_email_file(
            drive_service=drive_service,
            file_id=parsed.file_id,
            recipient=parsed.to,
            subject=parsed.subject,
            body=body,
            idempotency_key=idempotency_key,
        )

        print("\n=== EMAIL SEND CONFIRMATION ===")
        print(f"Recipient:  {prepared.recipient}")
        print(f"Subject:    {prepared.subject}")
        print(f"Attachment: {prepared.file_name}")
        print(f"Size:       {attachment_size_text(prepared.size_bytes)}")
        print(f"Request ID: {prepared.idempotency_key}")
        confirmation = input("Type SEND to transmit exactly this message: ").strip()
        if confirmation != "SEND":
            print("Cancelled. Gmail OAuth and email sending were not started.")
            return 2

        gmail_service = build_gmail_service()
        result = send_prepared_email(
            prepared=prepared,
            drive_service=drive_service,
            gmail_service=gmail_service,
        )
        print(
            json.dumps(
                {
                    "status": result.status,
                    "message_id": result.message_id,
                    "file_id": result.file_id,
                    "file_name": result.file_name,
                    "recipient": result.recipient,
                    "attachment_size": result.attachment_size,
                    "idempotent_replay": result.idempotent_replay,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except DriveDownloadAuthenticationError:
        print(
            "DRIVE_AUTH_FAILED: Dedicated Drive download OAuth failed.",
            file=sys.stderr,
        )
    except GmailAuthenticationError:
        print(
            "GMAIL_AUTH_FAILED: Dedicated Gmail send OAuth failed.",
            file=sys.stderr,
        )
    except EmailServiceError as error:
        print(str(error), file=sys.stderr)
    except (OSError, ValueError) as error:
        print(f"LOCAL_INPUT_FAILED: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
