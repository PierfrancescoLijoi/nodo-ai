"""Index maintenance's view of PostgreSQL (migration 0007).

Runs as the maintenance identity, which holds the `nodo_maintenance` role: a policy lets that role
list meetings across workspaces (five columns, nothing about their content). Everything else the job
touches goes through the normal per-workspace connection.
"""

from __future__ import annotations

from datetime import datetime

from nodo_api.adapters.azure.postgres import PostgresDatabase
from nodo_api.domain.maintenance import MaintenanceMeeting, PendingJob
from nodo_api.domain.models import MeetingState


class PgMaintenanceStore:
    def __init__(self, db: PostgresDatabase) -> None:
        self._db = db

    def unpublished_jobs(self, older_than: datetime, limit: int) -> tuple[PendingJob, ...]:
        with self._db.admin() as conn:
            rows = conn.execute(
                "SELECT workspace_id, job_key, job_type, payload FROM jobs"
                " WHERE published_at IS NULL AND created_at < %s ORDER BY created_at LIMIT %s",
                (older_than, limit),
            ).fetchall()
        return tuple(
            PendingJob(r["workspace_id"], r["job_key"], r["job_type"], dict(r["payload"]))
            for r in rows
        )

    def stuck_meetings(self, older_than: datetime, limit: int) -> tuple[MaintenanceMeeting, ...]:
        with self._db.admin() as conn:
            rows = conn.execute(
                "SELECT m.workspace_id, m.id, m.owner_id, m.state FROM meetings m"
                " JOIN jobs j ON j.workspace_id = m.workspace_id AND j.job_key = m.id"
                " AND j.job_type = 'transcribe'"
                " WHERE m.state IN ('transcribing', 'extracting') AND m.tombstoned_at IS NULL"
                " AND j.created_at < %s ORDER BY j.created_at LIMIT %s",
                (older_than, limit),
            ).fetchall()
        return tuple(
            MaintenanceMeeting(
                r["workspace_id"], r["id"], r["owner_id"], MeetingState(r["state"]), False
            )
            for r in rows
        )

    def list_meetings(
        self, after: tuple[str, str] | None, limit: int
    ) -> tuple[MaintenanceMeeting, ...]:
        floor = after or ("", "")
        with self._db.admin() as conn:
            rows = conn.execute(
                "SELECT workspace_id, id, owner_id, state, tombstoned_at FROM meetings"
                " WHERE (workspace_id, id) > (%s, %s) ORDER BY workspace_id, id LIMIT %s",
                (floor[0], floor[1], limit),
            ).fetchall()
        return tuple(
            MaintenanceMeeting(
                r["workspace_id"],
                r["id"],
                r["owner_id"],
                MeetingState(r["state"]),
                r["tombstoned_at"] is not None,
            )
            for r in rows
        )
