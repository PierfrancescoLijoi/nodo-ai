"""The speech job a meeting is waiting for, in PostgreSQL (migration 0006), scoped by workspace."""

from __future__ import annotations

from datetime import datetime

from nodo_api.adapters.azure.postgres import PostgresDatabase
from nodo_api.domain.models import SpeechJob


class PgSpeechJobStore:
    def __init__(self, db: PostgresDatabase) -> None:
        self._db = db

    def get(self, workspace_id: str, meeting_id: str) -> SpeechJob | None:
        with self._db.workspace(workspace_id) as conn:
            row = conn.execute(
                "SELECT handle, submitted_at, polls FROM speech_jobs"
                " WHERE workspace_id = %s AND meeting_id = %s",
                (workspace_id, meeting_id),
            ).fetchone()
        if row is None:
            return None
        return SpeechJob(row["handle"], row["submitted_at"], row["polls"])

    def start(self, workspace_id: str, meeting_id: str, handle: str, now: datetime) -> None:
        with self._db.workspace(workspace_id) as conn:
            conn.execute(
                "INSERT INTO speech_jobs (workspace_id, meeting_id, handle, submitted_at, polls)"
                " VALUES (%s, %s, %s, %s, 0)"
                " ON CONFLICT (workspace_id, meeting_id) DO UPDATE"
                " SET handle = EXCLUDED.handle, submitted_at = EXCLUDED.submitted_at, polls = 0",
                (workspace_id, meeting_id, handle, now),
            )

    def record_poll(self, workspace_id: str, meeting_id: str) -> int:
        with self._db.workspace(workspace_id) as conn:
            row = conn.execute(
                "UPDATE speech_jobs SET polls = polls + 1"
                " WHERE workspace_id = %s AND meeting_id = %s RETURNING polls",
                (workspace_id, meeting_id),
            ).fetchone()
        return int(row["polls"]) if row else 0

    def clear(self, workspace_id: str, meeting_id: str) -> None:
        with self._db.workspace(workspace_id) as conn:
            conn.execute(
                "DELETE FROM speech_jobs WHERE workspace_id = %s AND meeting_id = %s",
                (workspace_id, meeting_id),
            )
