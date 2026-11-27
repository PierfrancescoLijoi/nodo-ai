"""Sources, sections and fragments on SQLite (ADR-029).

Meetings are copied into the sources table by triggers (schema 0009), so the queries below see them
without any meeting code running. Their fragments are not copied: they stay in transcript_segments
and are read through the transcript repository."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from nodo_api.adapters.local.db import LocalDatabase, from_text, to_text
from nodo_api.adapters.local.repos import SqliteTranscriptRepository
from nodo_api.domain.models import Locale
from nodo_api.domain.sources import (
    ACTIVE_STATES,
    Clear,
    DuplicateContentError,
    Fragment,
    Locator,
    Section,
    SectionSummary,
    Source,
    SourceKind,
    SourceVersion,
    check_version,
    outline_order,
)
from nodo_api.errors import ConflictError, NotFoundError

# The retention sweep leaves a source alone while its import runs (the states are constants).
_NO_RUNNING_IMPORT = (
    " AND NOT EXISTS (SELECT 1 FROM import_jobs j WHERE j.workspace_id = sources.workspace_id"
    f" AND j.source_id = sources.id AND j.state IN ({', '.join(repr(s) for s in ACTIVE_STATES)}))"
)

_SOURCE_COLUMNS = (
    "workspace_id, owner_id, id, kind, title, state, origin, language, content_hash, version,"
    " created_at, tombstoned_at, retention_until"
)


def _source(row: sqlite3.Row) -> Source:
    return Source(
        id=row["id"],
        workspace_id=row["workspace_id"],
        owner_id=row["owner_id"],
        kind=SourceKind(row["kind"]),
        title=row["title"],
        state=row["state"],
        created_at=from_text(row["created_at"]),
        origin=row["origin"],
        language=Locale(row["language"]) if row["language"] else None,
        content_hash=row["content_hash"],
        version=row["version"],
        tombstoned_at=from_text(row["tombstoned_at"]),
        retention_until=from_text(row["retention_until"]),
    )


class SqliteSourceRepository:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    def add(self, workspace_id: str, source: Source) -> Source:
        if source.kind is SourceKind.MEETING:
            raise ValueError("Meetings are added through the meeting repository")
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    f"INSERT INTO sources ({_SOURCE_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                    " ?, ?, ?)",
                    (
                        workspace_id,
                        source.owner_id,
                        source.id,
                        source.kind.value,
                        source.title,
                        source.state,
                        source.origin,
                        source.language.value if source.language else None,
                        source.content_hash,
                        source.version,
                        to_text(source.created_at),
                        to_text(source.tombstoned_at),
                        to_text(source.retention_until),
                    ),
                )
        except sqlite3.IntegrityError as error:
            if "content_hash" in str(error):
                raise DuplicateContentError(
                    "This owner already has a source with this hash"
                ) from error
            raise ValueError("A source with this id already exists") from error
        return source

    def get_owned(
        self, workspace_id: str, user_id: str, source_id: str, *, include_tombstoned: bool = False
    ) -> Source:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                f"SELECT {_SOURCE_COLUMNS} FROM sources"
                " WHERE workspace_id = ? AND id = ? AND owner_id = ?",
                (workspace_id, source_id, user_id),
            ).fetchone()
        if row is None or (row["tombstoned_at"] and not include_tombstoned):
            raise NotFoundError
        return _source(row)

    def get_for_processing(self, workspace_id: str, source_id: str) -> Source:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                f"SELECT {_SOURCE_COLUMNS} FROM sources WHERE workspace_id = ? AND id = ?",
                (workspace_id, source_id),
            ).fetchone()
        if row is None or row["tombstoned_at"]:
            raise NotFoundError
        return _source(row)

    def save(self, workspace_id: str, source: Source, *, live_only: bool = False) -> Source:
        if source.kind is SourceKind.MEETING:
            raise ValueError("Meetings are saved through the meeting repository")
        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    "UPDATE sources SET title = ?, state = ?, origin = ?, language = ?,"
                    " content_hash = ?, tombstoned_at = ? WHERE workspace_id = ? AND id = ?"
                    " AND kind = ?" + (" AND tombstoned_at IS NULL" if live_only else ""),
                    (
                        source.title,
                        source.state,
                        source.origin,
                        source.language.value if source.language else None,
                        source.content_hash,
                        to_text(source.tombstoned_at),
                        workspace_id,
                        source.id,
                        source.kind.value,
                    ),
                )
                if cursor.rowcount == 0:
                    raise NotFoundError
                row = conn.execute(
                    f"SELECT {_SOURCE_COLUMNS} FROM sources WHERE workspace_id = ? AND id = ?",
                    (workspace_id, source.id),
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise DuplicateContentError("This owner already has a source with this hash") from error
        return _source(row)

    def update(
        self,
        workspace_id: str,
        source_id: str,
        *,
        state: str | None = None,
        content_hash: str | None = None,
        title: str | None = None,
        retention_until: datetime | Clear | None = None,
        expected_state: str | None = None,
    ) -> Source:
        changes = {
            "state": state,
            "content_hash": content_hash,
            "title": title,
            "retention_until": (
                None if isinstance(retention_until, Clear) else to_text(retention_until)
            ),
        }
        columns = [name for name, value in changes.items() if value is not None]
        if isinstance(retention_until, Clear):
            columns.append("retention_until")
        where = "workspace_id = ? AND id = ? AND kind != 'meeting' AND tombstoned_at IS NULL"
        try:
            with self._db.transaction() as conn:
                row = conn.execute(
                    f"SELECT {_SOURCE_COLUMNS} FROM sources WHERE {where}",
                    (workspace_id, source_id),
                ).fetchone()
                if row is None:
                    raise NotFoundError
                if expected_state is not None and row["state"] != expected_state:
                    raise ConflictError
                claim = conn.execute(
                    "SELECT retention_erase_phase FROM sources WHERE workspace_id = ? AND id = ?",
                    (workspace_id, source_id),
                ).fetchone()
                if claim["retention_erase_phase"] is not None:
                    raise ConflictError
                if columns:  # names come from the fixed dict above, never from the caller
                    conn.execute(
                        f"UPDATE sources SET {', '.join(f'{c} = ?' for c in columns)}"
                        f" WHERE {where}",
                        (*(changes[c] for c in columns), workspace_id, source_id),
                    )
                row = conn.execute(
                    f"SELECT {_SOURCE_COLUMNS} FROM sources WHERE workspace_id = ? AND id = ?",
                    (workspace_id, source_id),
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise DuplicateContentError("This owner already has a source with this hash") from error
        return _source(row)

    def find_by_hash(self, workspace_id: str, user_id: str, content_hash: str) -> Source | None:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                f"SELECT {_SOURCE_COLUMNS} FROM sources WHERE workspace_id = ? AND owner_id = ?"
                " AND content_hash = ? AND tombstoned_at IS NULL AND kind != 'meeting'",
                (workspace_id, user_id, content_hash),
            ).fetchone()
        return _source(row) if row else None

    def originals_due(self, now: datetime, limit: int) -> tuple[Source, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                f"SELECT {_SOURCE_COLUMNS} FROM sources WHERE retention_until IS NOT NULL"
                " AND content_hash IS NOT NULL AND tombstoned_at IS NULL AND kind = 'audio'"
                " AND state != 'cancelling'"
                " AND retention_until <= ?"
                + _NO_RUNNING_IMPORT
                + " ORDER BY retention_until LIMIT ?",
                (to_text(now), limit),
            ).fetchall()
        return tuple(_source(row) for row in rows)

    @staticmethod
    def _erase_params(source: Source) -> tuple[str, str, str, str, str]:
        assert source.retention_until is not None and source.content_hash is not None
        return (
            source.workspace_id,
            source.id,
            to_text(source.retention_until),
            source.content_hash,
            source.state,
        )

    def claim_original_erase(self, source: Source) -> str | None:
        workspace_id, source_id, due, content_hash, state = self._erase_params(source)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE sources SET retention_erase_phase = 'erase'"
                " WHERE workspace_id = ? AND id = ? AND retention_until = ?"
                " AND content_hash = ? AND state = ? AND tombstoned_at IS NULL"
                " AND state != 'cancelling'"
                " AND retention_erase_phase IS NULL" + _NO_RUNNING_IMPORT,
                (workspace_id, source_id, due, content_hash, state),
            )
            if cursor.rowcount == 1:
                return "erase"
            row = conn.execute(
                "SELECT retention_erase_phase FROM sources WHERE workspace_id = ? AND id = ?"
                " AND retention_until = ? AND content_hash = ? AND state = ?"
                " AND tombstoned_at IS NULL",
                (workspace_id, source_id, due, content_hash, state),
            ).fetchone()
        return row["retention_erase_phase"] if row is not None else None

    def prepare_original_erased(self, source: Source) -> bool:
        workspace_id, source_id, due, content_hash, state = self._erase_params(source)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE sources SET retention_erase_phase = 'audit'"
                " WHERE workspace_id = ? AND id = ? AND retention_until = ?"
                " AND content_hash = ? AND state = ? AND tombstoned_at IS NULL"
                " AND retention_erase_phase = 'erase'",
                (workspace_id, source_id, due, content_hash, state),
            )
        return cursor.rowcount == 1

    def mark_original_erased(self, source: Source) -> bool:
        workspace_id, source_id, due, content_hash, state = self._erase_params(source)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE sources SET retention_until = NULL, retention_erase_phase = NULL"
                " WHERE workspace_id = ? AND id = ? AND retention_until = ?"
                " AND content_hash = ? AND state = ? AND tombstoned_at IS NULL"
                " AND retention_erase_phase = 'audit'",
                (workspace_id, source_id, due, content_hash, state),
            )
        return cursor.rowcount == 1

    def unfinished_deletes(self, limit: int) -> tuple[Source, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                f"SELECT {_SOURCE_COLUMNS} FROM sources WHERE tombstoned_at IS NOT NULL"
                " AND kind != 'meeting' AND state = 'deleting' LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(_source(row) for row in rows)

    def list_owned(
        self,
        workspace_id: str,
        user_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
        kinds: frozenset[SourceKind] | None = None,
        state: str | None = None,
        title_query: str | None = None,
    ) -> tuple[Source, ...]:
        where = "workspace_id = ? AND owner_id = ? AND tombstoned_at IS NULL"
        params: list[object] = [workspace_id, user_id]
        if kinds is not None:
            ordered = sorted(kind.value for kind in kinds)
            where += f" AND kind IN ({', '.join('?' * len(ordered))})"
            params.extend(ordered)
        if state is not None:
            where += " AND state = ?"
            params.append(state)
        if title_query is not None:
            # ponytail: nodo_casefold runs on every row of the owner (a full scan per query); add
            # a casefolded title column with an index, or FTS, if libraries reach many thousands
            where += " AND instr(nodo_casefold(COALESCE(title, '')), nodo_casefold(?)) > 0"
            params.append(title_query)
        if cursor is not None:
            where += " AND (created_at, id) < (?, ?)"
            params.extend((to_text(cursor[0]), cursor[1]))
        params.append(limit)
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                f"SELECT {_SOURCE_COLUMNS} FROM sources WHERE {where}"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return tuple(_source(row) for row in rows)

    def titles(
        self, workspace_id: str, user_id: str, source_ids: Sequence[str]
    ) -> dict[str, str | None]:
        ids = list(dict.fromkeys(source_ids))
        if not ids:
            return {}
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT id, title FROM sources WHERE workspace_id = ? AND owner_id = ?"
                f" AND tombstoned_at IS NULL AND id IN ({', '.join('?' * len(ids))})",
                (workspace_id, user_id, *ids),
            ).fetchall()
        return {row["id"]: row["title"] for row in rows}


def _fragment(row: sqlite3.Row) -> Fragment:
    return Fragment(
        id=row["id"],
        source_id=row["source_id"],
        section_id=row["section_id"],
        ordinal=row["ordinal"],
        text=row["text"],
        locator=Locator.from_json(row["locator"]),
        version=row["version"],
        speaker_id=row["speaker_id"],
        confidence=row["confidence"],
        locale=Locale(row["locale"]) if row["locale"] else None,
    )


def _section(row: sqlite3.Row) -> Section:
    return Section(
        id=row["id"],
        source_id=row["source_id"],
        parent_id=row["parent_id"],
        ordinal=row["ordinal"],
        level=row["level"],
        title=row["title"],
    )


class SqliteFragmentRepository:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db
        self._transcripts = SqliteTranscriptRepository(db)

    def add_version(
        self,
        workspace_id: str,
        source_id: str,
        sections: tuple[Section, ...],
        fragments: tuple[Fragment, ...],
    ) -> int:
        check_version(source_id, sections, fragments)
        with self._db.transaction() as conn:
            kind = self._kind(conn, workspace_id, source_id)
            if kind is None:
                raise NotFoundError
            if kind is SourceKind.MEETING:
                raise ValueError(
                    "A meeting transcript is written through the transcript repository"
                )
            gone = conn.execute(
                "SELECT tombstoned_at FROM sources WHERE workspace_id = ? AND id = ?",
                (workspace_id, source_id),
            ).fetchone()["tombstoned_at"]
            if gone is not None:
                raise NotFoundError  # deletion wins: a deleted source takes no new content
            top = conn.execute(
                "SELECT max(version) AS version FROM fragments WHERE workspace_id = ?"
                " AND source_id = ?",
                (workspace_id, source_id),
            ).fetchone()["version"]
            version = (top or 0) + 1
            conn.execute(
                "DELETE FROM sections WHERE workspace_id = ? AND source_id = ?",
                (workspace_id, source_id),
            )
            conn.executemany(
                "INSERT INTO sections (workspace_id, source_id, id, parent_id, ordinal, level,"
                " title) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (workspace_id, source_id, s.id, s.parent_id, s.ordinal, s.level, s.title)
                    for s in sections
                ],
            )
            conn.executemany(
                "INSERT INTO fragments (workspace_id, source_id, version, id, section_id, ordinal,"
                " text, locator, speaker_id, confidence, locale)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        workspace_id,
                        source_id,
                        version,
                        f.id,
                        f.section_id,
                        f.ordinal,
                        f.text,
                        f.locator.to_json(),
                        f.speaker_id,
                        f.confidence,
                        f.locale.value if f.locale else None,
                    )
                    for f in fragments
                ],
            )
            conn.execute(
                "UPDATE sources SET version = ? WHERE workspace_id = ? AND id = ?",
                (version, workspace_id, source_id),
            )
        return version

    def latest(self, workspace_id: str, source_id: str) -> tuple[Fragment, ...]:
        with self._db.transaction(write=False) as conn:
            if self._kind(conn, workspace_id, source_id) is SourceKind.MEETING:
                return self._transcript(workspace_id, source_id)
            rows = conn.execute(
                "SELECT * FROM fragments WHERE workspace_id = ? AND source_id = ? AND version ="
                " (SELECT max(version) FROM fragments WHERE workspace_id = ? AND source_id = ?)"
                " ORDER BY ordinal, id",
                (workspace_id, source_id, workspace_id, source_id),
            ).fetchall()
        return tuple(_fragment(row) for row in rows)

    def get_fragment(self, workspace_id: str, source_id: str, fragment_id: str) -> Fragment | None:
        # One definition of "the fragment" for every kind: the one `latest` returns.
        return next((f for f in self.latest(workspace_id, source_id) if f.id == fragment_id), None)

    def outline(self, workspace_id: str, source_id: str) -> tuple[Section, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT * FROM sections WHERE workspace_id = ? AND source_id = ?",
                (workspace_id, source_id),
            ).fetchall()
        return outline_order([_section(row) for row in rows])

    def record_version(
        self,
        workspace_id: str,
        source_id: str,
        version: int,
        content_hash: str,
        parser: str,
        extraction_version: str | None,
        original_name: str | None,
        size_bytes: int,
    ) -> None:
        if version < 1 or size_bytes < 0:
            raise ValueError("A version starts at 1 and a file has no negative size")
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT kind, tombstoned_at FROM sources WHERE workspace_id = ? AND id = ?",
                (workspace_id, source_id),
            ).fetchone()
            if row is None or row["tombstoned_at"] is not None:
                raise NotFoundError  # deletion wins: a deleted source takes no new records
            if row["kind"] == SourceKind.MEETING:
                raise ValueError("A meeting has no file versions")
            conn.execute(
                "INSERT OR REPLACE INTO source_versions (workspace_id, source_id, version,"
                " content_hash, parser, extraction_version, original_name, size_bytes, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    source_id,
                    version,
                    content_hash,
                    parser,
                    extraction_version,
                    original_name,
                    size_bytes,
                    to_text(datetime.now(UTC)),
                ),
            )

    def versions(
        self, workspace_id: str, user_id: str, source_id: str
    ) -> tuple[SourceVersion, ...]:
        with self._db.transaction(write=False) as conn:
            owned = conn.execute(
                "SELECT 1 FROM sources WHERE workspace_id = ? AND id = ? AND owner_id = ?"
                " AND tombstoned_at IS NULL",
                (workspace_id, source_id, user_id),
            ).fetchone()
            if owned is None:
                raise NotFoundError
            rows = conn.execute(
                "SELECT * FROM source_versions WHERE workspace_id = ? AND source_id = ?"
                " ORDER BY version DESC",
                (workspace_id, source_id),
            ).fetchall()
        return tuple(
            SourceVersion(
                source_id=row["source_id"],
                version=row["version"],
                content_hash=row["content_hash"],
                parser=row["parser"],
                extraction_version=row["extraction_version"],
                original_name=row["original_name"],
                size_bytes=row["size_bytes"],
                created_at=from_text(row["created_at"]),
            )
            for row in rows
        )

    def erase(self, workspace_id: str, source_id: str) -> None:
        with self._db.transaction() as conn:
            if self._kind(conn, workspace_id, source_id) is SourceKind.MEETING:
                raise ValueError("Meeting transcripts are erased through the meeting service")
            for table in ("fragments", "sections", "source_versions"):
                conn.execute(
                    f"DELETE FROM {table} WHERE workspace_id = ? AND source_id = ?",
                    (workspace_id, source_id),
                )

    @staticmethod
    def _kind(conn: sqlite3.Connection, workspace_id: str, source_id: str) -> SourceKind | None:
        row = conn.execute(
            "SELECT kind FROM sources WHERE workspace_id = ? AND id = ?", (workspace_id, source_id)
        ).fetchone()
        return SourceKind(row["kind"]) if row else None

    def _transcript(self, workspace_id: str, source_id: str) -> tuple[Fragment, ...]:
        try:
            segments = self._transcripts.latest(workspace_id, source_id)
        except NotFoundError:
            return ()
        return tuple(Fragment.from_segment(s, i) for i, s in enumerate(segments))


class SqliteSectionSummaryRepository:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    def get(
        self, workspace_id: str, source_id: str, section_id: str, source_version: int
    ) -> SectionSummary | None:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                "SELECT ss.* FROM section_summaries ss JOIN sources s"
                " ON s.workspace_id = ss.workspace_id AND s.id = ss.source_id"
                " WHERE ss.workspace_id = ? AND ss.source_id = ? AND ss.section_id = ?"
                " AND ss.source_version = ? AND s.version = ss.source_version"
                " AND s.tombstoned_at IS NULL",
                (workspace_id, source_id, section_id, source_version),
            ).fetchone()
        return self._summary(row) if row else None

    def put(self, workspace_id: str, summary: SectionSummary) -> None:
        with self._db.transaction() as conn:
            current = conn.execute(
                "SELECT 1 FROM sources s JOIN sections sec ON sec.workspace_id = s.workspace_id"
                " AND sec.source_id = s.id WHERE s.workspace_id = ? AND s.id = ?"
                " AND s.version = ? AND s.tombstoned_at IS NULL AND sec.id = ?",
                (workspace_id, summary.source_id, summary.source_version, summary.section_id),
            ).fetchone()
            if current is None:
                raise NotFoundError
            conn.execute(
                "INSERT OR REPLACE INTO section_summaries (workspace_id, source_id, section_id,"
                " source_version, model_version, prompt_version, text, token_count)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    summary.source_id,
                    summary.section_id,
                    summary.source_version,
                    summary.model_version,
                    summary.prompt_version,
                    summary.text,
                    summary.token_count,
                ),
            )

    def erase_source(self, workspace_id: str, source_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM section_summaries WHERE workspace_id = ? AND source_id = ?",
                (workspace_id, source_id),
            )

    @staticmethod
    def _summary(row: sqlite3.Row) -> SectionSummary:
        return SectionSummary(
            row["source_id"],
            row["section_id"],
            row["source_version"],
            row["model_version"],
            row["prompt_version"],
            row["text"],
            row["token_count"],
        )
