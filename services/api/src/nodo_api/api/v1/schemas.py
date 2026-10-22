from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nodo_api.domain.models import Answerability, Locale, MeetingState


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)


class AttestationInput(FrozenModel):
    notice_version: str = Field(min_length=1, max_length=64)
    locale: Locale
    retention_policy_id: str = Field(min_length=1, max_length=64)
    acknowledged: Literal[True]


class MeetingCreate(FrozenModel):
    recording_attestation: AttestationInput
    project: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=120)


class AttestationOutput(FrozenModel):
    notice_version: str
    locale: Locale
    retention_policy_id: str
    acknowledged_at: datetime
    audit_event_id: str
    signature: str


class InsightNode(FrozenModel):
    id: str
    label: str


class InsightDecision(FrozenModel):
    id: str
    label: str
    status: Literal["proposed", "approved", "superseded"]
    effective_at: datetime | None


class InsightAction(FrozenModel):
    id: str
    label: str
    owner_label: str | None
    due_at: datetime | None
    status: Literal["proposed", "approved"]


class Insights(FrozenModel):
    people: tuple[InsightNode, ...] = ()
    topics: tuple[InsightNode, ...] = ()
    decisions: tuple[InsightDecision, ...] = ()
    actions: tuple[InsightAction, ...] = ()


class SpeakerOutput(FrozenModel):
    id: str
    name: str | None
    segments: int
    talk_ms: int


class SpeakerNameInput(FrozenModel):
    name: str | None = Field(default=None, max_length=80)


class MeetingOutput(FrozenModel):
    id: str
    workspace_id: str
    owner_id: str
    title: str
    state: MeetingState
    created_at: datetime
    project: str | None
    duration_ms: int | None
    summary: str | None
    recording_attestation: AttestationOutput
    insights: Insights = Insights()
    speakers: tuple[SpeakerOutput, ...] = ()


class MeetingListItem(FrozenModel):
    id: str
    title: str
    state: MeetingState
    created_at: datetime
    project: str | None
    duration_ms: int | None
    summary: str | None
    people: tuple[str, ...]
    topics: tuple[str, ...]
    decision_count: int
    action_count: int


class MeetingList(FrozenModel):
    items: tuple[MeetingListItem, ...]
    next_cursor: str | None


class UploadSessionOutput(FrozenModel):
    id: str
    upload_url: str
    expires_in_seconds: int


class SegmentOutput(FrozenModel):
    id: str
    meeting_id: str
    speaker_id: str
    start_ms: int
    end_ms: int
    text: str
    confidence: float
    version: int
    locale: Locale


class TranscriptPage(FrozenModel):
    items: tuple[SegmentOutput, ...]
    total: int
    limit: int
    offset: int
    version: int


class CorrectionInput(FrozenModel):
    segment_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=4000)


class ReviewInput(FrozenModel):
    corrections: tuple[CorrectionInput, ...] = Field(min_length=1, max_length=500)
    base_version: int | None = Field(default=None, ge=1)


class ReviewOutput(FrozenModel):
    version: int
    segments: tuple[SegmentOutput, ...]


class QueryFiltersInput(FrozenModel):
    time_from: datetime | None = None
    time_to: datetime | None = None
    project: str | None = Field(default=None, max_length=64)


class QueryInput(FrozenModel):
    question: str = Field(min_length=1, max_length=2000)
    ui_locale: Locale
    filters: QueryFiltersInput | None = None


class VoiceQueryInput(FrozenModel):
    transcript_text: str = Field(min_length=1, max_length=2000)
    ui_locale: Locale
    filters: QueryFiltersInput | None = None
    tts: bool = False


class CitationOutput(FrozenModel):
    meeting_id: str
    segment_id: str
    start_ms: int
    end_ms: int
    quote: str
    speaker_id: str = ""


class QueryOutput(FrozenModel):
    answer: str
    answerability: Answerability
    citations: tuple[CitationOutput, ...]
    related_entities: tuple[str, ...]
    query_trace_id: str
    model_version: str
    retrieval_version: str


class EvidenceOutput(FrozenModel):
    meeting_id: str
    segment_id: str


class GraphNodeOutput(FrozenModel):
    id: str
    type: Literal["person", "project", "system", "topic", "decision", "action", "meeting"]
    label: str
    status: str | None
    meeting_id: str | None


class GraphEdgeOutput(FrozenModel):
    id: str
    source: str
    target: str
    kind: Literal[
        "MENTIONED_IN",
        "ABOUT",
        "DECIDED_IN",
        "PROPOSED_BY",
        "ASSIGNED_TO",
        "DEPENDS_ON",
        "SUPERSEDES",
        "CONTRADICTS",
    ]
    status: Literal["proposed", "approved", "rejected"]
    valid_from: datetime | None
    valid_to: datetime | None
    confidence: float
    evidence: tuple[EvidenceOutput, ...]


class SubgraphOutput(FrozenModel):
    nodes: tuple[GraphNodeOutput, ...]
    edges: tuple[GraphEdgeOutput, ...]
    depth: int
    truncated: bool


class ReviewRelationInput(FrozenModel):
    status: Literal["approved", "rejected"]


class ChangeOutput(FrozenModel):
    id: str
    new_label: str
    old_label: str
    meeting_id: str | None
    at: datetime | None


class ChangesOutput(FrozenModel):
    items: tuple[ChangeOutput, ...]


class ExportInput(FrozenModel):
    meeting_id: str | None = Field(default=None, max_length=128)


class ExportOutput(FrozenModel):
    job_id: str
    status: Literal["queued"] = "queued"
