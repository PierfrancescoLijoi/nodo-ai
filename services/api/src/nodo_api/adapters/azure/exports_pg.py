"""Export requests in PostgreSQL (migration 0005), scoped by workspace like everything else."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from nodo_api.adapters.azure.postgres import PostgresDatabase
from nodo_api.domain.exports import EXPORT_TTL_HOURS, Export, ExportState
from nodo_api.errors import NotFoundError


def _export(row: dict[str, Any]) -> Export:
    return Export(
        id=row["id"],
        workspace_id=row["workspace_id"],
        owner_id=row["owner_id"],
        meeting_id=row["meeting_id"],
        state=ExportState(row["state"]),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        blob_name=row["blob_name"],
        size_bytes=row["size_bytes"],
    )


class PgExportStore:
    def __init__(self, db: PostgresDatabase) -> None:
        self._db = db

    def create(
        self, workspace_id: str, owner_id: str, meeting_id: str | None, now: datetime
    ) -> Export:
        export_id = str(uuid4())
        expires = now + timedelta(hours=EXPORT_TTL_HOURS)
        with self._db.workspace(workspace_id) as conn:
            conn.execute(
                "INSERT INTO exports (workspace_id, id, owner_id, meeting_id, state, created_at,"
                " expires_at) VALUES (%s, %s, %s, %s, 'queued', %s, %s)",
                (workspace_id, export_id, owner_id, meeting_id, now, expires),
            )
        return Export(
            export_id, workspace_id, owner_id, meeting_id, ExportState.QUEUED, now, expires
        )

    def get(self, workspace_id: str, owner_id: str, export_id: str) -> Export:
        with self._db.workspace(workspace_id) as conn:
            row = conn.execute(
                "SELECT * FROM exports WHERE workspace_id = %s AND id = %s AND owner_id = %s",
                (workspace_id, export_id, owner_id),
            ).fetchone()
        if row is None:
            raise NotFoundError
        return _export(row)

    def get_for_processing(self, workspace_id: str, export_id: str) -> Export:
        with self._db.workspace(workspace_id) as conn:
            row = conn.execute(
                "SELECT * FROM exports WHERE workspace_id = %s AND id = %s",
                (workspace_id, export_id),
            ).fetchone()
        if row is None:
            raise NotFoundError
        return _export(row)

    def set_state(
        self,
        workspace_id: str,
        export_id: str,
        state: ExportState,
        blob_name: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        with self._db.workspace(workspace_id) as conn:
            conn.execute(
                "UPDATE exports SET state = %s, blob_name = COALESCE(%s, blob_name),"
                " size_bytes = COALESCE(%s, size_bytes) WHERE workspace_id = %s AND id = %s",
                (state.value, blob_name, size_bytes, workspace_id, export_id),
            )
