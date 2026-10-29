"""The audit archive's view of PostgreSQL (migration 0009) and of the immutable storage account.

Runs as the audit identity, which holds `nodo_audit`: a policy lets it read audit events across
workspaces, and it owns the one-row cursor table. Nothing else."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from azure.core.exceptions import ResourceExistsError

from nodo_api.adapters.azure.postgres import PostgresDatabase
from nodo_api.services.audit_archive import ArchiveCursor, ArchivedEvent

AUDIT_CONTAINER = "audit"


class PgAuditSource:
    def __init__(self, db: PostgresDatabase) -> None:
        self._db = db

    def read_after(
        self, cursor: ArchiveCursor | None, before: datetime, limit: int
    ) -> Sequence[ArchivedEvent]:
        floor = cursor
        with self._db.admin() as conn:
            if floor is None:
                rows = conn.execute(
                    "SELECT workspace_id, id, event_type, metadata, occurred_at FROM audit_events"
                    " WHERE occurred_at < %s ORDER BY occurred_at, workspace_id, id LIMIT %s",
                    (before, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT workspace_id, id, event_type, metadata, occurred_at FROM audit_events"
                    " WHERE occurred_at < %s AND (occurred_at, workspace_id, id) > (%s, %s, %s)"
                    " ORDER BY occurred_at, workspace_id, id LIMIT %s",
                    (before, floor.occurred_at, floor.workspace_id, floor.event_id, limit),
                ).fetchall()
        return tuple(
            ArchivedEvent(
                r["workspace_id"], r["id"], r["event_type"], dict(r["metadata"]), r["occurred_at"]
            )
            for r in rows
        )

    def load(self) -> tuple[ArchiveCursor | None, str]:
        with self._db.admin() as conn:
            row = conn.execute(
                "SELECT occurred_at, workspace_id, event_id, last_sha256 FROM audit_archive_state"
                " WHERE id = 1"
            ).fetchone()
        cursor = (
            None
            if row["occurred_at"] is None
            else ArchiveCursor(row["occurred_at"], row["workspace_id"], row["event_id"])
        )
        return cursor, row["last_sha256"]

    def save(self, cursor: ArchiveCursor, sha256: str, count: int) -> None:
        with self._db.admin() as conn:
            conn.execute(
                "UPDATE audit_archive_state SET occurred_at = %s, workspace_id = %s, event_id = %s,"
                " last_sha256 = %s, archived = archived + %s WHERE id = 1",
                (cursor.occurred_at, cursor.workspace_id, cursor.event_id, sha256, count),
            )


class AzureAuditSink:
    """Create-only writes into the container whose retention policy makes them permanent."""

    def __init__(self, service_client: Any, container: str = AUDIT_CONTAINER) -> None:
        self._service = service_client
        self._container = container

    def put(self, name: str, data: bytes) -> bool:
        blob = self._service.get_blob_client(self._container, name)
        try:
            blob.upload_blob(data, overwrite=False, content_type="application/x-ndjson")
        except ResourceExistsError:
            return False
        return True
