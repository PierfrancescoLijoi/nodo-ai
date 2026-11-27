"""Validated Ask scopes and their authorized, retrieval-ready form (Phase 4)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from nodo_api.errors import NotAvailableOnEngine, NotFoundError, ValidationFailed

MAX_QUERY_SOURCES = 100


class CitationFormat(StrEnum):
    LEGACY = "legacy"
    SOURCE = "source"


class QueryScopeMode(StrEnum):
    LEGACY = "legacy"
    ALL = "all"
    COLLECTION = "collection"
    SOURCES = "sources"
    MEETINGS = "meetings"
    SOURCES_ONLY = "sources_only"


@dataclass(frozen=True, slots=True)
class QueryScope:
    mode: QueryScopeMode = QueryScopeMode.LEGACY
    collection_id: str | None = None
    source_ids: tuple[str, ...] = ()
    meeting_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode is QueryScopeMode.COLLECTION:
            if not self.collection_id or self.source_ids or self.meeting_ids:
                raise ValueError("A collection scope needs only collection_id")
        elif self.mode is QueryScopeMode.SOURCES:
            if self.collection_id or not self.source_ids or self.meeting_ids:
                raise ValueError("A sources scope needs only source_ids")
        elif self.mode is QueryScopeMode.MEETINGS:
            if self.collection_id or self.source_ids or not self.meeting_ids:
                raise ValueError("A meetings scope needs only meeting_ids")
        elif self.collection_id or self.source_ids or self.meeting_ids:
            raise ValueError("This scope mode takes no ids")
        if len(self.source_ids) > MAX_QUERY_SOURCES:
            raise ValueError(f"At most {MAX_QUERY_SOURCES} sources may be selected")
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("Selected source ids must be unique")
        if len(self.meeting_ids) > MAX_QUERY_SOURCES or len(set(self.meeting_ids)) != len(
            self.meeting_ids
        ):
            raise ValueError("Selected meeting ids must be unique and within the source limit")


@dataclass(frozen=True, slots=True)
class ResolvedQueryScope:
    mode: QueryScopeMode
    source_ids: frozenset[str] | None
    sources_only: bool

    @classmethod
    def legacy(cls) -> ResolvedQueryScope:
        return cls(QueryScopeMode.LEGACY, None, True)


def resolve_query_scope(
    workspace_id: str,
    owner_id: str,
    scope: QueryScope,
    citation_format: CitationFormat,
    sources: Any,
    collections: Any,
    meetings: Any = None,
) -> ResolvedQueryScope:
    """Resolve only authorized Source ids before retrieval. Foreign ids stay indistinguishable
    from absent ids because the owner-scoped repositories raise the same NotFoundError."""

    if scope.mode is QueryScopeMode.LEGACY:
        return ResolvedQueryScope.legacy()
    if citation_format is not CitationFormat.SOURCE:
        raise ValidationFailed("Source scopes require source citations")
    if sources is None:
        raise NotAvailableOnEngine

    if scope.mode is QueryScopeMode.SOURCES:
        ids = tuple(
            sources.get_owned(workspace_id, owner_id, source_id).id
            for source_id in scope.source_ids
        )
    elif scope.mode is QueryScopeMode.MEETINGS:
        if meetings is None:
            raise NotAvailableOnEngine
        ids = tuple(
            meetings.get_owned(workspace_id, owner_id, meeting_id).id
            for meeting_id in scope.meeting_ids
        )
    elif scope.mode is QueryScopeMode.COLLECTION:
        if collections is None:
            raise NotAvailableOnEngine
        # Validate ownership first. Adapters use one not-found response for foreign and absent ids.
        collections.get_owned(workspace_id, owner_id, scope.collection_id)
        members = collections.list_sources(
            workspace_id, owner_id, scope.collection_id, MAX_QUERY_SOURCES + 1, None
        )
        if len(members) > MAX_QUERY_SOURCES:
            raise ValidationFailed("The query scope contains too many sources")
        ids = tuple(source.id for source in members)
    else:
        # `all` and `sources_only` mean all current authorized Sources. Passing None to the
        # retrieval adapters preserves their in-store owner filter; paging a first 100 Sources
        # here would silently turn an all-knowledge query into an arbitrary recent subset.
        return ResolvedQueryScope(scope.mode, None, scope.mode is QueryScopeMode.SOURCES_ONLY)

    if len(ids) > MAX_QUERY_SOURCES:
        raise ValidationFailed("The query scope contains too many sources")
    if scope.mode is QueryScopeMode.SOURCES and len(ids) != len(scope.source_ids):
        # Defensive only: owner-scoped get_owned normally raises before this point.
        raise NotFoundError
    return ResolvedQueryScope(
        scope.mode,
        frozenset(ids),
        scope.mode is QueryScopeMode.SOURCES_ONLY,
    )
