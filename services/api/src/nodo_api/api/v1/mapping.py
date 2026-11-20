"""Turns domain objects into the API shapes (docs/API-V1.md). No I/O here."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from datetime import datetime

from nodo_api.api.v1.schemas import (
    CollectionOutput,
    ConceptOutput,
    EvidenceOutput,
    FragmentOutput,
    GraphEdgeOutput,
    GraphNodeOutput,
    HierarchyEvidenceOutput,
    HierarchyLinkOutput,
    ImportOutput,
    InsightAction,
    InsightDecision,
    InsightNode,
    Insights,
    MeetingListItem,
    MeetingOutput,
    SectionOutput,
    SourceOutput,
    SourceVersionOutput,
    SpeakerOutput,
    SubgraphOutput,
)
from nodo_api.domain.graph import (
    Entity,
    EntityType,
    Evidence,
    MeetingDigest,
    Relation,
    RelationKind,
    Subgraph,
)
from nodo_api.domain.models import Meeting, SpeakerSummary
from nodo_api.domain.sources import (
    Collection,
    Fragment,
    ImportJob,
    Locator,
    Section,
    Source,
    SourceVersion,
)
from nodo_api.errors import ValidationFailed
from nodo_api.services.ingestion import default_title


def encode_cursor(item: Meeting | Source) -> str:
    raw = f"{item.created_at.isoformat()}|{item.id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    try:
        created, meeting_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(created), meeting_id
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        raise ValidationFailed from error


def _decision_status(entity, digest: MeetingDigest) -> str:
    if entity.status == "superseded":
        return "superseded"
    return digest.review.get(entity.id, "proposed")


def insights(digest: MeetingDigest) -> Insights:
    return Insights(
        people=tuple(InsightNode(id=e.id, label=e.label) for e in digest.people),
        topics=tuple(InsightNode(id=e.id, label=e.label) for e in digest.topics),
        decisions=tuple(
            InsightDecision(
                id=e.id,
                label=e.label,
                status=_decision_status(e, digest),
                effective_at=e.effective_at,
            )
            for e in digest.decisions
        ),
        actions=tuple(
            InsightAction(
                id=e.id,
                label=e.label,
                owner_label=digest.action_owners.get(e.id),
                due_at=e.due_at,
                status=digest.review.get(e.id, "proposed"),
            )
            for e in digest.actions
        ),
    )


def meeting_output(
    meeting: Meeting,
    digest: MeetingDigest | None = None,
    speakers: tuple[SpeakerSummary, ...] = (),
) -> MeetingOutput:
    a = meeting.attestation
    return MeetingOutput(
        id=meeting.id,
        workspace_id=meeting.workspace_id,
        owner_id=meeting.owner_id,
        title=default_title(meeting),
        state=meeting.state,
        created_at=meeting.created_at,
        project=meeting.project,
        duration_ms=meeting.duration_ms,
        summary=meeting.summary,
        recording_attestation={
            "notice_version": a.notice_version,
            "locale": a.locale,
            "retention_policy_id": a.retention_policy_id,
            "acknowledged_at": a.acknowledged_at,
            "audit_event_id": a.audit_event_id,
            "signature": a.signature,
        },
        insights=insights(digest) if digest else Insights(),
        speakers=tuple(
            SpeakerOutput(id=s.id, name=s.name, segments=s.segments, talk_ms=s.talk_ms)
            for s in speakers
        ),
    )


def list_item(meeting: Meeting, digest: MeetingDigest | None) -> MeetingListItem:
    digest = digest or MeetingDigest()
    return MeetingListItem(
        id=meeting.id,
        title=default_title(meeting),
        state=meeting.state,
        created_at=meeting.created_at,
        project=meeting.project,
        duration_ms=meeting.duration_ms,
        summary=meeting.summary,
        people=tuple(e.label for e in digest.people),
        topics=tuple(e.label for e in digest.topics),
        decision_count=len(digest.decisions),
        action_count=len(digest.actions),
    )


def source_output(source: Source) -> SourceOutput:
    return SourceOutput(
        id=source.id,
        kind=source.kind,
        title=source.title,
        state=source.state,
        created_at=source.created_at,
        origin=source.origin,
        language=source.language,
        version=source.version,
        retention_until=source.retention_until,
    )


def collection_output(collection: Collection) -> CollectionOutput:
    return CollectionOutput(
        id=collection.id,
        parent_id=collection.parent_id,
        name=collection.name,
        created_at=collection.created_at,
        source_count=collection.source_count,
    )


def import_output(
    job: ImportJob,
    *,
    unchanged: bool = False,
    existing_source_id: str | None = None,
    warnings: tuple[str, ...] = (),
) -> ImportOutput:
    return ImportOutput(
        state=job.state,
        step=job.step,
        done=job.done,
        total=job.total,
        attempt=job.attempt,
        error_code=job.error_code,
        unchanged=unchanged,
        existing_source_id=existing_source_id,
        warnings=warnings,
    )


def source_version_output(version: SourceVersion) -> SourceVersionOutput:
    return SourceVersionOutput(
        version=version.version,
        content_hash_prefix=version.content_hash[:12],
        parser=version.parser,
        created_at=version.created_at,
        size_bytes=version.size_bytes,
    )


def section_output(section: Section) -> SectionOutput:
    return SectionOutput(
        id=section.id,
        parent_id=section.parent_id,
        ordinal=section.ordinal,
        level=section.level,
        title=section.title,
    )


def fragment_output(fragment: Fragment) -> FragmentOutput:
    return FragmentOutput(
        id=fragment.id,
        section_id=fragment.section_id,
        ordinal=fragment.ordinal,
        text=fragment.text,
        locator=json.loads(fragment.locator.to_json()),
        speaker_id=fragment.speaker_id,
        confidence=fragment.confidence,
    )


def subgraph_output(graph: Subgraph, depth: int) -> SubgraphOutput:
    """The legacy graph view. The store already read it meetings-only; SOURCE nodes, CHILD_OF
    edges and edges touching a SOURCE node are dropped here too, as a guard, so the frozen
    clients and the OpenAPI literals never see the source vocabulary."""
    hidden = {n.id for n in graph.nodes if n.type is EntityType.SOURCE}
    edges = [
        edge
        for edge in graph.edges
        if edge.kind is not RelationKind.CHILD_OF
        and edge.source_id not in hidden
        and edge.target_id not in hidden
    ]
    meeting_of: dict[str, str] = {}
    for edge in edges:
        if edge.evidence:
            first = edge.evidence[0].meeting_id
            meeting_of.setdefault(edge.source_id, first)
            meeting_of.setdefault(edge.target_id, first)
    return SubgraphOutput(
        nodes=tuple(
            GraphNodeOutput(
                id=n.id,
                type=n.type.value,
                label=n.label,
                status=n.status,
                meeting_id=n.meeting_id if n.type is EntityType.MEETING else meeting_of.get(n.id),
            )
            for n in graph.nodes
            if n.id not in hidden
        ),
        edges=tuple(edge_output(e) for e in edges),
        depth=depth,
        truncated=graph.truncated,
    )


def edge_output(edge: Relation) -> GraphEdgeOutput:
    return GraphEdgeOutput(
        id=edge.id,
        source=edge.source_id,
        target=edge.target_id,
        kind=edge.kind.value,
        status=edge.status.value,
        valid_from=edge.valid_from,
        valid_to=edge.valid_to,
        confidence=edge.confidence,
        evidence=tuple(
            EvidenceOutput(meeting_id=e.meeting_id, segment_id=e.segment_id) for e in edge.evidence
        ),
    )


EVIDENCE_PER_LINK = 3  # keeps a hierarchy response bounded; the fragment is read for these only


def concept_output(entity: Entity) -> ConceptOutput:
    return ConceptOutput(
        id=entity.id, type=entity.type.value, label=entity.label, status=entity.status
    )


def hierarchy_link_output(
    edge: Relation, locate: Callable[[Evidence], Locator | None]
) -> HierarchyLinkOutput:
    evidence = []
    for item in edge.evidence[:EVIDENCE_PER_LINK]:
        locator = locate(item)
        evidence.append(
            HierarchyEvidenceOutput(
                source_id=item.source_id,
                fragment_id=item.fragment_id,
                locator=json.loads(locator.to_json()) if locator is not None else None,
            )
        )
    return HierarchyLinkOutput(
        id=edge.id,
        child_id=edge.source_id,
        parent_id=edge.target_id,
        status=edge.status.value,
        confidence=edge.confidence,
        evidence=tuple(evidence),
    )
