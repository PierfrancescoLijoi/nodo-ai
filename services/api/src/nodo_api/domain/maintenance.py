"""What index maintenance looks at and what it reports. Ids and counts only, never text."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from nodo_api.domain.models import MeetingState


class FindingKind(StrEnum):
    MISSING_FROM_INDEX = "missing_from_index"  # a transcript with no search documents
    PARTIAL_INDEX = "partial_index"  # fewer documents than segments
    STALE_INDEX = "stale_index"  # documents the transcript no longer has
    LEFTOVER_INDEX = "leftover_index"  # documents of a deleted meeting
    LEFTOVER_GRAPH = "leftover_graph"  # graph evidence of a deleted meeting
    DANGLING_EVIDENCE = "dangling_evidence"  # graph evidence pointing at text that does not exist
    ENTITY_VECTORS_MISSING = "entity_vectors_missing"  # entities not findable by meaning yet


@dataclass(frozen=True, slots=True)
class MaintenanceMeeting:
    """A meeting as the maintenance job sees it, across workspaces and including deleted ones."""

    workspace_id: str
    meeting_id: str
    owner_id: str
    state: MeetingState
    tombstoned: bool


@dataclass(frozen=True, slots=True)
class Finding:
    kind: FindingKind
    workspace_id: str
    meeting_id: str


@dataclass(frozen=True, slots=True)
class Failure:
    workspace_id: str
    meeting_id: str
    error: str  # the exception type only: messages may carry text


@dataclass(frozen=True, slots=True)
class PendingJob:
    """A job the outbox recorded and nobody sent. Identifiers only."""

    workspace_id: str
    job_key: str
    job_type: str
    payload: dict[str, str]


@dataclass(slots=True)
class SweepReport:
    republished: int = 0  # recorded but unsent jobs sent again
    restarted: int = 0  # meetings stuck in processing, started again
    failed: list[Failure] = field(default_factory=list)


@dataclass(slots=True)
class IndexReport:
    checked: int = 0  # meetings looked at
    skipped: int = 0  # meetings with nothing to compare yet (no transcript)
    repaired: int = 0
    findings: list[Finding] = field(default_factory=list)
    failed: list[Failure] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """No failure, and nothing found that a repair run did not fix."""
        return not self.failed and (not self.findings or self.repaired >= len(self.findings))
