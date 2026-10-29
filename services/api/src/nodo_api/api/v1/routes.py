from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.concurrency import run_in_threadpool
from opentelemetry import trace

from nodo_api.api.v1.mapping import (
    decode_cursor,
    edge_output,
    encode_cursor,
    list_item,
    meeting_output,
    subgraph_output,
)
from nodo_api.api.v1.schemas import (
    ChangeOutput,
    ChangesOutput,
    ExportInput,
    ExportOutput,
    GraphEdgeOutput,
    MeetingCreate,
    MeetingList,
    MeetingOutput,
    QueryFiltersInput,
    QueryInput,
    QueryOutput,
    ReviewInput,
    ReviewOutput,
    ReviewRelationInput,
    SpeakerNameInput,
    SubgraphOutput,
    TranscriptPage,
    UploadSessionOutput,
    VoiceQueryInput,
)
from nodo_api.api.v1.sync_schemas import SyncPackage, SyncResult
from nodo_api.auth import AuthError, Principal
from nodo_api.domain.graph import ReviewStatus
from nodo_api.errors import AuthenticationError, ValidationFailed
from nodo_api.services.query import QueryFilters, QueryRequest
from nodo_api.telemetry import pseudonym

router = APIRouter(prefix="/v1")


def principal(request: Request) -> Principal:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise AuthenticationError
    try:
        actor = request.app.state.container.auth.authenticate(header[7:])
    except AuthError as exc:
        raise AuthenticationError from exc
    # Operators can follow one user through the traces; the ids themselves never leave the process.
    current = trace.get_current_span()
    current.set_attribute("enduser.pseudo_id", pseudonym(actor.user_id))
    current.set_attribute("nodo.workspace", pseudonym(actor.workspace_id))
    return actor


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
        payload.title,
    )
    return meeting_output(meeting)


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
    return meeting_output(meeting)


@router.get("/meetings", response_model=MeetingList)
def list_meetings(
    actor: PrincipalDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
):
    container = request.app.state.container
    page = container.meetings.list_owned(
        actor.workspace_id, actor.user_id, limit + 1, decode_cursor(cursor)
    )
    items, has_more = page[:limit], len(page) > limit
    digests = container.graph_service.digests(
        actor.workspace_id, actor.user_id, [m.id for m in items]
    )
    return MeetingList(
        items=tuple(list_item(m, digests.get(m.id)) for m in items),
        next_cursor=encode_cursor(items[-1]) if has_more and items else None,
    )


@router.get("/meetings/{meeting_id}", response_model=MeetingOutput)
def get_meeting(meeting_id: str, actor: PrincipalDep, request: Request):
    container = request.app.state.container
    meeting = container.meeting_service.get(actor.workspace_id, actor.user_id, meeting_id)
    return _detail(container, actor, meeting)


def _detail(container, actor: Principal, meeting) -> MeetingOutput:
    digest = container.graph_service.digests(actor.workspace_id, actor.user_id, [meeting.id])
    speakers = container.meeting_service.speakers(actor.workspace_id, actor.user_id, meeting)
    return meeting_output(meeting, digest.get(meeting.id), speakers)


@router.patch("/meetings/{meeting_id}/speakers/{speaker_id}", response_model=MeetingOutput)
def name_speaker(
    meeting_id: str,
    speaker_id: Annotated[str, Path(pattern=r"^S[1-9][0-9]{0,3}$")],
    body: SpeakerNameInput,
    actor: PrincipalDep,
    request: Request,
):
    container = request.app.state.container
    meeting = container.meeting_service.name_speaker(
        actor.workspace_id, actor.user_id, meeting_id, speaker_id, body.name
    )
    return _detail(container, actor, meeting)


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
    return _ask(request, actor, payload.question, payload.ui_locale, payload.filters)


def _ask(request: Request, actor: Principal, text: str, locale, filters):
    """Questions are the expensive request (a model call each). A replica answers a limited number
    at once and tells the rest to come back, so a burst of questions can not starve reads."""
    container = request.app.state.container
    with container.query_gate:
        return container.query_service.query(
            actor.workspace_id, actor.user_id, _query_request(text, locale, filters)
        )


@router.post("/voice-query", response_model=QueryOutput)
def voice_query(payload: VoiceQueryInput, actor: PrincipalDep, request: Request):
    return _ask(request, actor, payload.transcript_text, payload.ui_locale, payload.filters)


@router.get("/graph/subgraph", response_model=SubgraphOutput)
def graph_subgraph(
    actor: PrincipalDep,
    request: Request,
    seed_id: Annotated[str | None, Query(max_length=64)] = None,
    entity: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    depth: Annotated[int, Query(ge=1, le=2)] = 2,
    include_proposed: bool = False,
    as_of: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
):
    graph = request.app.state.container.graph_service.subgraph(
        actor.workspace_id,
        actor.user_id,
        seed_id=seed_id,
        entity=entity,
        depth=depth,
        include_proposed=include_proposed,
        as_of=as_of,
        limit=limit,
    )
    return subgraph_output(graph, depth)


@router.get("/graph/reviews", response_model=SubgraphOutput)
def graph_reviews(
    actor: PrincipalDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """The inbox: relations the model proposed that nobody has decided yet (ADR-025)."""
    graph = request.app.state.container.graph_service.reviews(
        actor.workspace_id, actor.user_id, limit
    )
    return subgraph_output(graph, 1)


@router.patch("/graph/relations/{relation_id}", response_model=GraphEdgeOutput)
def review_relation(
    relation_id: str, payload: ReviewRelationInput, actor: PrincipalDep, request: Request
):
    relation = request.app.state.container.graph_service.review(
        actor.workspace_id, actor.user_id, relation_id, ReviewStatus(payload.status)
    )
    return edge_output(relation)


@router.get("/graph/changes", response_model=ChangesOutput)
def graph_changes(
    actor: PrincipalDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
):
    changes = request.app.state.container.graph_service.changes(
        actor.workspace_id, actor.user_id, limit
    )
    return ChangesOutput(
        items=tuple(
            ChangeOutput(
                id=c.id,
                new_label=c.new_label,
                old_label=c.old_label,
                meeting_id=c.meeting_id,
                at=c.at,
            )
            for c in changes
        )
    )


@router.post("/export", response_model=ExportOutput, status_code=status.HTTP_202_ACCEPTED)
def export_data(payload: ExportInput, actor: PrincipalDep, request: Request):
    export = request.app.state.container.export_service.request(
        actor.workspace_id, actor.user_id, payload.meeting_id
    )
    return _export_output(export)


def _export_output(export) -> ExportOutput:
    ready = export.state.value == "ready"
    return ExportOutput(
        job_id=export.id,
        status=export.state.value,
        expires_at=export.expires_at,
        download_path=f"/v1/export/{export.id}/download" if ready else None,
    )


@router.get("/export/{export_id}", response_model=ExportOutput)
def export_status(export_id: str, actor: PrincipalDep, request: Request):
    return _export_output(
        request.app.state.container.export_service.status(
            actor.workspace_id, actor.user_id, export_id
        )
    )


@router.get("/export/{export_id}/download")
def export_download(export_id: str, actor: PrincipalDep, request: Request) -> Response:
    data = request.app.state.container.export_service.download(
        actor.workspace_id, actor.user_id, export_id
    )
    return Response(
        content=data,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="nodo-export.json"'},
    )


@router.delete("/meetings/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting(meeting_id: str, actor: PrincipalDep, request: Request) -> Response:
    request.app.state.container.meeting_service.delete(
        actor.workspace_id, actor.user_id, meeting_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _query_request(question, locale, filters: QueryFiltersInput | None) -> QueryRequest:
    service_filters = QueryFilters(**filters.model_dump()) if filters else None
    return QueryRequest(question, locale, service_filters)


MAX_SYNC_BYTES = 40 * 1024 * 1024  # a long meeting with many versions is a few MB


@router.put("/sync/meetings/{meeting_id}", response_model=SyncResult)
async def import_meeting(meeting_id: str, actor: PrincipalDep, request: Request):
    """Copy a meeting from a local engine (ADR-015). Safe to repeat."""
    body = bytearray()
    async for chunk in request.stream():  # bounded here, before anything is parsed
        body += chunk
        if len(body) > MAX_SYNC_BYTES:
            raise HTTPException(status_code=413)
    try:
        package = SyncPackage.model_validate_json(bytes(body))
    except ValueError as error:
        raise ValidationFailed from error
    if package.meeting_id != meeting_id:
        raise ValidationFailed
    return await run_in_threadpool(
        request.app.state.container.sync_import.import_meeting,
        actor.workspace_id,
        actor.user_id,
        package,
    )
