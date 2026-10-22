"""Meetings, transcripts and the audit log on SQLite. Behaviour matches the PostgreSQL adapters."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from uuid import uuid4

from nodo_api.adapters.local.db import LocalDatabase, from_text, now_text, to_text
from nodo_api.domain.models import (
    Locale,
    Meeting,
    MeetingState,
    RecordingAttestation,
    TranscriptSegment,
)
from nodo_api.errors import NotFoundError

_MEETING_SELECT = """
    SELECT m.workspace_id, m.id, m.owner_id, m.state, m.project, m.created_at, m.tombstoned_at,
           m.title, m.summary, m.duration_ms, m.speaker_names,
           a.user_id, a.meeting_id, a.notice_version, a.locale, a.retention_policy_id,
           a.acknowledged_at, a.audit_event_id, a.signature
    FROM meetings m
    JOIN recording_attestations a
      ON a.workspace_id = m.workspace_id AND a.meeting_id = m.id
"""


def _meeting(row: sqlite3.Row) -> Meeting:
    return Meeting(
        id=row["id"],
        workspace_id=row["workspace_id"],
        owner_id=row["owner_id"],
        created_at=from_text(row["created_at"]),
        state=MeetingState(row["state"]),
        attestation=RecordingAttestation(
            user_id=row["user_id"],
            meeting_id=row["meeting_id"],
            notice_version=row["notice_version"],
            acknowledged_at=from_text(row["acknowledged_at"]),
            retention_policy_id=row["retention_policy_id"],
            locale=Locale(row["locale"]),
            audit_event_id=row["audit_event_id"],
            signature=row["signature"],
        ),
        project=row["project"],
        tombstoned_at=from_text(row["tombstoned_at"]),
        title=row["title"],
        summary=row["summary"],
        duration_ms=row["duration_ms"],
        speaker_names=tuple(sorted(json.loads(row["speaker_names"] or "{}").items())),
    )


class SqliteMeetingRepository:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    def add(
        self, workspace_id: str, meeting: Meeting, idempotency_key: str, request_hash: str
    ) -> Meeting:
        with self._db.transaction() as conn:  # the write lock makes check-then-insert atomic
            existing = self._by_key(conn, workspace_id, meeting.owner_id, idempotency_key)
            if existing is not None:
                return existing[0]
            conn.execute(
                "INSERT INTO meetings (workspace_id, id, owner_id, state, project, created_at,"
                " title, summary, duration_ms, speaker_names) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    meeting.id,
                    meeting.owner_id,
                    meeting.state.value,
                    meeting.project,
                    to_text(meeting.created_at),
                    meeting.title,
                    meeting.summary,
                    meeting.duration_ms,
                    json.dumps(dict(meeting.speaker_names)),
                ),
            )
            a = meeting.attestation
            conn.execute(
                "INSERT INTO recording_attestations (workspace_id, meeting_id, user_id,"
                " notice_version, locale, retention_policy_id, acknowledged_at, audit_event_id,"
                " signature) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    meeting.id,
                    a.user_id,
                    a.notice_version,
                    a.locale.value,
                    a.retention_policy_id,
                    to_text(a.acknowledged_at),
                    a.audit_event_id,
                    a.signature,
                ),
            )
            conn.execute(
                "INSERT INTO idempotency_keys (workspace_id, user_id, key, meeting_id, request_hash)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (workspace_id, user_id, key) DO UPDATE"
                " SET meeting_id = excluded.meeting_id, request_hash = excluded.request_hash",
                (workspace_id, meeting.owner_id, idempotency_key, meeting.id, request_hash),
            )
        return meeting

    def get_by_idempotency_key(
        self, workspace_id: str, user_id: str, key: str
    ) -> tuple[Meeting, str] | None:
        with self._db.transaction(write=False) as conn:
            return self._by_key(conn, workspace_id, user_id, key)

    @staticmethod
    def _by_key(
        conn: sqlite3.Connection, workspace_id: str, user_id: str, key: str
    ) -> tuple[Meeting, str] | None:
        row = conn.execute(
            _MEETING_SELECT
            + " JOIN idempotency_keys k ON k.workspace_id = m.workspace_id AND k.meeting_id = m.id"
            " WHERE k.workspace_id = ? AND k.user_id = ? AND k.key = ? AND m.tombstoned_at IS NULL",
            (workspace_id, user_id, key),
        ).fetchone()
        if row is None:
            return None
        stored = conn.execute(
            "SELECT request_hash FROM idempotency_keys WHERE workspace_id = ? AND user_id = ?"
            " AND key = ?",
            (workspace_id, user_id, key),
        ).fetchone()
        return _meeting(row), stored["request_hash"]

    def get_owned(
        self, workspace_id: str, user_id: str, meeting_id: str, *, include_tombstoned: bool = False
    ) -> Meeting:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                _MEETING_SELECT + " WHERE m.workspace_id = ? AND m.id = ? AND m.owner_id = ?",
                (workspace_id, meeting_id, user_id),
            ).fetchone()
        if row is None or (row["tombstoned_at"] and not include_tombstoned):
            raise NotFoundError
        return _meeting(row)

    def get_for_processing(self, workspace_id: str, meeting_id: str) -> Meeting:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                _MEETING_SELECT + " WHERE m.workspace_id = ? AND m.id = ?",
                (workspace_id, meeting_id),
            ).fetchone()
        if row is None or row["tombstoned_at"]:
            raise NotFoundError
        return _meeting(row)

    def owned_ids(self, workspace_id: str, user_id: str) -> frozenset[str]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT id FROM meetings WHERE workspace_id = ? AND owner_id = ?"
                " AND tombstoned_at IS NULL",
                (workspace_id, user_id),
            ).fetchall()
        return frozenset(row["id"] for row in rows)

    def save(self, workspace_id: str, meeting: Meeting) -> Meeting:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE meetings SET state = ?, project = ?, tombstoned_at = ?, title = ?,"
                " summary = ?, duration_ms = ?, speaker_names = ? WHERE workspace_id = ? AND id = ?",
                (
                    meeting.state.value,
                    meeting.project,
                    to_text(meeting.tombstoned_at),
                    meeting.title,
                    meeting.summary,
                    meeting.duration_ms,
                    json.dumps(dict(meeting.speaker_names)),
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
        after = "" if cursor is None else " AND (m.created_at, m.id) < (?, ?)"
        params: tuple = (
            workspace_id,
            user_id,
            *((to_text(cursor[0]), cursor[1]) if cursor else ()),
            limit,
        )
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                _MEETING_SELECT
                + " WHERE m.workspace_id = ? AND m.owner_id = ? AND m.tombstoned_at IS NULL"
                + after
                + " ORDER BY m.created_at DESC, m.id DESC LIMIT ?",
                params,
            ).fetchall()
        return tuple(_meeting(row) for row in rows)

    def all_for_workspace(self, workspace_id: str) -> tuple[Meeting, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                _MEETING_SELECT
                + " WHERE m.workspace_id = ? AND m.tombstoned_at IS NULL ORDER BY m.created_at",
                (workspace_id,),
            ).fetchall()
        return tuple(_meeting(row) for row in rows)


def _segment(row: sqlite3.Row) -> TranscriptSegment:
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


class SqliteTranscriptRepository:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    def add_version(
        self, workspace_id: str, meeting_id: str, segments: tuple[TranscriptSegment, ...]
    ) -> tuple[TranscriptSegment, ...]:
        if not segments or any(segment.meeting_id != meeting_id for segment in segments):
            raise ValueError("A transcript version must contain segments for one meeting")
        version = segments[0].version
        with self._db.transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM transcript_segments WHERE workspace_id = ? AND meeting_id = ?"
                " AND version = ? LIMIT 1",
                (workspace_id, meeting_id, version),
            ).fetchone()
            if exists:
                raise ValueError("Transcript versions are immutable")
            conn.executemany(
                "INSERT INTO transcript_segments (workspace_id, meeting_id, version, id, speaker_id,"
                " start_ms, end_ms, text, confidence, locale, project)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT * FROM transcript_segments WHERE workspace_id = ? AND meeting_id = ?"
                " AND version = ? ORDER BY start_ms, id",
                (workspace_id, meeting_id, version),
            ).fetchall()
        if not rows:
            raise NotFoundError
        return tuple(_segment(row) for row in rows)

    def latest(self, workspace_id: str, meeting_id: str) -> tuple[TranscriptSegment, ...]:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                "SELECT max(version) AS version FROM transcript_segments"
                " WHERE workspace_id = ? AND meeting_id = ?",
                (workspace_id, meeting_id),
            ).fetchone()
        if row is None or row["version"] is None:
            raise NotFoundError
        return self.get_version(workspace_id, meeting_id, row["version"])

    def get_segment(
        self, workspace_id: str, meeting_id: str, segment_id: str
    ) -> TranscriptSegment | None:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                "SELECT * FROM transcript_segments WHERE workspace_id = ? AND meeting_id = ?"
                " AND id = ? ORDER BY version DESC LIMIT 1",
                (workspace_id, meeting_id, segment_id),
            ).fetchone()
        return _segment(row) if row else None

    def versions(self, workspace_id: str, meeting_id: str) -> tuple[int, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT DISTINCT version FROM transcript_segments WHERE workspace_id = ?"
                " AND meeting_id = ? ORDER BY version",
                (workspace_id, meeting_id),
            ).fetchall()
        return tuple(row["version"] for row in rows)

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM transcript_segments WHERE workspace_id = ? AND meeting_id = ?",
                (workspace_id, meeting_id),
            )


class SqliteAuditLog:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    def append(self, workspace_id: str, event_type: str, metadata: dict[str, str]) -> str:
        event_id = str(uuid4())
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO audit_events (workspace_id, id, event_type, metadata, occurred_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (workspace_id, event_id, event_type, json.dumps(metadata), now_text()),
            )
        return event_id
