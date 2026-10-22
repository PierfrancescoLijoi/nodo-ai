from __future__ import annotations

from typing import Any, Protocol

from nodo_api.domain.models import GraphNode, Locale, Meeting, TranscriptSegment


class MeetingRepository(Protocol):
    def add(self, workspace_id: str, meeting: Meeting, idempotency_key: str) -> Meeting: ...

    def get(
        self, workspace_id: str, meeting_id: str, *, include_tombstoned: bool = False
    ) -> Meeting: ...

    def get_by_idempotency_key(self, workspace_id: str, key: str) -> Meeting | None: ...

    def save(self, workspace_id: str, meeting: Meeting) -> Meeting: ...

    def all_for_workspace(self, workspace_id: str) -> tuple[Meeting, ...]: ...


class TranscriptRepository(Protocol):
    def add_version(
        self, workspace_id: str, meeting_id: str, segments: tuple[TranscriptSegment, ...]
    ) -> tuple[TranscriptSegment, ...]: ...

    def get_version(
        self, workspace_id: str, meeting_id: str, version: int
    ) -> tuple[TranscriptSegment, ...]: ...

    def latest(self, workspace_id: str, meeting_id: str) -> tuple[TranscriptSegment, ...]: ...

    def get_segment(self, workspace_id: str, segment_id: str) -> TranscriptSegment | None: ...

    def erase(self, workspace_id: str, meeting_id: str) -> None: ...


class BlobStore(Protocol):
    def create_upload_session(self, workspace_id: str, meeting_id: str) -> dict[str, Any]: ...

    def erase(self, workspace_id: str, meeting_id: str) -> None: ...


class JobQueue(Protocol):
    def publish_once(
        self, workspace_id: str, meeting_id: str, job_type: str, payload: dict[str, Any]
    ) -> str: ...


class Retriever(Protocol):
    def search(
        self, workspace_id: str, query: str, filters: dict[str, Any] | None
    ) -> tuple[TranscriptSegment, ...]: ...

    def erase(self, workspace_id: str, meeting_id: str) -> None: ...


class GraphStore(Protocol):
    def lookup(
        self, workspace_id: str, entity: str, depth: int, node_limit: int
    ) -> tuple[GraphNode, ...]: ...

    def erase(self, workspace_id: str, meeting_id: str) -> None: ...


class LlmClient(Protocol):
    def answer(
        self,
        workspace_id: str,
        question: str,
        locale: Locale,
        evidence: tuple[TranscriptSegment, ...],
        graph: tuple[GraphNode, ...],
    ) -> Any: ...


class AuditLog(Protocol):
    def append(self, workspace_id: str, event_type: str, metadata: dict[str, str]) -> str: ...
