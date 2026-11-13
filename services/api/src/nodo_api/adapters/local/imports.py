"""Import jobs on SQLite (schema 0010): the progress of each document import, one row per source."""

from __future__ import annotations

import sqlite3
from collections.abc import Collection
from datetime import UTC, datetime

from nodo_api.adapters.local.db import LocalDatabase, from_text, to_text
from nodo_api.domain.sources import CLEAR, Clear, ImportJob, check_job_update
from nodo_api.errors import ConflictError, NotFoundError

_COLUMNS = (
    "workspace_id, owner_id, source_id, state, step, done, total, attempt, error_code,"
    " cancel_requested, created_at, updated_at"
)


def _job(row: sqlite3.Row) -> ImportJob:
    return ImportJob(
        workspace_id=row["workspace_id"],
        owner_id=row["owner_id"],
        source_id=row["source_id"],
        state=row["state"],
        step=row["step"],
        done=row["done"],
        total=row["total"],
        attempt=row["attempt"],
        error_code=row["error_code"],
        cancel_requested=bool(row["cancel_requested"]),
        created_at=from_text(row["created_at"]),
        updated_at=from_text(row["updated_at"]),
    )


class SqliteImportJobRepository:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    def create(self, workspace_id: str, owner_id: str, source_id: str) -> ImportJob:
        now = to_text(datetime.now(UTC))
        with self._db.transaction() as conn:
            # Only the owner's live, non-meeting source gets a job; a second call changes nothing.
            conn.execute(
                f"INSERT OR IGNORE INTO import_jobs ({_COLUMNS})"
                " SELECT workspace_id, owner_id, id, 'queued', NULL, 0, 0, 0, NULL, 0, ?, ?"
                " FROM sources WHERE workspace_id = ? AND id = ? AND owner_id = ?"
                " AND kind != 'meeting' AND tombstoned_at IS NULL",
                (now, now, workspace_id, source_id, owner_id),
            )
        return self.get_owned(workspace_id, owner_id, source_id)

    def get_owned(self, workspace_id: str, user_id: str, source_id: str) -> ImportJob:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM import_jobs"
                " WHERE workspace_id = ? AND source_id = ? AND owner_id = ?",
                (workspace_id, source_id, user_id),
            ).fetchone()
        if row is None:
            raise NotFoundError
        return _job(row)

    def get_for_processing(self, workspace_id: str, source_id: str) -> ImportJob:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM import_jobs WHERE workspace_id = ? AND source_id = ?",
                (workspace_id, source_id),
            ).fetchone()
        if row is None:
            raise NotFoundError
        return _job(row)

    def update(
        self,
        workspace_id: str,
        source_id: str,
        *,
        state: str | None = None,
        step: str | Clear | None = None,
        done: int | None = None,
        total: int | None = None,
        attempt: int | None = None,
        error_code: str | Clear | None = None,
        cancel_requested: bool | None = None,
        expected_state: str | Collection[str] | None = None,
    ) -> ImportJob:
        wanted = check_job_update(state, step, (done, total, attempt), error_code, expected_state)
        sets = ["updated_at = ?"]
        params: list[object] = [to_text(datetime.now(UTC))]
        # None leaves a column as it is; CLEAR sets step or error_code to NULL.
        for column, value in (
            ("state", state),
            ("step", step),
            ("done", done),
            ("total", total),
            ("attempt", attempt),
            ("error_code", error_code),
            ("cancel_requested", None if cancel_requested is None else int(cancel_requested)),
        ):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(None if value is CLEAR else value)
        params.extend((workspace_id, source_id))
        where = "workspace_id = ? AND source_id = ?"
        if wanted is not None:
            where += f" AND state IN ({', '.join('?' * len(wanted))})"
            params.extend(wanted)
        with self._db.transaction() as conn:
            # One statement: the state is tested and the row written together.
            cursor = conn.execute(f"UPDATE import_jobs SET {', '.join(sets)} WHERE {where}", params)
            if cursor.rowcount == 0:
                exists = conn.execute(
                    "SELECT 1 FROM import_jobs WHERE workspace_id = ? AND source_id = ?",
                    (workspace_id, source_id),
                ).fetchone()
                raise ConflictError if exists else NotFoundError
        return self.get_for_processing(workspace_id, source_id)

    def request_cancel(self, workspace_id: str, user_id: str, source_id: str) -> ImportJob:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE import_jobs SET cancel_requested = 1, updated_at = ?"
                " WHERE workspace_id = ? AND source_id = ? AND owner_id = ?",
                (to_text(datetime.now(UTC)), workspace_id, source_id, user_id),
            )
            if cursor.rowcount == 0:
                raise NotFoundError
        return self.get_owned(workspace_id, user_id, source_id)

    def is_cancel_requested(self, workspace_id: str, source_id: str) -> bool:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM import_jobs WHERE workspace_id = ? AND source_id = ?",
                (workspace_id, source_id),
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def delete(self, workspace_id: str, source_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM import_jobs WHERE workspace_id = ? AND source_id = ?",
                (workspace_id, source_id),
            )
