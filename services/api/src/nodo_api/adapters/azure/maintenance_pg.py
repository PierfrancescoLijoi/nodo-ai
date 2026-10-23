"""Index maintenance's view of PostgreSQL (migration 0007).

Runs as the maintenance identity, which holds the `nodo_maintenance` role: a policy lets that role
list meetings across workspaces (five columns, nothing about their content). Everything else the job
touches goes through the normal per-workspace connection.
"""

from __future__ import annotations

from nodo_api.adapters.azure.postgres import PostgresDatabase
from nodo_api.domain.maintenance import MaintenanceMeeting
from nodo_api.domain.models import MeetingState


class PgMaintenanceStore:
    def __init__(self, db: PostgresDatabase) -> None:
        self._db = db

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
