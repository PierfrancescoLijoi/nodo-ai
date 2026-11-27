"""Watched folders in the local database (Task 15). The folder path is stored here only."""

from __future__ import annotations

import sqlite3

from nodo_api.adapters.local.db import LocalDatabase, from_text, to_text
from nodo_api.domain.sources import WatchFile, WatchFolder
from nodo_api.errors import NotFoundError

_FILE = "SELECT * FROM watch_files WHERE folder_id = ? AND rel_path = ?"


def _folder(row) -> WatchFolder:
    return WatchFolder(
        row["id"],
        row["workspace_id"],
        row["owner_id"],
        row["path"],
        bool(row["enabled"]),
        from_text(row["created_at"]),
    )


def _file(row) -> WatchFile:
    return WatchFile(
        row["folder_id"],
        row["rel_path"],
        row["size"],
        row["mtime_ns"],
        row["sha256"],
        row["source_id"],
        row["seen_stable_count"],
        bool(row["linked_only"]),
    )


class SqliteWatchFolderRepository:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    def add(self, folder: WatchFolder) -> WatchFolder:
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO watch_folders (id, workspace_id, owner_id, path, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        folder.id,
                        folder.workspace_id,
                        folder.owner_id,
                        folder.path,
                        int(folder.enabled),
                        to_text(folder.created_at),
                    ),
                )
        except sqlite3.IntegrityError:
            raise ValueError("Watch folder already exists") from None
        return folder

    def list_owned(self, workspace_id: str, owner_id: str) -> tuple[WatchFolder, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT * FROM watch_folders WHERE workspace_id = ? AND owner_id = ? ORDER BY created_at, id",
                (workspace_id, owner_id),
            ).fetchall()
        return tuple(_folder(row) for row in rows)

    def list_enabled(self) -> tuple[WatchFolder, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT * FROM watch_folders WHERE enabled = 1 ORDER BY created_at, id"
            ).fetchall()
        return tuple(_folder(row) for row in rows)

    def delete_owned(self, workspace_id: str, owner_id: str, folder_id: str) -> None:
        with self._db.transaction() as conn:
            deleted = conn.execute(
                "DELETE FROM watch_folders WHERE workspace_id = ? AND owner_id = ? AND id = ?",
                (workspace_id, owner_id, folder_id),
            ).rowcount
            if deleted == 0:
                raise NotFoundError
            conn.execute("DELETE FROM watch_files WHERE folder_id = ?", (folder_id,))

    def observe_file(self, folder_id: str, rel_path: str, size: int, mtime_ns: int) -> WatchFile:
        with self._db.transaction() as conn:
            if (
                conn.execute("SELECT 1 FROM watch_folders WHERE id = ?", (folder_id,)).fetchone()
                is None
            ):
                raise NotFoundError  # deleted during the scan
            conn.execute(
                "INSERT INTO watch_files (folder_id, rel_path, size, mtime_ns, seen_stable_count) VALUES (?, ?, ?, ?, 1) "
                "ON CONFLICT (folder_id, rel_path) DO UPDATE SET "
                "seen_stable_count = CASE WHEN size = excluded.size AND mtime_ns = excluded.mtime_ns THEN seen_stable_count + 1 ELSE 1 END, "
                "size = excluded.size, mtime_ns = excluded.mtime_ns",
                (folder_id, rel_path, size, mtime_ns),
            )
            row = conn.execute(_FILE, (folder_id, rel_path)).fetchone()
        return _file(row)

    def get_file(self, folder_id: str, rel_path: str) -> WatchFile | None:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(_FILE, (folder_id, rel_path)).fetchone()
        return _file(row) if row else None

    def mark_imported(
        self,
        folder_id: str,
        rel_path: str,
        sha256: str,
        source_id: str | None,
        linked_only: bool = False,
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE watch_files SET sha256 = ?, source_id = ?, linked_only = ? WHERE folder_id = ? AND rel_path = ?",
                (sha256, source_id, int(linked_only), folder_id, rel_path),
            )

    def unlink_source(self, source_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE watch_files SET source_id = NULL, linked_only = 0 WHERE source_id = ?",
                (source_id,),
            )

    def prune(self, folder_id: str, keep: frozenset[str]) -> None:
        with self._db.transaction() as conn:
            known = [
                r["rel_path"]
                for r in conn.execute(
                    "SELECT rel_path FROM watch_files WHERE folder_id = ?", (folder_id,)
                )
            ]
            conn.executemany(
                "DELETE FROM watch_files WHERE folder_id = ? AND rel_path = ?",
                [(folder_id, rel) for rel in known if rel not in keep],
            )

    def reset_stable(self, folder_id: str, rel_path: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE watch_files SET seen_stable_count = 1 WHERE folder_id = ? AND rel_path = ?",
                (folder_id, rel_path),
            )
