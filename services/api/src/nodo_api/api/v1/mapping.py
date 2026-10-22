"""Turns domain objects into the API shapes (docs/API-V1.md). No I/O here."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime

from nodo_api.api.v1.schemas import (
    EvidenceOutput,
    GraphEdgeOutput,
    GraphNodeOutput,
    InsightAction,
    InsightDecision,
    InsightNode,
    Insights,
    MeetingListItem,
    MeetingOutput,
    SubgraphOutput,
)
from nodo_api.domain.graph import EntityType, MeetingDigest, Relation, Subgraph
from nodo_api.domain.models import Meeting
from nodo_api.errors import ValidationFailed
from nodo_api.services.ingestion import default_title


def encode_cursor(meeting: Meeting) -> str:
    raw = f"{meeting.created_at.isoformat()}|{meeting.id}".encode()
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


def meeting_output(meeting: Meeting, digest: MeetingDigest | None = None) -> MeetingOutput:
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


def subgraph_output(graph: Subgraph, depth: int) -> SubgraphOutput:
    meeting_of: dict[str, str] = {}
    for edge in graph.edges:
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
        ),
        edges=tuple(edge_output(e) for e in graph.edges),
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
