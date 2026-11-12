from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from nodo_api.domain.sources import SourceKind
from nodo_api.errors import ValidationFailed
from nodo_api.ports import AuditLog, FragmentRepository, GraphStore, SourceRepository
from nodo_api.services.answer_cache import AnswerCache, invalidate


class SourceService:
    """Operations that must erase every derived copy of a non-meeting source."""

    def __init__(
        self,
        sources: SourceRepository,
        fragments: FragmentRepository,
        graph: GraphStore,
        answers: AnswerCache | None,
        audit: AuditLog,
        clock: Callable[[], datetime],
    ) -> None:
        self._sources = sources
        self._fragments = fragments
        self._graph = graph
        self._answers = answers
        self._audit = audit
        self._clock = clock

    def delete(self, workspace_id: str, user_id: str, source_id: str) -> None:
        """Tombstone first, then erase derived data. Safe to retry after a partial failure."""
        source = self._sources.get_owned(workspace_id, user_id, source_id, include_tombstoned=True)
        if source.kind is SourceKind.MEETING:
            raise ValidationFailed("use the meetings API")

        first_time = source.tombstoned_at is None
        if first_time:
            self._sources.save(workspace_id, replace(source, tombstoned_at=self._clock()))
        self._graph.erase(workspace_id, source_id)
        self._fragments.erase(workspace_id, source_id)
        invalidate(self._answers, workspace_id, user_id)
        if first_time:
            self._audit.append(workspace_id, "source_deleted", {"kind": source.kind.value})
