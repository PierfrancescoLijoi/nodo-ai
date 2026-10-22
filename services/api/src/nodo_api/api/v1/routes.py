from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from nodo_api.api.v1.schemas import (
    ExportInput,
    ExportOutput,
    GraphOutput,
    MeetingCreate,
    MeetingOutput,
    QueryFiltersInput,
    QueryInput,
    QueryOutput,
    ReviewInput,
    ReviewOutput,
    TranscriptPage,
    UploadSessionOutput,
    VoiceQueryInput,
)
from nodo_api.auth import AuthError, Principal
from nodo_api.errors import AuthenticationError
from nodo_api.services.query import QueryFilters, QueryRequest

router = APIRouter(prefix="/v1")


def principal(request: Request) -> Principal:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise AuthenticationError
    try:
        return request.app.state.container.auth.authenticate(header[7:])
    except AuthError as exc:
        raise AuthenticationError from exc


PrincipalDep = Annotated[Principal, Depends(principal)]


@router.post("/meetings", response_model=MeetingOutput, status_code=status.HTTP_201_CREATED)
def create_meeting(
    payload: MeetingCreate,
    actor: PrincipalDep,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
    ],
):
    meeting = request.app.state.container.meeting_service.create(
        actor.workspace_id,
        actor.user_id,
        idempotency_key,
        payload.recording_attestation.model_dump(mode="json"),
        payload.project,
    )
    return _meeting_output(meeting)


@router.post(
    "/meetings/{meeting_id}/upload-sessions",
    response_model=UploadSessionOutput,
    status_code=status.HTTP_201_CREATED,
)
def upload_session(meeting_id: str, actor: PrincipalDep, request: Request):
    return request.app.state.container.meeting_service.upload_session(
        actor.workspace_id, actor.user_id, meeting_id
    )


@router.post("/meetings/{meeting_id}/complete", response_model=MeetingOutput)
def complete_meeting(meeting_id: str, actor: PrincipalDep, request: Request):
    meeting = request.app.state.container.meeting_service.complete(
        actor.workspace_id, actor.user_id, meeting_id
    )
    return _meeting_output(meeting)


@router.get("/meetings/{meeting_id}", response_model=MeetingOutput)
def get_meeting(meeting_id: str, actor: PrincipalDep, request: Request):
    meeting = request.app.state.container.meeting_service.get(
        actor.workspace_id, actor.user_id, meeting_id
    )
    return _meeting_output(meeting)


@router.get("/meetings/{meeting_id}/transcript", response_model=TranscriptPage)
def get_transcript(
    meeting_id: str,
    actor: PrincipalDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    container = request.app.state.container
    container.meeting_service.get(actor.workspace_id, actor.user_id, meeting_id)
    segments = container.transcripts.latest(actor.workspace_id, meeting_id)
    return TranscriptPage(
        items=segments[offset : offset + limit],
        total=len(segments),
        limit=limit,
        offset=offset,
        version=segments[0].version,
    )


@router.patch("/meetings/{meeting_id}/review", response_model=ReviewOutput)
def review_meeting(meeting_id: str, payload: ReviewInput, actor: PrincipalDep, request: Request):
    segments = request.app.state.container.meeting_service.review(
        actor.workspace_id,
        actor.user_id,
        meeting_id,
        [item.model_dump() for item in payload.corrections],
        payload.base_version,
    )
    return ReviewOutput(version=segments[0].version, segments=segments)


@router.post("/query", response_model=QueryOutput)
def query(payload: QueryInput, actor: PrincipalDep, request: Request):
    result = request.app.state.container.query_service.query(
        actor.workspace_id,
        actor.user_id,
        _query_request(payload.question, payload.ui_locale, payload.filters),
    )
    return result


@router.post("/voice-query", response_model=QueryOutput)
def voice_query(payload: VoiceQueryInput, actor: PrincipalDep, request: Request):
    result = request.app.state.container.query_service.query(
        actor.workspace_id,
        actor.user_id,
        _query_request(payload.transcript_text, payload.ui_locale, payload.filters),
    )
    return result


@router.get("/graph/subgraph", response_model=GraphOutput)
def graph_subgraph(
    actor: PrincipalDep,
    request: Request,
    entity: Annotated[str, Query(min_length=1, max_length=128)],
    depth: Annotated[int, Query(ge=1, le=2)] = 2,
):
    container = request.app.state.container
    allowed = container.meetings.owned_ids(actor.workspace_id, actor.user_id)
    limit = container.settings.graph_node_limit
    nodes = container.graph.lookup(actor.workspace_id, entity, min(depth, 2), limit, allowed)[
        :limit
    ]
    return GraphOutput(nodes=nodes, depth=depth)


@router.post("/export", response_model=ExportOutput, status_code=status.HTTP_202_ACCEPTED)
def export_data(payload: ExportInput, actor: PrincipalDep, request: Request):
    job_id = request.app.state.container.meeting_service.export(
        actor.workspace_id, actor.user_id, payload.meeting_id
    )
    return ExportOutput(job_id=job_id)


@router.delete("/meetings/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting(meeting_id: str, actor: PrincipalDep, request: Request) -> Response:
    request.app.state.container.meeting_service.delete(
        actor.workspace_id, actor.user_id, meeting_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _query_request(question, locale, filters: QueryFiltersInput | None) -> QueryRequest:
    service_filters = QueryFilters(**filters.model_dump()) if filters else None
    return QueryRequest(question, locale, service_filters)


def _meeting_output(meeting) -> MeetingOutput:
    attestation = meeting.attestation
    return MeetingOutput(
        id=meeting.id,
        workspace_id=meeting.workspace_id,
        owner_id=meeting.owner_id,
        state=meeting.state,
        created_at=meeting.created_at,
        project=meeting.project,
        recording_attestation={
            "notice_version": attestation.notice_version,
            "locale": attestation.locale,
            "retention_policy_id": attestation.retention_policy_id,
            "acknowledged_at": attestation.acknowledged_at,
            "audit_event_id": attestation.audit_event_id,
            "signature": attestation.signature,
        },
    )
