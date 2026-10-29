"""Audio retention on the local database (the same rule as the cloud, ADR-019)."""

from __future__ import annotations

from datetime import datetime

from nodo_api.adapters.local.db import LocalDatabase, from_text, to_text
from nodo_api.domain.retention import AudioDue, audio_expires_at


class SqliteRetentionStore:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    def audio_due(self, now: datetime, limit: int) -> tuple[AudioDue, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT m.workspace_id, m.id, m.owner_id, m.created_at, a.retention_policy_id"
                " FROM meetings m JOIN recording_attestations a"
                " ON a.workspace_id = m.workspace_id AND a.meeting_id = m.id"
                " WHERE m.audio_erased_at IS NULL AND m.tombstoned_at IS NULL ORDER BY m.created_at"
            ).fetchall()
        due = [
            AudioDue(
                r["workspace_id"],
                r["id"],
                r["owner_id"],
                from_text(r["created_at"]),
                r["retention_policy_id"],
            )
            for r in rows
        ]
        return tuple([d for d in due if audio_expires_at(d.created_at, d.policy_id) <= now][:limit])

    def mark_audio_erased(self, workspace_id: str, meeting_id: str, when: datetime) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE meetings SET audio_erased_at = ? WHERE workspace_id = ? AND id = ?"
                " AND audio_erased_at IS NULL",
                (to_text(when), workspace_id, meeting_id),
            )
