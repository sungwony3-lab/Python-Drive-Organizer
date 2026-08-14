import re
from pathlib import Path


PARSER_VERSION = "MVP05-PARSER-1"

MULTIPLE_SPACES = re.compile(r"\s+")
MULTIPLE_PAREN_SUFFIX = re.compile(r"\(\s*\d+\s*\)\s*\(\s*\d+\s*\)\s*$")
SINGLE_PAREN_COPY_SUFFIX = re.compile(
    r"^(?P<base>.+?) \((?P<number>[1-9]\d*)\)$"
)
KOREAN_COPY_SUFFIX = re.compile(
    r"^(?P<base>.+?)(?:\s*-\s*|\s+)복사본$", re.IGNORECASE
)
ENGLISH_COPY_SUFFIX = re.compile(
    r"^(?P<base>.+?)(?:\s*-\s*|\s+)copy$", re.IGNORECASE
)
PAREN_REVISION_SUFFIX = re.compile(
    r"^(?P<base>.+?)\s*\(\s*(?:REV|R)\s*\.?\s*(?P<number>\d+)\s*\)$",
    re.IGNORECASE,
)
PLAIN_REVISION_SUFFIX = re.compile(
    r"^(?P<base>.+?)(?:\s+|_|-)(?:REV|R)\s*\.?\s*(?P<number>\d+)$",
    re.IGNORECASE,
)


def collapse_spaces(value: str) -> str:
    return MULTIPLE_SPACES.sub(" ", value.strip())


def split_extension(name: str, extension: str | None = None) -> tuple[str, str]:
    if extension:
        suffix = f".{extension.lstrip('.')}"
        if name.lower().endswith(suffix.lower()):
            return name[: -len(suffix)], suffix

    suffix = Path(name).suffix
    if suffix:
        return name[: -len(suffix)], suffix
    return name, ""


def clean_base_name(value: str) -> str:
    cleaned = collapse_spaces(value).rstrip(" _-").strip()
    return cleaned or collapse_spaces(value)


def parse_filename(name: str, extension: str | None = None) -> dict:
    cleaned_name = collapse_spaces(name)
    normalized_name = cleaned_name.lower()
    stem, _ = split_extension(cleaned_name, extension)
    working_name = collapse_spaces(stem)

    revision_type = "NONE"
    revision_number = None
    copy_type = "NONE"
    copy_number = None
    auto_action = "NONE"

    if not MULTIPLE_PAREN_SUFFIX.search(working_name):
        match = SINGLE_PAREN_COPY_SUFFIX.fullmatch(working_name)
        if match:
            working_name = clean_base_name(match.group("base"))
            copy_type = "SINGLE_PAREN_COPY"
            copy_number = int(match.group("number"))
            auto_action = "DELETE"
        else:
            match = KOREAN_COPY_SUFFIX.fullmatch(working_name)
            if match:
                working_name = clean_base_name(match.group("base"))
                copy_type = "KOREAN_COPY"
            else:
                match = ENGLISH_COPY_SUFFIX.fullmatch(working_name)
                if match:
                    working_name = clean_base_name(match.group("base"))
                    copy_type = "ENGLISH_COPY"

    revision_match = PAREN_REVISION_SUFFIX.fullmatch(working_name)
    if revision_match is None:
        revision_match = PLAIN_REVISION_SUFFIX.fullmatch(working_name)

    if revision_match:
        working_name = clean_base_name(revision_match.group("base"))
        revision_type = "REVISION"
        revision_number = int(revision_match.group("number"))

    return {
        "normalized_name": normalized_name,
        "base_name": clean_base_name(working_name),
        "revision_type": revision_type,
        "revision_number": revision_number,
        "copy_type": copy_type,
        "copy_number": copy_number,
        "auto_action": auto_action,
        "parser_version": PARSER_VERSION,
    }
