from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nodo_api.domain.models import Answerability, Locale, MeetingState


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)


class AttestationInput(FrozenModel):
    notice_version: str = Field(min_length=1)
    locale: Locale
    retention_policy_id: str = Field(min_length=1)
    acknowledged: Literal[True]


class MeetingCreate(FrozenModel):
    recording_attestation: AttestationInput
    project: str | None = None


class AttestationOutput(FrozenModel):
    notice_version: str
    locale: Locale
    retention_policy_id: str
    acknowledged_at: datetime
    audit_event_id: str
    signature: str


class MeetingOutput(FrozenModel):
    id: str
    workspace_id: str
    owner_id: str
    state: MeetingState
    created_at: datetime
    project: str | None
    recording_attestation: AttestationOutput


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
    segment_id: str
    text: str


class ReviewInput(FrozenModel):
    corrections: tuple[CorrectionInput, ...] = Field(min_length=1)


class ReviewOutput(FrozenModel):
    version: int
    segments: tuple[SegmentOutput, ...]


class QueryFiltersInput(FrozenModel):
    time_from: datetime | None = None
    time_to: datetime | None = None
    project: str | None = None


class QueryInput(FrozenModel):
    question: str = Field(min_length=1)
    ui_locale: Locale
    filters: QueryFiltersInput | None = None


class VoiceQueryInput(FrozenModel):
    transcript_text: str = Field(min_length=1)
    ui_locale: Locale
    filters: QueryFiltersInput | None = None
    tts: bool = False


class CitationOutput(FrozenModel):
    meeting_id: str
    segment_id: str
    start_ms: int
    end_ms: int
    quote: str


class QueryOutput(FrozenModel):
    answer: str
    answerability: Answerability
    citations: tuple[CitationOutput, ...]
    related_entities: tuple[str, ...]
    query_trace_id: str
    model_version: str
    retrieval_version: str


class GraphNodeOutput(FrozenModel):
    id: str
    label: str
    meeting_id: str
    segment_id: str


class GraphOutput(FrozenModel):
    nodes: tuple[GraphNodeOutput, ...]
    edges: tuple[dict[str, str], ...] = ()
    depth: int


class ExportInput(FrozenModel):
    meeting_id: str | None = None


class ExportOutput(FrozenModel):
    job_id: str
    status: Literal["queued"] = "queued"
