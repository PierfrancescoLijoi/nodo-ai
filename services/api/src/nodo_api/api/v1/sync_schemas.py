"""The package a local engine sends to the cloud to copy one meeting (ADR-015).

Everything in it is data the owner already has locally: the transcript versions, the speaker names,
the knowledge-graph contribution with its review decisions, and the original attestation. Ids that
depend on the workspace (graph entities, relations) are not sent: the receiver derives its own from
labels, which is what makes the copy idempotent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from nodo_api.api.v1.schemas import FrozenModel
from nodo_api.domain.graph import EntityType, RelationKind, ReviewStatus
from nodo_api.domain.models import Locale

MAX_SEGMENTS = 30_000
MAX_ENTITIES = 500
MAX_RELATIONS = 1_000


class SyncSegment(FrozenModel):
    id: str = Field(min_length=1, max_length=64)
    speaker_id: str = Field(pattern=r"^S[1-9][0-9]{0,3}$")
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=8_000)
    confidence: float = Field(ge=0, le=1)
    locale: Locale

    @model_validator(mode="after")
    def ordered(self) -> SyncSegment:
        if self.end_ms < self.start_ms:
            raise ValueError("A segment ends after it starts")
        return self


class SyncVersion(FrozenModel):
    version: int = Field(ge=1, le=1_000)
    segments: tuple[SyncSegment, ...] = Field(min_length=1, max_length=MAX_SEGMENTS)


class SyncAttestation(FrozenModel):
    notice_version: str = Field(min_length=1, max_length=64)
    locale: Locale
    retention_policy_id: str = Field(min_length=1, max_length=64)
    acknowledged_at: datetime
    audit_event_id: str = Field(min_length=1, max_length=128)
    signature: str = Field(min_length=1, max_length=256)


class SyncEntity(FrozenModel):
    type: EntityType
    label: str = Field(min_length=1, max_length=240)
    evidence: tuple[str, ...] = Field(min_length=1, max_length=200)
    due_at: datetime | None = None
    effective_at: datetime | None = None

    @model_validator(mode="after")
    def not_a_meeting(self) -> SyncEntity:
        if self.type is EntityType.MEETING:
            raise ValueError("The meeting node is created by the receiver")
        return self


class SyncRef(FrozenModel):
    """An endpoint by type and label, or the meeting itself (both left empty)."""

    type: EntityType | None = None
    label: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def both_or_neither(self) -> SyncRef:
        if (self.type is None) != (self.label is None):
            raise ValueError("A reference has a type and a label, or neither")
        if self.type is EntityType.MEETING:
            raise ValueError("The meeting is the reference with no type")
        return self


class SyncRelation(FrozenModel):
    source: SyncRef
    kind: RelationKind
    target: SyncRef
    confidence: float = Field(ge=0, le=1)
    status: ReviewStatus
    evidence: tuple[str, ...] = Field(min_length=1, max_length=200)


class SyncSource(FrozenModel):
    """How the local copy was produced; kept for provenance, never trusted for anything else."""

    engine: str = Field(default="local", max_length=40)
    speech: str = Field(default="", max_length=80)
    language_model: str = Field(default="", max_length=80)


class SyncPackage(FrozenModel):
    meeting_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    title: str | None = Field(default=None, max_length=120)
    summary: str | None = Field(default=None, max_length=4_000)
    duration_ms: int | None = Field(default=None, ge=0)
    project: str | None = Field(default=None, max_length=120)
    created_at: datetime
    state: Literal["review", "ready"]
    speaker_names: dict[str, str] = Field(default_factory=dict, max_length=50)
    attestation: SyncAttestation
    versions: tuple[SyncVersion, ...] = Field(min_length=1, max_length=100)
    entities: tuple[SyncEntity, ...] = Field(default=(), max_length=MAX_ENTITIES)
    relations: tuple[SyncRelation, ...] = Field(default=(), max_length=MAX_RELATIONS)
    source: SyncSource = SyncSource()


class SyncResult(FrozenModel):
    meeting_id: str
    created: bool
    versions_added: int
    segments_indexed: int
    entities: int
    relations: int
