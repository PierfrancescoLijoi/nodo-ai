from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nodo_api.domain.models import Answerability, Locale, MeetingState
from nodo_api.domain.retrieval import MAX_QUERY_SOURCES, CitationFormat, QueryScopeMode
from nodo_api.domain.sources import ImportState, SourceKind, collection_name


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


class SourceOutput(FrozenModel):
    id: str
    kind: SourceKind
    title: str | None
    state: str
    created_at: datetime
    origin: str | None
    language: Locale | None
    version: int
    retention_until: datetime | None = None  # when the stored original is erased (FR-4)


class SourceList(FrozenModel):
    items: tuple[SourceOutput, ...]
    next_cursor: str | None


class SourceCreate(FrozenModel):
    filename: str = Field(min_length=1, max_length=200)
    size_bytes: int = Field(ge=1)
    kind: SourceKind | None = None
    origin_note: str | None = Field(default=None, max_length=500)


class WatchFolderCreate(FrozenModel):
    path: str = Field(min_length=1, max_length=1024)


class WatchFolderOutput(FrozenModel):
    """A watched folder; its path is answered to its owner only."""

    id: str
    path: str
    enabled: bool
    created_at: datetime


class WatchFolderList(FrozenModel):
    items: tuple[WatchFolderOutput, ...]


class SourceCreateOutput(FrozenModel):
    source: SourceOutput
    upload_url: str
    expires_in_seconds: int


class ImportOutput(FrozenModel):
    state: ImportState
    step: str | None
    done: int
    total: int
    attempt: int
    error_code: str | None
    unchanged: bool = False
    existing_source_id: str | None = None
    # Stable notice codes; "slow_on_this_machine": the local engine may take long (PERFORMANCE.md).
    warnings: tuple[str, ...] = ()


class ProcessingConfirmation(FrozenModel):
    """The owner of an imported recording states "I may process this recording"."""

    confirmed: Literal[True]


class ImportStatusOutput(FrozenModel):
    import_: ImportOutput = Field(alias="import", serialization_alias="import")


class SourceVersionOutput(FrozenModel):
    version: int
    content_hash_prefix: str
    parser: str
    created_at: datetime
    size_bytes: int


class SourceVersionList(FrozenModel):
    items: tuple[SourceVersionOutput, ...]


class SectionOutput(FrozenModel):
    id: str
    parent_id: str | None
    ordinal: int
    level: int
    title: str
    fragment_count: int = 0  # fragments of the latest version directly in this section
    first_locator: dict[str, Any] | None = None  # where its first fragment is, in reading order


class SourceOutline(FrozenModel):
    sections: tuple[SectionOutput, ...]
    version: int


class FragmentOutput(FrozenModel):
    id: str
    section_id: str | None
    ordinal: int
    text: str
    locator: dict[str, Any]
    speaker_id: str | None
    confidence: float | None


class SourceFragments(FrozenModel):
    version: int
    fragments: tuple[FragmentOutput, ...]
    next_offset: int | None = None  # set only when a limit cut the answer short


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


class QueryScopeInput(FrozenModel):
    mode: QueryScopeMode = QueryScopeMode.LEGACY
    collection_id: str | None = Field(default=None, min_length=1, max_length=64)
    source_ids: tuple[str, ...] = Field(default=(), max_length=MAX_QUERY_SOURCES)

    @model_validator(mode="after")
    def valid_shape(self):
        if self.mode is QueryScopeMode.COLLECTION:
            valid = self.collection_id is not None and not self.source_ids
        elif self.mode is QueryScopeMode.SOURCES:
            valid = self.collection_id is None and bool(self.source_ids)
        else:
            valid = self.collection_id is None and not self.source_ids
        if not valid or len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("Invalid query scope")
        return self


class QueryInput(FrozenModel):
    question: str = Field(min_length=1, max_length=2000)
    ui_locale: Locale
    filters: QueryFiltersInput | None = None
    scope: QueryScopeInput = QueryScopeInput()
    citation_format: CitationFormat = CitationFormat.LEGACY


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
    source_id: str | None = None
    fragment_id: str | None = None
    source_version: int | None = None
    locator: dict[str, Any] | None = None


class QueryOutput(FrozenModel):
    answer: str
    answerability: Answerability
    citations: tuple[CitationOutput, ...]
    related_entities: tuple[str, ...]
    query_trace_id: str
    model_version: str
    retrieval_version: str
    degraded: bool = False


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
    status: Literal["queued", "running", "ready", "failed"] = "queued"
    expires_at: datetime | None = None
    download_path: str | None = None


# --- concept hierarchy (ADR-030): new models only, /v1/graph/* keeps hiding CHILD_OF --------------


class ConceptOutput(FrozenModel):
    id: str
    type: str
    label: str
    status: str | None


class SourceMentionOutput(FrozenModel):
    source_id: str
    title: str | None


class HierarchyEvidenceOutput(FrozenModel):
    source_id: str
    fragment_id: str
    locator: dict[str, Any] | None


class HierarchyLinkOutput(FrozenModel):
    id: str
    child_id: str
    parent_id: str
    status: Literal["proposed", "approved", "rejected"]
    confidence: float
    evidence: tuple[HierarchyEvidenceOutput, ...]


class ConceptHierarchyOutput(FrozenModel):
    concept: ConceptOutput
    mentioned_in: tuple[SourceMentionOutput, ...]
    nodes: tuple[ConceptOutput, ...]
    links: tuple[HierarchyLinkOutput, ...]
    truncated: bool


class HierarchyProposalList(FrozenModel):
    items: tuple[HierarchyLinkOutput, ...]
    nodes: tuple[ConceptOutput, ...]
    truncated: bool


class HierarchyReviewInput(FrozenModel):
    status: Literal["approved", "rejected"]


class ParentSuggestionInput(FrozenModel):
    locale: Locale = Locale.EN


# --- collections of sources (FR-4, FR-8); local and memory engines, 501 on Azure ----------------


def _checked_name(name: str | None) -> str | None:
    # collection_name raises a ValueError that never repeats the name: a plain 422.
    return None if name is None else collection_name(name)


class CollectionOutput(FrozenModel):
    id: str
    parent_id: str | None
    name: str
    created_at: datetime
    source_count: int


class CollectionList(FrozenModel):
    items: tuple[CollectionOutput, ...]


class PageOutput(FrozenModel):
    id: str
    kind: str
    subject_id: str
    status: str
    current_revision: int | None
    created_at: datetime


class PageRevisionOutput(FrozenModel):
    number: int
    body_md: str
    author_id: str
    created_at: datetime
    machine_generated: bool
    claims: tuple[PageClaimOutput, ...]


class PageCitationInput(FrozenModel):
    source_id: str = Field(min_length=1, max_length=64)
    fragment_id: str = Field(min_length=1, max_length=128)
    source_version: int = Field(ge=1)
    locator: dict[str, Any]
    quote: str = Field(min_length=1, max_length=10_000)


class PageClaimInput(FrozenModel):
    span_start: int = Field(ge=0)
    span_end: int = Field(ge=0)
    citations: tuple[PageCitationInput, ...] = ()


class PageEditInput(FrozenModel):
    body_md: str = Field(min_length=1, max_length=200_000)
    claims: tuple[PageClaimInput, ...] = ()


class PageRefreshInput(FrozenModel):
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


class PageCitationOutput(FrozenModel):
    source_id: str
    fragment_id: str
    source_version: int
    locator: dict[str, Any]
    quote: str


class PageClaimOutput(FrozenModel):
    ordinal: int
    span_start: int
    span_end: int
    text: str
    machine_generated: bool
    citations: tuple[PageCitationOutput, ...]


class PageList(FrozenModel):
    items: tuple[PageOutput, ...]


class PageFindingOutput(FrozenModel):
    kind: str
    page_id: str
    revision: int | None
    claim_ordinal: int | None


class PageHealthOutput(FrozenModel):
    items: tuple[PageFindingOutput, ...]


class GraphInsightsOutput(FrozenModel):
    isolated: tuple[str, ...]
    bridges: tuple[str, ...]
    truncated: bool


class CollectionCreate(FrozenModel):
    name: str
    parent_id: str | None = Field(default=None, max_length=64)

    _name = field_validator("name")(_checked_name)


class CollectionUpdate(FrozenModel):
    """`parent_id` sent as null moves to the root; left out, the parent stays."""

    name: str | None = None
    parent_id: str | None = Field(default=None, max_length=64)

    _name = field_validator("name")(_checked_name)
