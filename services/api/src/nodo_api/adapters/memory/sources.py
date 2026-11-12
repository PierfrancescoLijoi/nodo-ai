"""Sources and fragments in memory, for tests and the demo backend (ADR-029).

Meetings are not stored here: the source repository reads them from the meeting repository and
merges them in, the way the SQLite triggers copy meetings into the sources table."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from nodo_api.adapters.memory.adapters import MemoryMeetingRepository, MemoryTranscriptRepository
from nodo_api.domain.sources import (
    Fragment,
    Section,
    Source,
    SourceKind,
    check_version,
    outline_order,
)
from nodo_api.errors import NotFoundError


class MemorySourceRepository:
    def __init__(self, meetings: MemoryMeetingRepository) -> None:
        self._meetings = meetings
        self._sources: dict[tuple[str, str], Source] = {}

    def add(self, workspace_id: str, source: Source) -> Source:
        if source.kind is SourceKind.MEETING:
            raise ValueError("Meetings are added through the meeting repository")
        if self.kind_of(workspace_id, source.id) is not None:
            raise ValueError("A source with this id already exists")
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

    def save(self, workspace_id: str, source: Source) -> Source:
        if source.kind is SourceKind.MEETING:
            raise ValueError("Meetings are saved through the meeting repository")
        stored = self._sources.get((workspace_id, source.id))
        if stored is None or stored.kind is not source.kind:
            raise NotFoundError
        # The owner never moves and the version belongs to the fragment repository.
        saved = replace(source, owner_id=stored.owner_id, version=stored.version)
        self._sources[(workspace_id, source.id)] = saved
        return saved

    def list_owned(
        self,
        workspace_id: str,
        user_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
        kinds: frozenset[SourceKind] | None = None,
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

    def set_version(self, workspace_id: str, source_id: str, version: int) -> None:
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

    def erase(self, workspace_id: str, source_id: str) -> None:
        if self._sources.kind_of(workspace_id, source_id) is SourceKind.MEETING:
            raise ValueError("Meeting transcripts are erased through the meeting service")
        self._versions.pop((workspace_id, source_id), None)
        self._sections.pop((workspace_id, source_id), None)

    def _transcript(self, workspace_id: str, source_id: str) -> tuple[Fragment, ...]:
        try:
            segments = self._transcripts.latest(workspace_id, source_id)
        except NotFoundError:
            return ()
        return tuple(Fragment.from_segment(s, i) for i, s in enumerate(segments))
