"""Sources and fragments in memory, for tests and the demo backend (ADR-029).

Meetings are not stored here: the source repository reads them from the meeting repository and
merges them in, the way the SQLite triggers copy meetings into the sources table."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from nodo_api.adapters.memory.adapters import MemoryMeetingRepository, MemoryTranscriptRepository
from nodo_api.domain.sources import (
    Clear,
    DuplicateContentError,
    Fragment,
    Section,
    Source,
    SourceKind,
    SourceVersion,
    check_version,
    outline_order,
)
from nodo_api.errors import ConflictError, NotFoundError


class MemorySourceRepository:
    def __init__(self, meetings: MemoryMeetingRepository) -> None:
        self._meetings = meetings
        self._sources: dict[tuple[str, str], Source] = {}
        self._erase_claims: dict[tuple[str, str], tuple[datetime, str, str, str]] = {}
        # Whether an import of (workspace, source) is running; the import job twin sets it (the
        # SQLite store joins import_jobs). The retention sweep leaves such a source alone.
        self.import_running: Callable[[str, str], bool] = lambda workspace_id, source_id: False
        # The check for a clash and the write must not be separated by another thread.
        self._lock = threading.RLock()

    def add(self, workspace_id: str, source: Source) -> Source:
        if source.kind is SourceKind.MEETING:
            raise ValueError("Meetings are added through the meeting repository")
        with self._lock:
            if self.kind_of(workspace_id, source.id) is not None:
                raise ValueError("A source with this id already exists")
            self._check_hash_free(workspace_id, source)
            self._sources[(workspace_id, source.id)] = source
        return source

    def get_owned(
        self, workspace_id: str, user_id: str, source_id: str, *, include_tombstoned: bool = False
    ) -> Source:
        source = self._sources.get((workspace_id, source_id))
        if source is None:
            meeting = self._meetings.get_owned(
                workspace_id, user_id, source_id, include_tombstoned=include_tombstoned
            )
            return Source.from_meeting(meeting)
        if source.owner_id != user_id or (source.tombstoned_at and not include_tombstoned):
            raise NotFoundError
        return source

    def get_for_processing(self, workspace_id: str, source_id: str) -> Source:
        source = self._sources.get((workspace_id, source_id))
        if source is None:
            return Source.from_meeting(self._meetings.get_for_processing(workspace_id, source_id))
        if source.tombstoned_at is not None:
            raise NotFoundError
        return source

    def save(self, workspace_id: str, source: Source, *, live_only: bool = False) -> Source:
        if source.kind is SourceKind.MEETING:
            raise ValueError("Meetings are saved through the meeting repository")
        with self._lock:
            stored = self._sources.get((workspace_id, source.id))
            if stored is None or stored.kind is not source.kind:
                raise NotFoundError
            if live_only and stored.tombstoned_at is not None:
                raise NotFoundError
            # The owner never moves, the version belongs to the fragment repository and the
            # retention date is written by update and the sweep only (as on SQLite).
            saved = replace(
                source,
                owner_id=stored.owner_id,
                version=stored.version,
                retention_until=stored.retention_until,
            )
            self._check_hash_free(workspace_id, saved)
            self._sources[(workspace_id, source.id)] = saved
        return saved

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
        with self._lock:
            stored = self._sources.get((workspace_id, source_id))
            if stored is None or stored.tombstoned_at is not None:
                raise NotFoundError
            if expected_state is not None and stored.state != expected_state:
                raise ConflictError
            if (workspace_id, source_id) in self._erase_claims:
                raise ConflictError
            changes = {
                "state": state,
                "content_hash": content_hash,
                "title": title,
                "retention_until": None if isinstance(retention_until, Clear) else retention_until,
            }
            provided = {k: v for k, v in changes.items() if v is not None}
            if isinstance(retention_until, Clear):
                provided["retention_until"] = None
            saved = replace(stored, **provided)
            self._check_hash_free(workspace_id, saved)
            self._sources[(workspace_id, source_id)] = saved
        return saved

    def find_by_hash(self, workspace_id: str, user_id: str, content_hash: str) -> Source | None:
        return next(
            (
                s
                for (stored_workspace, _), s in self._sources.items()
                if stored_workspace == workspace_id
                and s.owner_id == user_id
                and s.content_hash == content_hash
                and s.tombstoned_at is None
            ),
            None,
        )

    def originals_due(self, now: datetime, limit: int) -> tuple[Source, ...]:
        with self._lock:
            due = [
                s
                for s in self._sources.values()
                if s.kind is SourceKind.AUDIO
                and s.tombstoned_at is None
                and s.retention_until is not None
                and s.retention_until <= now
                and s.content_hash is not None
                and s.state != "cancelling"
                and not self.import_running(s.workspace_id, s.id)
            ]
        return tuple(sorted(due, key=lambda s: s.retention_until or now)[:limit])

    @staticmethod
    def _erase_revision(source: Source) -> tuple[datetime, str, str]:
        assert source.retention_until is not None and source.content_hash is not None
        return source.retention_until, source.content_hash, source.state

    def claim_original_erase(self, source: Source) -> str | None:
        with self._lock:
            key = (source.workspace_id, source.id)
            stored = self._sources.get(key)
            revision = self._erase_revision(source)
            claim = self._erase_claims.get(key)
            if claim is not None:
                return claim[3] if claim[:3] == revision else None
            if (
                stored is None
                or stored.tombstoned_at is not None
                or stored.content_hash is None
                or stored.state == "cancelling"
                or self.import_running(*key)
                or self._erase_revision(stored) != revision
            ):
                return None
            self._erase_claims[key] = (*revision, "erase")
            return "erase"

    def prepare_original_erased(self, source: Source) -> bool:
        with self._lock:
            key = (source.workspace_id, source.id)
            stored = self._sources.get(key)
            revision = self._erase_revision(source)
            if (
                stored is None
                or stored.tombstoned_at is not None
                or stored.content_hash is None
                or self._erase_revision(stored) != revision
                or self._erase_claims.get(key) != (*revision, "erase")
            ):
                return False
            self._erase_claims[key] = (*revision, "audit")
            return True

    def mark_original_erased(self, source: Source) -> bool:
        with self._lock:
            key = (source.workspace_id, source.id)
            stored = self._sources.get(key)
            revision = self._erase_revision(source)
            if (
                stored is None
                or stored.tombstoned_at is not None
                or stored.content_hash is None
                or self._erase_revision(stored) != revision
                or self._erase_claims.get(key) != (*revision, "audit")
            ):
                return False
            self._sources[key] = replace(stored, retention_until=None)
            self._erase_claims.pop(key, None)
            return True

    def unfinished_deletes(self, limit: int) -> tuple[Source, ...]:
        with self._lock:
            return tuple(
                s
                for s in self._sources.values()
                if s.tombstoned_at is not None
                and s.kind is not SourceKind.MEETING
                and s.state == "deleting"
            )[:limit]

    def _check_hash_free(self, workspace_id: str, source: Source) -> None:
        """The rule schema 0010 enforces: one live source per owner and file hash."""
        if source.content_hash is None or source.tombstoned_at is not None:
            return
        clash = self.find_by_hash(workspace_id, source.owner_id, source.content_hash)
        if clash is not None and clash.id != source.id:
            raise DuplicateContentError("This owner already has a source with this hash")

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
        owned = [
            source
            for (stored_workspace, _), source in self._sources.items()
            if stored_workspace == workspace_id
            and source.owner_id == user_id
            and source.tombstoned_at is None
        ]
        owned.extend(
            Source.from_meeting(meeting)
            for meeting in self._meetings.all_for_workspace(workspace_id)
            if meeting.owner_id == user_id
        )
        if kinds is not None:
            owned = [source for source in owned if source.kind in kinds]
        if state is not None:
            owned = [source for source in owned if source.state == state]
        if title_query is not None:
            # ponytail: full scan of the owner's sources per query, a title index if libraries grow
            query = title_query.casefold()
            owned = [source for source in owned if query in (source.title or "").casefold()]
        owned.sort(key=lambda s: (s.created_at, s.id), reverse=True)
        if cursor is not None:
            owned = [s for s in owned if (s.created_at, s.id) < cursor]
        return tuple(owned[:limit])

    # Helpers for MemoryFragmentRepository.

    def kind_of(self, workspace_id: str, source_id: str) -> SourceKind | None:
        stored = self._sources.get((workspace_id, source_id))
        if stored is not None:
            return stored.kind
        return SourceKind.MEETING if self._meetings.exists(workspace_id, source_id) else None

    def status(self, workspace_id: str, source_id: str) -> tuple[bool, int | None] | None:
        """(deleted, current version) of a source of any owner, None if it does not exist. The
        version is None for a meeting: its versions live in the transcript. The fragment index
        reads this the way the SQLite index reads the sources table."""
        stored = self._sources.get((workspace_id, source_id))
        if stored is not None:
            return stored.tombstoned_at is not None, stored.version
        if not self._meetings.exists(workspace_id, source_id):
            return None
        try:
            self._meetings.get_for_processing(workspace_id, source_id)
        except NotFoundError:
            return True, None
        return False, None

    def set_version(self, workspace_id: str, source_id: str, version: int) -> None:
        with self._lock:
            stored = self._sources[(workspace_id, source_id)]
            self._sources[(workspace_id, source_id)] = replace(stored, version=version)


class MemoryFragmentRepository:
    def __init__(
        self, sources: MemorySourceRepository, transcripts: MemoryTranscriptRepository
    ) -> None:
        self._sources = sources
        self._transcripts = transcripts
        self._versions: dict[tuple[str, str], dict[int, tuple[Fragment, ...]]] = {}
        self._sections: dict[tuple[str, str], tuple[Section, ...]] = {}
        self._records: dict[tuple[str, str], dict[int, SourceVersion]] = {}

    def add_version(
        self,
        workspace_id: str,
        source_id: str,
        sections: tuple[Section, ...],
        fragments: tuple[Fragment, ...],
    ) -> int:
        kind = self._sources.kind_of(workspace_id, source_id)
        if kind is None:
            raise NotFoundError
        if kind is SourceKind.MEETING:
            raise ValueError("A meeting transcript is written through the transcript repository")
        stored = self._sources.get_for_processing(workspace_id, source_id)
        if stored.tombstoned_at is not None:
            raise NotFoundError  # deletion wins: a deleted source takes no new content
        check_version(source_id, sections, fragments)
        key = (workspace_id, source_id)
        versions = self._versions.setdefault(key, {})
        version = max(versions, default=0) + 1
        versions[version] = tuple(replace(f, version=version) for f in fragments)
        self._sections[key] = tuple(sections)
        self._sources.set_version(workspace_id, source_id, version)
        return version

    def latest(self, workspace_id: str, source_id: str) -> tuple[Fragment, ...]:
        if self._sources.kind_of(workspace_id, source_id) is SourceKind.MEETING:
            return self._transcript(workspace_id, source_id)
        versions = self._versions.get((workspace_id, source_id))
        if not versions:
            return ()
        return tuple(sorted(versions[max(versions)], key=lambda f: (f.ordinal, f.id)))

    def get_fragment(self, workspace_id: str, source_id: str, fragment_id: str) -> Fragment | None:
        return next((f for f in self.latest(workspace_id, source_id) if f.id == fragment_id), None)

    def outline(self, workspace_id: str, source_id: str) -> tuple[Section, ...]:
        return outline_order(self._sections.get((workspace_id, source_id), ()))

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
        kind = self._sources.kind_of(workspace_id, source_id)
        if kind is None:
            raise NotFoundError
        if kind is SourceKind.MEETING:
            raise ValueError("A meeting has no file versions")
        self._sources.get_for_processing(workspace_id, source_id)  # deletion wins: NotFoundError
        self._records.setdefault((workspace_id, source_id), {})[version] = SourceVersion(
            source_id,
            version,
            content_hash,
            parser,
            extraction_version,
            original_name,
            size_bytes,
            datetime.now(UTC),
        )

    def versions(
        self, workspace_id: str, user_id: str, source_id: str
    ) -> tuple[SourceVersion, ...]:
        self._sources.get_owned(workspace_id, user_id, source_id)  # NotFoundError otherwise
        recorded = self._records.get((workspace_id, source_id), {})
        return tuple(recorded[number] for number in sorted(recorded, reverse=True))

    def erase(self, workspace_id: str, source_id: str) -> None:
        if self._sources.kind_of(workspace_id, source_id) is SourceKind.MEETING:
            raise ValueError("Meeting transcripts are erased through the meeting service")
        self._records.pop((workspace_id, source_id), None)
        self._versions.pop((workspace_id, source_id), None)
        self._sections.pop((workspace_id, source_id), None)

    def _transcript(self, workspace_id: str, source_id: str) -> tuple[Fragment, ...]:
        try:
            segments = self._transcripts.latest(workspace_id, source_id)
        except NotFoundError:
            return ()
        return tuple(Fragment.from_segment(s, i) for i, s in enumerate(segments))
