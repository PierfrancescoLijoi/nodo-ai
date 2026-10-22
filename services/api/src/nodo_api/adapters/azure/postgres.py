"""PostgreSQL adapters: the source of truth (ARCHITECTURE.md section 6).

Every call runs in one transaction that first sets app.workspace_id, so row level security filters
the rows even if a query forgets its own workspace predicate. Authentication to Azure Database for
PostgreSQL uses a fresh Entra access token as the password; no password is stored.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from nodo_api.domain.models import (
    Locale,
    Meeting,
    MeetingState,
    RecordingAttestation,
    TranscriptSegment,
)
from nodo_api.errors import NotFoundError

POSTGRES_TOKEN_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"
TOKEN_LIFETIME_SAFETY_SECONDS = 2400  # recycle connections well before the token expires


class PostgresDatabase:
    """Connection pool whose connections are scoped to one workspace per transaction."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    @classmethod
    def from_entra(
        cls,
        host: str,
        database: str,
        user: str,
        credential: Any,
        max_size: int = 10,
        pool_timeout: float = 5.0,
        statement_timeout_ms: int = 15_000,
    ) -> PostgresDatabase:
        class EntraConnection(psycopg.Connection):
            @classmethod
            def connect(cls, conninfo: str = "", **kwargs: Any):  # type: ignore[override]
                kwargs["password"] = credential.get_token(POSTGRES_TOKEN_SCOPE).token
                return super().connect(conninfo, **kwargs)

        # connect_timeout and statement_timeout turn a stuck database into a fast error; the pool
        # timeout does the same for a saturated pool (a request waits 5 s for a connection, not 30).
        conninfo = (
            f"host={host} dbname={database} user={user} sslmode=require connect_timeout=5"
            f" options='-c statement_timeout={statement_timeout_ms}'"
        )
        pool = ConnectionPool(
            conninfo,
            connection_class=EntraConnection,
            max_size=max_size,
            timeout=pool_timeout,
            max_lifetime=TOKEN_LIFETIME_SAFETY_SECONDS,
            max_idle=300,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        return cls(pool)

    @classmethod
    def from_dsn(cls, dsn: str, max_size: int = 5) -> PostgresDatabase:
        """Local development and tests only: a plain DSN with a password."""
        pool = ConnectionPool(dsn, max_size=max_size, kwargs={"row_factory": dict_row}, open=True)
        return cls(pool)

    @contextmanager
    def workspace(self, workspace_id: str) -> Iterator[psycopg.Connection]:
        with self._pool.connection() as conn:
            conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))
            yield conn

    @contextmanager
    def admin(self) -> Iterator[psycopg.Connection]:
        """Schema work (migrations, role grants) that is not tied to a workspace."""
        with self._pool.connection() as conn:
            yield conn

    def ping(self) -> None:
        """A readiness probe: can a connection be had and used within the pool timeout?"""
        with self._pool.connection() as conn:
            conn.execute("SELECT 1")

    def close(self) -> None:
        self._pool.close()


def _meeting(row: dict[str, Any], attestation: dict[str, Any]) -> Meeting:
    return Meeting(
        id=row["id"],
        workspace_id=row["workspace_id"],
        owner_id=row["owner_id"],
        created_at=row["created_at"],
        state=MeetingState(row["state"]),
        attestation=RecordingAttestation(
            user_id=attestation["user_id"],
            meeting_id=attestation["meeting_id"],
            notice_version=attestation["notice_version"],
            acknowledged_at=attestation["acknowledged_at"],
            retention_policy_id=attestation["retention_policy_id"],
            locale=Locale(attestation["locale"]),
            audit_event_id=attestation["audit_event_id"],
            signature=attestation["signature"],
        ),
        project=row["project"],
        tombstoned_at=row["tombstoned_at"],
        title=row["title"],
        summary=row["summary"],
        duration_ms=row["duration_ms"],
        speaker_names=tuple(sorted((row["speaker_names"] or {}).items())),
    )


_MEETING_SELECT = """
    SELECT m.workspace_id, m.id, m.owner_id, m.state, m.project, m.created_at, m.tombstoned_at,
           m.title, m.summary, m.duration_ms, m.speaker_names,
           a.user_id, a.meeting_id, a.notice_version, a.locale, a.retention_policy_id,
           a.acknowledged_at, a.audit_event_id, a.signature
    FROM meetings m
    JOIN recording_attestations a
      ON a.workspace_id = m.workspace_id AND a.meeting_id = m.id
"""


class PgMeetingRepository:
    def __init__(self, db: PostgresDatabase) -> None:
        self._db = db

    def add(
        self, workspace_id: str, meeting: Meeting, idempotency_key: str, request_hash: str
    ) -> Meeting:
        with self._db.workspace(workspace_id) as conn:
            # Serialise concurrent creates that share a key so exactly one meeting results.
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{workspace_id}|{meeting.owner_id}|{idempotency_key}",),
            )
            existing = self._by_key(conn, workspace_id, meeting.owner_id, idempotency_key)
            if existing is not None:
                return existing[0]
            conn.execute(
                "INSERT INTO meetings (workspace_id, id, owner_id, state, project, created_at,"
                " title, summary, duration_ms) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    workspace_id,
                    meeting.id,
                    meeting.owner_id,
                    meeting.state.value,
                    meeting.project,
                    meeting.created_at,
                    meeting.title,
                    meeting.summary,
                    meeting.duration_ms,
                ),
            )
            a = meeting.attestation
            conn.execute(
                "INSERT INTO recording_attestations (workspace_id, meeting_id, user_id,"
                " notice_version, locale, retention_policy_id, acknowledged_at, audit_event_id,"
                " signature) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    workspace_id,
                    meeting.id,
                    a.user_id,
                    a.notice_version,
                    a.locale.value,
                    a.retention_policy_id,
                    a.acknowledged_at,
                    a.audit_event_id,
                    a.signature,
                ),
            )
            conn.execute(
                "INSERT INTO idempotency_keys"
                " (workspace_id, user_id, key, meeting_id, request_hash)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (workspace_id, user_id, key) DO UPDATE"
                " SET meeting_id = EXCLUDED.meeting_id, request_hash = EXCLUDED.request_hash",
                (workspace_id, meeting.owner_id, idempotency_key, meeting.id, request_hash),
            )
        return meeting

    def get_by_idempotency_key(
        self, workspace_id: str, user_id: str, key: str
    ) -> tuple[Meeting, str] | None:
        with self._db.workspace(workspace_id) as conn:
            return self._by_key(conn, workspace_id, user_id, key)

    @staticmethod
    def _by_key(
        conn: psycopg.Connection, workspace_id: str, user_id: str, key: str
    ) -> tuple[Meeting, str] | None:
        row = conn.execute(
            _MEETING_SELECT
            + " JOIN idempotency_keys k"
            + " ON k.workspace_id = m.workspace_id AND k.meeting_id = m.id"
            + " WHERE k.workspace_id = %s AND k.user_id = %s AND k.key = %s"
            + " AND m.tombstoned_at IS NULL",
            (workspace_id, user_id, key),
        ).fetchone()
        if row is None:
            return None
        request_hash = conn.execute(
            "SELECT request_hash FROM idempotency_keys"
            " WHERE workspace_id = %s AND user_id = %s AND key = %s",
            (workspace_id, user_id, key),
        ).fetchone()["request_hash"]
        return _meeting(row, row), request_hash

    def get_owned(
        self, workspace_id: str, user_id: str, meeting_id: str, *, include_tombstoned: bool = False
    ) -> Meeting:
        with self._db.workspace(workspace_id) as conn:
            row = conn.execute(
                _MEETING_SELECT + " WHERE m.workspace_id = %s AND m.id = %s AND m.owner_id = %s",
                (workspace_id, meeting_id, user_id),
            ).fetchone()
        if row is None or (row["tombstoned_at"] and not include_tombstoned):
            raise NotFoundError
        return _meeting(row, row)

    def get_for_processing(self, workspace_id: str, meeting_id: str) -> Meeting:
        with self._db.workspace(workspace_id) as conn:
            row = conn.execute(
                _MEETING_SELECT + " WHERE m.workspace_id = %s AND m.id = %s",
                (workspace_id, meeting_id),
            ).fetchone()
        if row is None or row["tombstoned_at"]:
            raise NotFoundError
        return _meeting(row, row)

    def owned_ids(self, workspace_id: str, user_id: str) -> frozenset[str]:
        with self._db.workspace(workspace_id) as conn:
            rows = conn.execute(
                "SELECT id FROM meetings WHERE workspace_id = %s AND owner_id = %s"
                " AND tombstoned_at IS NULL",
                (workspace_id, user_id),
            ).fetchall()
        return frozenset(row["id"] for row in rows)

    def save(self, workspace_id: str, meeting: Meeting) -> Meeting:
        with self._db.workspace(workspace_id) as conn:
            cursor = conn.execute(
                "UPDATE meetings SET state = %s, project = %s, tombstoned_at = %s, title = %s,"
                " summary = %s, duration_ms = %s, speaker_names = %s"
                " WHERE workspace_id = %s AND id = %s",
                (
                    meeting.state.value,
                    meeting.project,
                    meeting.tombstoned_at,
                    meeting.title,
                    meeting.summary,
                    meeting.duration_ms,
                    Jsonb(dict(meeting.speaker_names)),
                    workspace_id,
                    meeting.id,
                ),
            )
            if cursor.rowcount == 0:
                raise NotFoundError
        return meeting

    def list_owned(
        self, workspace_id: str, user_id: str, limit: int, cursor: tuple[datetime, str] | None
    ) -> tuple[Meeting, ...]:
        """Keyset pagination on (created_at, id) so deep pages cost the same as the first."""
        after = "" if cursor is None else " AND (m.created_at, m.id) < (%s, %s)"
        params: tuple = (workspace_id, user_id, *(cursor or ()), limit)
        with self._db.workspace(workspace_id) as conn:
            rows = conn.execute(
                _MEETING_SELECT
                + " WHERE m.workspace_id = %s AND m.owner_id = %s AND m.tombstoned_at IS NULL"
                + after
                + " ORDER BY m.created_at DESC, m.id DESC LIMIT %s",
                params,
            ).fetchall()
        return tuple(_meeting(row, row) for row in rows)

    def all_for_workspace(self, workspace_id: str) -> tuple[Meeting, ...]:
        with self._db.workspace(workspace_id) as conn:
            rows = conn.execute(
                _MEETING_SELECT + " WHERE m.workspace_id = %s AND m.tombstoned_at IS NULL"
                " ORDER BY m.created_at",
                (workspace_id,),
            ).fetchall()
        return tuple(_meeting(row, row) for row in rows)


def _segment(row: dict[str, Any]) -> TranscriptSegment:
    return TranscriptSegment(
        id=row["id"],
        meeting_id=row["meeting_id"],
        speaker_id=row["speaker_id"],
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        text=row["text"],
        confidence=row["confidence"],
        version=row["version"],
        locale=Locale(row["locale"]),
        project=row["project"],
    )


class PgTranscriptRepository:
    def __init__(self, db: PostgresDatabase) -> None:
        self._db = db

    def add_version(
        self, workspace_id: str, meeting_id: str, segments: tuple[TranscriptSegment, ...]
    ) -> tuple[TranscriptSegment, ...]:
        if not segments or any(segment.meeting_id != meeting_id for segment in segments):
            raise ValueError("A transcript version must contain segments for one meeting")
        version = segments[0].version
        with self._db.workspace(workspace_id) as conn:
            exists = conn.execute(
                "SELECT 1 FROM transcript_segments"
                " WHERE workspace_id = %s AND meeting_id = %s AND version = %s LIMIT 1",
                (workspace_id, meeting_id, version),
            ).fetchone()
            if exists:
                raise ValueError("Transcript versions are immutable")
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO transcript_segments (workspace_id, meeting_id, version, id,"
                    " speaker_id, start_ms, end_ms, text, confidence, locale, project)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            workspace_id,
                            meeting_id,
                            s.version,
                            s.id,
                            s.speaker_id,
                            s.start_ms,
                            s.end_ms,
                            s.text,
                            s.confidence,
                            s.locale.value,
                            s.project,
                        )
                        for s in segments
                    ],
                )
        return segments

    def get_version(
        self, workspace_id: str, meeting_id: str, version: int
    ) -> tuple[TranscriptSegment, ...]:
        with self._db.workspace(workspace_id) as conn:
            rows = conn.execute(
                "SELECT * FROM transcript_segments"
                " WHERE workspace_id = %s AND meeting_id = %s AND version = %s"
                " ORDER BY start_ms, id",
                (workspace_id, meeting_id, version),
            ).fetchall()
        if not rows:
            raise NotFoundError
        return tuple(_segment(row) for row in rows)

    def latest(self, workspace_id: str, meeting_id: str) -> tuple[TranscriptSegment, ...]:
        with self._db.workspace(workspace_id) as conn:
            row = conn.execute(
                "SELECT max(version) AS version FROM transcript_segments"
                " WHERE workspace_id = %s AND meeting_id = %s",
                (workspace_id, meeting_id),
            ).fetchone()
        if row is None or row["version"] is None:
            raise NotFoundError
        return self.get_version(workspace_id, meeting_id, row["version"])

    def get_segment(
        self, workspace_id: str, meeting_id: str, segment_id: str
    ) -> TranscriptSegment | None:
        with self._db.workspace(workspace_id) as conn:
            row = conn.execute(
                "SELECT * FROM transcript_segments"
                " WHERE workspace_id = %s AND meeting_id = %s AND id = %s"
                " ORDER BY version DESC LIMIT 1",
                (workspace_id, meeting_id, segment_id),
            ).fetchone()
        return _segment(row) if row else None

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        with self._db.workspace(workspace_id) as conn:
            conn.execute(
                "DELETE FROM transcript_segments WHERE workspace_id = %s AND meeting_id = %s",
                (workspace_id, meeting_id),
            )


class PgAuditLog:
    def __init__(self, db: PostgresDatabase) -> None:
        self._db = db

    def append(self, workspace_id: str, event_type: str, metadata: dict[str, str]) -> str:
        event_id = str(uuid4())
        with self._db.workspace(workspace_id) as conn:
            conn.execute(
                "INSERT INTO audit_events (workspace_id, id, event_type, metadata)"
                " VALUES (%s, %s, %s, %s)",
                (workspace_id, event_id, event_type, json.dumps(metadata)),
            )
        return event_id


class PgJobOutbox:
    """Records each job exactly once and remembers whether it has been published."""

    def __init__(self, db: PostgresDatabase) -> None:
        self._db = db

    def record(
        self, workspace_id: str, job_key: str, job_type: str, payload: dict[str, Any]
    ) -> tuple[str, bool]:
        """Return (job_id, already_published). Creates the row on first sight."""
        with self._db.workspace(workspace_id) as conn:
            conn.execute(
                "INSERT INTO jobs (workspace_id, job_key, job_type, id, payload)"
                " VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (workspace_id, job_key, job_type, str(uuid4()), json.dumps(payload)),
            )
            row = conn.execute(
                "SELECT id, published_at FROM jobs"
                " WHERE workspace_id = %s AND job_key = %s AND job_type = %s",
                (workspace_id, job_key, job_type),
            ).fetchone()
        published: datetime | None = row["published_at"]
        return row["id"], published is not None

    def mark_published(self, workspace_id: str, job_key: str, job_type: str) -> None:
        with self._db.workspace(workspace_id) as conn:
            conn.execute(
                "UPDATE jobs SET published_at = now()"
                " WHERE workspace_id = %s AND job_key = %s AND job_type = %s",
                (workspace_id, job_key, job_type),
            )
