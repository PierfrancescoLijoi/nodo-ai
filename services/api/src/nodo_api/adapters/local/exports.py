"""Export requests on the local database."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from uuid import uuid4

from nodo_api.adapters.local.db import LocalDatabase, from_text, to_text
from nodo_api.domain.exports import EXPORT_TTL_HOURS, Export, ExportState
from nodo_api.errors import NotFoundError


def _export(row: sqlite3.Row) -> Export:
    return Export(
        id=row["id"],
        workspace_id=row["workspace_id"],
        owner_id=row["owner_id"],
        meeting_id=row["meeting_id"],
        state=ExportState(row["state"]),
        created_at=from_text(row["created_at"]),
        expires_at=from_text(row["expires_at"]),
        blob_name=row["blob_name"],
        size_bytes=row["size_bytes"],
    )


class SqliteExportStore:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    def create(
        self, workspace_id: str, owner_id: str, meeting_id: str | None, now: datetime
    ) -> Export:
        export_id = str(uuid4())
        expires = now + timedelta(hours=EXPORT_TTL_HOURS)
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO exports (workspace_id, id, owner_id, meeting_id, state, created_at,"
                " expires_at) VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                (workspace_id, export_id, owner_id, meeting_id, to_text(now), to_text(expires)),
            )
        return Export(
            export_id, workspace_id, owner_id, meeting_id, ExportState.QUEUED, now, expires
        )

    def _one(self, sql: str, params: tuple) -> Export:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(sql, params).fetchone()
        if row is None:
            raise NotFoundError
        return _export(row)

    def get(self, workspace_id: str, owner_id: str, export_id: str) -> Export:
        return self._one(
            "SELECT * FROM exports WHERE workspace_id = ? AND id = ? AND owner_id = ?",
            (workspace_id, export_id, owner_id),
        )

    def get_for_processing(self, workspace_id: str, export_id: str) -> Export:
        return self._one(
            "SELECT * FROM exports WHERE workspace_id = ? AND id = ?", (workspace_id, export_id)
        )

    def set_state(
        self,
        workspace_id: str,
        export_id: str,
        state: ExportState,
        blob_name: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE exports SET state = ?, blob_name = COALESCE(?, blob_name),"
                " size_bytes = COALESCE(?, size_bytes) WHERE workspace_id = ? AND id = ?",
                (state.value, blob_name, size_bytes, workspace_id, export_id),
            )
