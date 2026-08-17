import sqlite3
import unicodedata
from dataclasses import dataclass


CONTACT_PUBLIC_COLUMNS = """
    contact_id, organization, name, title, email, phone,
    email_usable, conflict_code
"""
EXACT_FILTER_COLUMNS = {
    "organization": "normalized_organization",
    "name": "normalized_name",
    "title": "normalized_title",
    "email": "normalized_email",
}


@dataclass
class ContactSearchResult:
    total: int
    items: list[dict]

    @property
    def showing(self) -> int:
        return len(self.items)


def normalize_contact_search(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    return normalized or None


def normalize_email_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    return normalized or None


def public_contact(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["email_usable"] = bool(item["email_usable"])
    return item


class ContactsService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def search(
        self,
        *,
        q: str | None = None,
        organization: str | None = None,
        name: str | None = None,
        title: str | None = None,
        email: str | None = None,
        limit: int = 20,
    ) -> ContactSearchResult:
        normalized_q = normalize_contact_search(q)
        filters = {
            "organization": normalize_contact_search(organization),
            "name": normalize_contact_search(name),
            "title": normalize_contact_search(title),
            "email": normalize_email_filter(email),
        }
        if normalized_q is None and not any(filters.values()):
            raise ValueError("CONTACT_SEARCH_CRITERIA_REQUIRED")

        where_parts: list[str] = []
        parameters: list[object] = []
        if normalized_q is not None:
            where_parts.append(
                """
                (
                    instr(COALESCE(normalized_name, ''), ?) > 0
                    OR instr(COALESCE(normalized_organization, ''), ?) > 0
                    OR instr(COALESCE(normalized_title, ''), ?) > 0
                    OR instr(COALESCE(normalized_email, ''), ?) > 0
                )
                """
            )
            parameters.extend([normalized_q] * 4)

        for field, value in filters.items():
            if value is not None:
                where_parts.append(f"{EXACT_FILTER_COLUMNS[field]} = ?")
                parameters.append(value)

        where_sql = " AND ".join(where_parts)
        total = self.connection.execute(
            f"SELECT COUNT(*) FROM contacts WHERE {where_sql}",
            parameters,
        ).fetchone()[0]

        if normalized_q is None:
            rank_sql = "CASE WHEN contact_id IS NULL THEN 1 ELSE 1 END"
            rank_parameters: list[object] = []
        else:
            rank_sql = """
                CASE
                    WHEN normalized_email = ? THEN 1
                    WHEN normalized_name = ? THEN 2
                    WHEN substr(normalized_name, 1, length(?)) = ? THEN 3
                    ELSE 4
                END
            """
            rank_parameters = [
                normalized_q,
                normalized_q,
                normalized_q,
                normalized_q,
            ]

        rows = self.connection.execute(
            f"""
            SELECT {CONTACT_PUBLIC_COLUMNS}
            FROM contacts
            WHERE {where_sql}
            ORDER BY {rank_sql},
                     normalized_name, name COLLATE NOCASE, name,
                     COALESCE(normalized_organization, ''),
                     COALESCE(organization, '') COLLATE NOCASE,
                     COALESCE(organization, ''),
                     COALESCE(normalized_title, ''),
                     COALESCE(title, '') COLLATE NOCASE,
                     COALESCE(title, ''),
                     contact_id
            LIMIT ?
            """,
            (*parameters, *rank_parameters, limit),
        ).fetchall()
        return ContactSearchResult(total, [public_contact(row) for row in rows])

    def get_contact(self, contact_id: str) -> dict | None:
        row = self.connection.execute(
            f"""
            SELECT {CONTACT_PUBLIC_COLUMNS}
            FROM contacts
            WHERE contact_id = ?
            """,
            (contact_id,),
        ).fetchone()
        return public_contact(row) if row else None

    def status(self) -> dict:
        latest = self.connection.execute(
            """
            SELECT sync_id, status, rows_seen, valid_rows, inserted, updated,
                   deleted, unchanged, invalid, conflicts
            FROM contacts_sync_state
            ORDER BY started_at DESC, rowid DESC
            LIMIT 1
            """
        ).fetchone()
        last_success = self.connection.execute(
            """
            SELECT finished_at
            FROM contacts_sync_state
            WHERE status IN ('COMPLETED', 'COMPLETED_WITH_WARNINGS')
            ORDER BY finished_at DESC, started_at DESC, rowid DESC
            LIMIT 1
            """
        ).fetchone()

        return {
            "latest_sync_id": latest["sync_id"] if latest else None,
            "latest_sync_status": latest["status"] if latest else None,
            "last_success_at": (
                last_success["finished_at"] if last_success else None
            ),
            "rows_seen": latest["rows_seen"] if latest else 0,
            "valid_rows": latest["valid_rows"] if latest else 0,
            "inserted": latest["inserted"] if latest else 0,
            "updated": latest["updated"] if latest else 0,
            "deleted": latest["deleted"] if latest else 0,
            "unchanged": latest["unchanged"] if latest else 0,
            "invalid": latest["invalid"] if latest else 0,
            "conflicts": latest["conflicts"] if latest else 0,
        }
