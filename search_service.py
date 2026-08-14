from dataclasses import dataclass
import sqlite3


COPY_TYPES = ("SINGLE_PAREN_COPY", "KOREAN_COPY", "ENGLISH_COPY")
DEFAULT_LIMIT = 100


@dataclass
class SearchResult:
    total: int
    items: list[dict]

    @property
    def showing(self) -> int:
        return len(self.items)


@dataclass
class TreeResult:
    text: str
    folder_ids: set[str]
    file_ids: set[str]


def stable_name_key(item: dict) -> tuple[str, str, str]:
    return (
        (item.get("name") or "").casefold(),
        item.get("name") or "",
        item.get("folder_id") or item.get("file_id") or "",
    )


class FolderIndex:
    def __init__(self, folders: list[dict]) -> None:
        self.folders = {folder["folder_id"]: folder for folder in folders}
        self.children: dict[str | None, list[dict]] = {}
        for folder in folders:
            self.children.setdefault(folder.get("parent_id"), []).append(folder)
        for children in self.children.values():
            children.sort(key=stable_name_key)
        self._path_cache: dict[str | None, str] = {None: "/"}

    def folder_path(self, folder_id: str | None) -> str:
        if folder_id in self._path_cache:
            return self._path_cache[folder_id]
        if folder_id not in self.folders:
            path = f"/[MISSING_PARENT:{folder_id}]"
            self._path_cache[folder_id] = path
            return path

        names = []
        seen = set()
        current_id = folder_id
        prefix = ""

        while current_id is not None:
            if current_id in seen:
                prefix = f"[CYCLE:{current_id}]"
                break
            seen.add(current_id)
            folder = self.folders.get(current_id)
            if folder is None:
                prefix = f"[MISSING_PARENT:{current_id}]"
                break
            names.append(folder["name"])
            current_id = folder.get("parent_id")

        parts = ([prefix] if prefix else []) + list(reversed(names))
        path = "/" + "/".join(parts)
        self._path_cache[folder_id] = path
        return path

    def file_path(self, parent_id: str | None, name: str) -> str:
        folder_path = self.folder_path(parent_id)
        return f"/{name}" if folder_path == "/" else f"{folder_path}/{name}"

    def descendants(self, root_folder_id: str, recursive: bool) -> list[dict]:
        if root_folder_id not in self.folders:
            raise ValueError(f"folder_id를 찾을 수 없습니다: {root_folder_id}")

        results = []
        visited = {root_folder_id}
        pending = list(self.children.get(root_folder_id, ()))
        while pending:
            folder = pending.pop(0)
            folder_id = folder["folder_id"]
            if folder_id in visited:
                continue
            visited.add(folder_id)
            results.append(folder)
            if recursive:
                pending.extend(self.children.get(folder_id, ()))
        return results


class SearchService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        folders = [
            dict(row)
            for row in connection.execute(
                "SELECT folder_id, name, parent_id FROM folders"
            )
        ]
        self.folder_index = FolderIndex(folders)

    @staticmethod
    def _file_record(row: sqlite3.Row, folder_index: FolderIndex) -> dict:
        record = dict(row)
        record["path"] = folder_index.file_path(
            record.get("parent_id"), record["name"]
        )
        return record

    def search_name(self, query: str, limit: int = DEFAULT_LIMIT) -> SearchResult:
        needle = query.strip().lower()
        where = """
            instr(lower(f.name), ?) > 0
            OR instr(COALESCE(f.normalized_name, ''), ?) > 0
            OR instr(lower(COALESCE(f.base_name, '')), ?) > 0
        """
        parameters = (needle, needle, needle)
        total = self.connection.execute(
            f"SELECT COUNT(*) FROM files f WHERE {where}", parameters
        ).fetchone()[0]
        rows = self.connection.execute(
            f"""
            SELECT f.file_id, f.name, f.parent_id, f.extension,
                   f.modified_time, f.revision_type, f.revision_number,
                   f.copy_type, f.copy_number, f.auto_action, m.group_id
            FROM files f
            LEFT JOIN file_group_members m ON m.file_id = f.file_id
            WHERE {where}
            ORDER BY f.name COLLATE NOCASE, f.name, f.file_id
            LIMIT ?
            """,
            (*parameters, limit),
        ).fetchall()
        return SearchResult(
            total,
            [self._file_record(row, self.folder_index) for row in rows],
        )

    def search_folders(
        self, query: str, limit: int = DEFAULT_LIMIT
    ) -> SearchResult:
        needle = query.strip().lower()
        total = self.connection.execute(
            "SELECT COUNT(*) FROM folders WHERE instr(lower(name), ?) > 0",
            (needle,),
        ).fetchone()[0]
        rows = self.connection.execute(
            """
            SELECT folder_id, name, parent_id
            FROM folders
            WHERE instr(lower(name), ?) > 0
            ORDER BY name COLLATE NOCASE, name, folder_id
            LIMIT ?
            """,
            (needle, limit),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["path"] = self.folder_index.folder_path(item["folder_id"])
            items.append(item)
        return SearchResult(total, items)

    def list_folder(
        self,
        folder_id: str,
        recursive: bool = False,
        limit: int = DEFAULT_LIMIT,
    ) -> SearchResult:
        folders = self.folder_index.descendants(folder_id, recursive)
        selected_folder_ids = {folder_id}
        if recursive:
            selected_folder_ids.update(folder["folder_id"] for folder in folders)

        file_rows = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT file_id, name, parent_id, extension, modified_time
                FROM files
                ORDER BY name COLLATE NOCASE, name, file_id
                """
            )
            if row["parent_id"] in selected_folder_ids
        ]
        if not recursive:
            folders = list(self.folder_index.children.get(folder_id, ()))

        items = [
            {
                "item_type": "FOLDER",
                "item_id": folder["folder_id"],
                "name": folder["name"],
                "path": self.folder_index.folder_path(folder["folder_id"]),
                "parent_id": folder.get("parent_id"),
            }
            for folder in folders
        ]
        items.extend(
            {
                "item_type": "FILE",
                "item_id": file["file_id"],
                "name": file["name"],
                "path": self.folder_index.file_path(
                    file.get("parent_id"), file["name"]
                ),
                "parent_id": file.get("parent_id"),
                "extension": file.get("extension"),
                "modified_time": file.get("modified_time"),
            }
            for file in file_rows
        )
        items.sort(
            key=lambda item: (
                item["path"].casefold(),
                item["item_type"],
                item["item_id"],
            )
        )
        return SearchResult(len(items), items[:limit])

    def search_revisions(
        self, limit: int = DEFAULT_LIMIT, min_revision: int | None = None
    ) -> SearchResult:
        where = "(f.revision_type = 'REVISION' OR f.revision_number IS NOT NULL)"
        parameters: list[object] = []
        if min_revision is not None:
            where += " AND f.revision_number >= ?"
            parameters.append(min_revision)
        total = self.connection.execute(
            f"SELECT COUNT(*) FROM files f WHERE {where}", parameters
        ).fetchone()[0]
        rows = self.connection.execute(
            f"""
            SELECT f.file_id, f.name, f.parent_id, f.revision_number,
                   m.group_id, g.latest_revision_number
            FROM files f
            LEFT JOIN file_group_members m ON m.file_id = f.file_id
            LEFT JOIN file_groups g ON g.group_id = m.group_id
            WHERE {where}
            ORDER BY f.revision_number DESC, f.name COLLATE NOCASE, f.file_id
            LIMIT ?
            """,
            (*parameters, limit),
        ).fetchall()
        return SearchResult(
            total,
            [self._file_record(row, self.folder_index) for row in rows],
        )

    def search_copies(self, limit: int = DEFAULT_LIMIT) -> SearchResult:
        placeholders = ",".join("?" for _ in COPY_TYPES)
        total = self.connection.execute(
            f"SELECT COUNT(*) FROM files WHERE copy_type IN ({placeholders})",
            COPY_TYPES,
        ).fetchone()[0]
        rows = self.connection.execute(
            f"""
            SELECT f.file_id, f.name, f.parent_id, f.copy_type, f.copy_number,
                   f.auto_action, m.group_id
            FROM files f
            LEFT JOIN file_group_members m ON m.file_id = f.file_id
            WHERE f.copy_type IN ({placeholders})
            ORDER BY f.name COLLATE NOCASE, f.file_id
            LIMIT ?
            """,
            (*COPY_TYPES, limit),
        ).fetchall()
        return SearchResult(
            total,
            [self._file_record(row, self.folder_index) for row in rows],
        )

    def search_auto_delete(self, limit: int = DEFAULT_LIMIT) -> SearchResult:
        total = self.connection.execute(
            "SELECT COUNT(*) FROM files WHERE auto_action = 'DELETE'"
        ).fetchone()[0]
        rows = self.connection.execute(
            """
            SELECT f.file_id, f.name, f.parent_id, f.copy_type, f.copy_number,
                   m.group_id, f.auto_action
            FROM files f
            LEFT JOIN file_group_members m ON m.file_id = f.file_id
            WHERE f.auto_action = 'DELETE'
            ORDER BY f.name COLLATE NOCASE, f.file_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return SearchResult(
            total,
            [self._file_record(row, self.folder_index) for row in rows],
        )

    def search_groups(
        self, min_members: int = 1, limit: int = DEFAULT_LIMIT
    ) -> SearchResult:
        total = self.connection.execute(
            "SELECT COUNT(*) FROM file_groups WHERE member_count >= ?",
            (min_members,),
        ).fetchone()[0]
        group_rows = self.connection.execute(
            """
            SELECT group_id, parent_id, group_base_name, extension,
                   member_count, revision_count, copy_count,
                   auto_delete_count, latest_revision_number
            FROM file_groups
            WHERE member_count >= ?
            ORDER BY member_count DESC, group_base_name COLLATE NOCASE,
                     group_id
            LIMIT ?
            """,
            (min_members, limit),
        ).fetchall()
        items = []
        selected_ids = {row["group_id"] for row in group_rows}
        members: dict[str, list[dict]] = {group_id: [] for group_id in selected_ids}
        if selected_ids:
            for row in self.connection.execute(
                """
                SELECT m.group_id, f.file_id, f.name, m.member_type,
                       m.revision_number, m.copy_number, m.auto_action
                FROM file_group_members m
                JOIN files f ON f.file_id = m.file_id
                ORDER BY m.group_id, f.name COLLATE NOCASE, f.name, f.file_id
                """
            ):
                if row["group_id"] in selected_ids:
                    members[row["group_id"]].append(dict(row))
        for row in group_rows:
            item = dict(row)
            item["folder_path"] = self.folder_index.folder_path(
                item.get("parent_id")
            )
            item["members"] = members[item["group_id"]]
            items.append(item)
        return SearchResult(total, items)

    def recent(self, count: int = 20) -> SearchResult:
        total = self.connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        rows = self.connection.execute(
            """
            SELECT file_id, name, parent_id, modified_time
            FROM files
            ORDER BY modified_time DESC, name COLLATE NOCASE, file_id
            LIMIT ?
            """,
            (count,),
        ).fetchall()
        return SearchResult(
            total,
            [self._file_record(row, self.folder_index) for row in rows],
        )

    def changed_in_scan(
        self, scan_id: str, limit: int = DEFAULT_LIMIT
    ) -> SearchResult:
        file_rows = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT file_id, name, parent_id, modified_time
                FROM files WHERE scan_id = ?
                """,
                (scan_id,),
            )
        ]
        folder_rows = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT folder_id, name, parent_id
                FROM folders WHERE scan_id = ?
                """,
                (scan_id,),
            )
        ]
        items = [
            {
                "item_type": "FILE",
                "item_id": row["file_id"],
                "name": row["name"],
                "path": self.folder_index.file_path(
                    row.get("parent_id"), row["name"]
                ),
                "modified_time": row.get("modified_time"),
            }
            for row in file_rows
        ]
        items.extend(
            {
                "item_type": "FOLDER",
                "item_id": row["folder_id"],
                "name": row["name"],
                "path": self.folder_index.folder_path(row["folder_id"]),
                "modified_time": None,
            }
            for row in folder_rows
        )
        items.sort(key=lambda item: (item["path"].casefold(), item["item_id"]))
        return SearchResult(len(items), items[:limit])

    def render_tree(
        self,
        root_folder_id: str | None = None,
        max_depth: int | None = None,
        include_files: bool = False,
    ) -> TreeResult:
        if root_folder_id is not None and root_folder_id not in self.folder_index.folders:
            raise ValueError(f"root folder를 찾을 수 없습니다: {root_folder_id}")

        files_by_parent: dict[str | None, list[dict]] = {}
        if include_files:
            for row in self.connection.execute(
                "SELECT file_id, name, parent_id FROM files"
            ):
                file = dict(row)
                files_by_parent.setdefault(file.get("parent_id"), []).append(file)
            for files in files_by_parent.values():
                files.sort(key=stable_name_key)

        lines = []
        folder_ids: set[str] = set()
        file_ids: set[str] = set()

        def child_entries(parent_id: str | None) -> list[tuple[str, dict]]:
            folder_entries = [
                ("FOLDER", item)
                for item in self.folder_index.children.get(parent_id, ())
            ]
            file_entries = (
                [("FILE", item) for item in files_by_parent.get(parent_id, ())]
                if include_files
                else []
            )
            return folder_entries + file_entries

        def append_children(parent_id: str | None, prefix: str, depth: int) -> None:
            stack = [(child_entries(parent_id), 0, prefix, depth)]
            while stack:
                entries, index, current_prefix, current_depth = stack.pop()
                if max_depth is not None and current_depth > max_depth:
                    continue
                if index >= len(entries):
                    continue

                item_type, item = entries[index]
                stack.append((entries, index + 1, current_prefix, current_depth))
                last = index == len(entries) - 1
                connector = "└─ " if last else "├─ "
                next_prefix = current_prefix + ("   " if last else "│  ")

                if item_type == "FILE":
                    if item["file_id"] in file_ids:
                        continue
                    file_ids.add(item["file_id"])
                    lines.append(
                        f"{current_prefix}{connector}[FILE] {item['name']}"
                    )
                    continue

                folder_id = item["folder_id"]
                if folder_id in folder_ids:
                    continue
                folder_ids.add(folder_id)
                lines.append(f"{current_prefix}{connector}{item['name']}")
                stack.append(
                    (
                        child_entries(folder_id),
                        0,
                        next_prefix,
                        current_depth + 1,
                    )
                )

        def reaches_root_or_missing_parent(folder_id: str) -> bool:
            seen = set()
            current_id: str | None = folder_id
            while current_id in self.folder_index.folders:
                if current_id in seen:
                    return False
                seen.add(current_id)
                current_id = self.folder_index.folders[current_id].get(
                    "parent_id"
                )
            return True

        if root_folder_id is not None:
            root = self.folder_index.folders[root_folder_id]
            lines.append(root["name"])
            folder_ids.add(root_folder_id)
            append_children(root_folder_id, "", 1)
        else:
            lines.append("Google Drive")
            append_children(None, "", 1)

            missing_parent_ids = sorted(
                parent_id
                for parent_id in self.folder_index.children
                if parent_id is not None
                and parent_id not in self.folder_index.folders
            )
            for parent_id in missing_parent_ids:
                lines.append(f"[MISSING PARENT: {parent_id}]")
                append_children(parent_id, "", 1)

            unresolved = sorted(
                (
                    folder
                    for folder_id, folder in self.folder_index.folders.items()
                    if folder_id not in folder_ids
                    and not reaches_root_or_missing_parent(folder_id)
                ),
                key=stable_name_key,
            )
            if unresolved:
                lines.append("[CYCLES / UNRESOLVED]")
                for folder in unresolved:
                    folder_id = folder["folder_id"]
                    if folder_id in folder_ids:
                        continue
                    folder_ids.add(folder_id)
                    lines.append(f"└─ {folder['name']} [folder_id={folder_id}]")
                    append_children(folder_id, "   ", 1)

            if include_files:
                missing_file_parents = sorted(
                    parent_id
                    for parent_id in files_by_parent
                    if parent_id is not None
                    and parent_id not in self.folder_index.folders
                    and parent_id not in self.folder_index.children
                )
                for parent_id in missing_file_parents:
                    lines.append(f"[FILES WITH MISSING PARENT: {parent_id}]")
                    append_children(parent_id, "", 1)

        return TreeResult("\n".join(lines), folder_ids, file_ids)
