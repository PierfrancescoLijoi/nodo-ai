"""The retention job's view of PostgreSQL (migration 0004).

Runs as the retention identity, which holds the `nodo_retention` role: row level security lets that
role read meetings and attestations across workspaces and set `audio_erased_at`, and nothing else.
The period is worked out in SQL from the policy id so one query finds everything that is due.
"""

from __future__ import annotations

from datetime import datetime

from nodo_api.adapters.azure.postgres import PostgresDatabase
from nodo_api.domain.retention import LONGEST_DAYS, AudioDue

_DUE = f"""
SELECT m.workspace_id, m.id, m.owner_id, m.created_at, a.retention_policy_id
FROM meetings m
JOIN recording_attestations a ON a.workspace_id = m.workspace_id AND a.meeting_id = m.id
WHERE m.audio_erased_at IS NULL AND m.tombstoned_at IS NULL
  AND m.created_at + make_interval(
        days => COALESCE(substring(a.retention_policy_id from '([0-9]{{1,4}})d$')::int, {LONGEST_DAYS})
      ) <= %s
ORDER BY m.created_at
LIMIT %s
"""


class PgRetentionStore:
    def __init__(self, db: PostgresDatabase) -> None:
        self._db = db

    def audio_due(self, now: datetime, limit: int) -> tuple[AudioDue, ...]:
        with self._db.admin() as conn:
            rows = conn.execute(_DUE, (now, limit)).fetchall()
        return tuple(
            AudioDue(
                r["workspace_id"], r["id"], r["owner_id"], r["created_at"], r["retention_policy_id"]
            )
            for r in rows
        )

    def mark_audio_erased(self, workspace_id: str, meeting_id: str, when: datetime) -> None:
        with self._db.admin() as conn:
            conn.execute(
                "UPDATE meetings SET audio_erased_at = %s WHERE workspace_id = %s AND id = %s"
                " AND audio_erased_at IS NULL",
                (when, workspace_id, meeting_id),
            )
