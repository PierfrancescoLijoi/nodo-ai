from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Annotated, Literal

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
    concept_output,
    decode_cursor,
    edge_output,
    encode_cursor,
    fragment_output,
    hierarchy_link_output,
    import_output,
    list_item,
    meeting_output,
    section_output,
    source_output,
    source_version_output,
    subgraph_output,
)
from nodo_api.api.v1.schemas import (
    ChangeOutput,
    ChangesOutput,
    ConceptHierarchyOutput,
    ExportInput,
    ExportOutput,
    GraphEdgeOutput,
    HierarchyLinkOutput,
    HierarchyProposalList,
    HierarchyReviewInput,
    ImportStatusOutput,
    MeetingCreate,
    MeetingList,
    MeetingOutput,
    ProcessingConfirmation,
    QueryFiltersInput,
    QueryInput,
    QueryOutput,
    ReviewInput,
    ReviewOutput,
    ReviewRelationInput,
    SourceCreate,
    SourceCreateOutput,
    SourceFragments,
    SourceList,
    SourceMentionOutput,
    SourceOutline,
    SourceOutput,
    SourceVersionList,
    SpeakerNameInput,
    SubgraphOutput,
    TranscriptPage,
    UploadSessionOutput,
    VoiceQueryInput,
    WatchFolderCreate,
    WatchFolderList,
    WatchFolderOutput,
)
from nodo_api.api.v1.sync_schemas import SyncPackage, SyncResult
from nodo_api.auth import AuthError, Principal
from nodo_api.domain.graph import ReviewStatus
from nodo_api.domain.hierarchy import MAX_DEPTH
from nodo_api.domain.source_files import UploadRejected
from nodo_api.domain.sources import SourceKind
from nodo_api.errors import (
    AuthenticationError,
    NotAvailableOnEngine,
    NotFoundError,
    ValidationFailed,
)
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


def _source_stores(container):
    if container.sources is None or container.fragments is None:
        raise NotAvailableOnEngine
    return container.sources, container.fragments


def _source_import(container):
    if container.source_import is None:
        raise NotAvailableOnEngine
    return container.source_import


def _source_delete(container):
    _source_import(container)
    if container.source_service is None:
        raise NotAvailableOnEngine
    return container.source_service


@router.post("/sources", response_model=SourceCreateOutput, status_code=status.HTTP_201_CREATED)
def create_source(
    payload: SourceCreate,
    actor: PrincipalDep,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
    ],
):
    container = request.app.state.container
    service = _source_import(container)
    try:
        source, session = service.create(
            actor.workspace_id,
            actor.user_id,
            filename=payload.filename,
            size_bytes=payload.size_bytes,
            kind=payload.kind,
            origin_note=payload.origin_note,
            idempotency_key=idempotency_key,
        )
    except UploadRejected as error:
        if error.code == "too_large":
            raise HTTPException(status_code=413) from error
        if error.code == "low_disk":
            raise HTTPException(status_code=507) from error
        raise HTTPException(status_code=409) from error
    return SourceCreateOutput(
        source=source_output(source),
        upload_url=f"/v1/local/source-uploads/{session.token}",
        expires_in_seconds=container.settings.upload_session_seconds,
    )


@router.get("/sources", response_model=SourceList)
def list_sources(
    actor: PrincipalDep,
    request: Request,
    kind: SourceKind | None = None,
    state: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
):
    sources, _ = _source_stores(request.app.state.container)
    kinds = None if kind is None else frozenset({kind})
    page = sources.list_owned(
        actor.workspace_id,
        actor.user_id,
        limit + 1,
        decode_cursor(cursor),
        kinds,
        state,
        q,
    )
    items, has_more = page[:limit], len(page) > limit
    return SourceList(
        items=tuple(source_output(item) for item in items),
        next_cursor=encode_cursor(items[-1]) if has_more and items else None,
    )


def _folder_watcher(container):
    if getattr(container, "folder_watcher", None) is None:
        raise NotAvailableOnEngine
    return container.folder_watcher


def _watch_folder_output(folder) -> WatchFolderOutput:
    return WatchFolderOutput(
        id=folder.id, path=folder.path, enabled=folder.enabled, created_at=folder.created_at
    )


# Before /sources/{source_id}: these paths would otherwise be read as a source id.
@router.get("/sources/watch-folders", response_model=WatchFolderList)
def list_watch_folders(actor: PrincipalDep, request: Request):
    watcher = _folder_watcher(request.app.state.container)
    folders = watcher.list_folders(actor.workspace_id, actor.user_id)
    return WatchFolderList(items=tuple(_watch_folder_output(f) for f in folders))


@router.post(
    "/sources/watch-folders",
    response_model=WatchFolderOutput,
    status_code=status.HTTP_201_CREATED,
    description="Watch a folder on this computer: supported files in it (down to five folder "
    "levels) are imported once they stop changing, a changed file becomes a new version, and "
    "recordings wait for a processing confirmation. 422 for a relative path, a missing folder, a "
    "network share, a link or a folder that overlaps the NODO data folder; 409 when it is "
    "watched already.",
)
def add_watch_folder(payload: WatchFolderCreate, actor: PrincipalDep, request: Request):
    watcher = _folder_watcher(request.app.state.container)
    folder = watcher.add_folder(actor.workspace_id, actor.user_id, payload.path)
    return _watch_folder_output(folder)


@router.delete(
    "/sources/watch-folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    description="Stop watching. Sources already imported from the folder stay.",
)
def delete_watch_folder(folder_id: str, actor: PrincipalDep, request: Request) -> Response:
    watcher = _folder_watcher(request.app.state.container)
    watcher.delete_folder(actor.workspace_id, actor.user_id, folder_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


SLOW_ON_THIS_MACHINE = "slow_on_this_machine"


def _import_status(job, container, *, requested_source_id: str | None = None) -> ImportStatusOutput:
    unchanged = requested_source_id is not None and job.source_id != requested_source_id
    settings = container.settings
    slow = settings.backend == "local" and settings.local_import_slow_notice
    return ImportStatusOutput(
        **{
            "import": import_output(
                job,
                unchanged=unchanged,
                existing_source_id=job.source_id if unchanged else None,
                warnings=(SLOW_ON_THIS_MACHINE,) if slow else (),
            )
        }
    )


@router.post(
    "/sources/{source_id}/complete",
    response_model=ImportStatusOutput,
    status_code=status.HTTP_202_ACCEPTED,
)
def complete_source(source_id: str, actor: PrincipalDep, request: Request):
    container = request.app.state.container
    job = _source_import(container).complete(actor.workspace_id, actor.user_id, source_id)
    return _import_status(job, container, requested_source_id=source_id)


@router.get("/sources/{source_id}/import", response_model=ImportStatusOutput)
def source_import_status(source_id: str, actor: PrincipalDep, request: Request):
    container = request.app.state.container
    _source_import(container)
    job = container.imports.get_owned(actor.workspace_id, actor.user_id, source_id)
    return _import_status(job, container)


@router.post(
    "/sources/{source_id}/cancel",
    response_model=ImportStatusOutput,
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_source_import(source_id: str, actor: PrincipalDep, request: Request):
    container = request.app.state.container
    job = _source_import(container).cancel(actor.workspace_id, actor.user_id, source_id)
    return _import_status(job, container)


@router.post(
    "/sources/{source_id}/retry",
    response_model=ImportStatusOutput,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_source_import(source_id: str, actor: PrincipalDep, request: Request):
    container = request.app.state.container
    job = _source_import(container).retry(actor.workspace_id, actor.user_id, source_id)
    return _import_status(job, container)


@router.post(
    "/sources/{source_id}/confirm-processing",
    response_model=ImportStatusOutput,
    status_code=status.HTTP_202_ACCEPTED,
    description="Imported audio or video waits in awaiting_confirmation until its owner confirms "
    '"I may process this recording". The confirmation is audited (kind only) and starts the '
    "transcription. A second call answers the current state; a cancelled, failed or deleted "
    "import, or a document, is refused. 409 original_missing once the original was erased.",
)
def confirm_source_processing(
    source_id: str, payload: ProcessingConfirmation, actor: PrincipalDep, request: Request
):
    container = request.app.state.container
    job = _source_import(container).confirm(actor.workspace_id, actor.user_id, source_id)
    return _import_status(job, container)


@router.get("/sources/{source_id}/versions", response_model=SourceVersionList)
def source_versions(source_id: str, actor: PrincipalDep, request: Request):
    container = request.app.state.container
    _source_import(container)
    source = container.sources.get_owned(actor.workspace_id, actor.user_id, source_id)
    if source.kind is SourceKind.MEETING:
        raise NotFoundError  # like complete, import, cancel and retry: not an imported source
    versions = container.fragments.versions(actor.workspace_id, actor.user_id, source_id)
    return SourceVersionList(items=tuple(source_version_output(item) for item in versions))


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: str, actor: PrincipalDep, request: Request) -> Response:
    _source_delete(request.app.state.container).delete(actor.workspace_id, actor.user_id, source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sources/{source_id}", response_model=SourceOutput)
def get_source(source_id: str, actor: PrincipalDep, request: Request):
    sources, _ = _source_stores(request.app.state.container)
    source = sources.get_owned(actor.workspace_id, actor.user_id, source_id)
    return source_output(source)


@router.get("/sources/{source_id}/outline", response_model=SourceOutline)
def get_source_outline(source_id: str, actor: PrincipalDep, request: Request):
    sources, fragments = _source_stores(request.app.state.container)
    sources.get_owned(actor.workspace_id, actor.user_id, source_id)
    return SourceOutline(
        sections=tuple(
            section_output(item) for item in fragments.outline(actor.workspace_id, source_id)
        )
    )


@router.get("/sources/{source_id}/fragments", response_model=SourceFragments)
def get_source_fragments(source_id: str, actor: PrincipalDep, request: Request):
    sources, fragments = _source_stores(request.app.state.container)
    source = sources.get_owned(actor.workspace_id, actor.user_id, source_id)
    latest = fragments.latest(actor.workspace_id, source_id)
    # A meeting is versioned by its transcript, not by the sources row.
    return SourceFragments(
        version=latest[0].version if latest else source.version,
        fragments=tuple(fragment_output(item) for item in latest),
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
        meetings_only=True,  # /v1 is the frozen clients' view
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
        actor.workspace_id, actor.user_id, limit, meetings_only=True
    )
    return subgraph_output(graph, 1)


@router.patch("/graph/relations/{relation_id}", response_model=GraphEdgeOutput)
def review_relation(
    relation_id: str, payload: ReviewRelationInput, actor: PrincipalDep, request: Request
):
    relation = request.app.state.container.graph_service.review(
        actor.workspace_id,
        actor.user_id,
        relation_id,
        ReviewStatus(payload.status),
        meetings_only=True,  # a relation /v1 never shows answers 404, before any write
    )
    return edge_output(relation)


@router.get("/graph/changes", response_model=ChangesOutput)
def graph_changes(
    actor: PrincipalDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
):
    changes = request.app.state.container.graph_service.changes(
        actor.workspace_id, actor.user_id, limit, meetings_only=True
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


# --- concept hierarchy (ADR-030); local and memory engines, 501 on Azure --------------------------


def _hierarchy(container):
    if getattr(container, "hierarchy", None) is None:
        raise NotAvailableOnEngine
    return container.hierarchy


@router.get("/concepts/{concept_id}/hierarchy", response_model=ConceptHierarchyOutput)
def concept_hierarchy(
    concept_id: Annotated[str, Path(max_length=64)],
    actor: PrincipalDep,
    request: Request,
    direction: Literal["parents", "children", "both"] = "both",
    depth: Annotated[int, Query(ge=1, le=MAX_DEPTH)] = 1,
    include_proposed: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
):
    service = _hierarchy(request.app.state.container)
    found = service.concept(
        actor.workspace_id,
        actor.user_id,
        concept_id,
        direction=direction,
        depth=depth,
        include_proposed=include_proposed,
        limit=limit,
    )
    locate = partial(service.locate, actor.workspace_id)
    return ConceptHierarchyOutput(
        concept=concept_output(found.concept),
        mentioned_in=tuple(
            SourceMentionOutput(source_id=source_id, title=title)
            for source_id, title in found.mentioned_in
        ),
        nodes=tuple(concept_output(node) for node in found.graph.nodes),
        links=tuple(hierarchy_link_output(edge, locate) for edge in found.graph.edges),
        truncated=found.graph.truncated,
    )


@router.get("/hierarchy/proposals", response_model=HierarchyProposalList)
def hierarchy_proposals(
    actor: PrincipalDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """Parent links the model or a document proposed that nobody has decided yet."""
    service = _hierarchy(request.app.state.container)
    graph = service.proposals(actor.workspace_id, actor.user_id, limit)
    locate = partial(service.locate, actor.workspace_id)
    return HierarchyProposalList(
        items=tuple(hierarchy_link_output(edge, locate) for edge in graph.edges),
        nodes=tuple(concept_output(node) for node in graph.nodes),
        truncated=graph.truncated,
    )


@router.patch("/hierarchy/links/{relation_id}", response_model=HierarchyLinkOutput)
def review_hierarchy_link(
    relation_id: Annotated[str, Path(max_length=64)],
    payload: HierarchyReviewInput,
    actor: PrincipalDep,
    request: Request,
):
    service = _hierarchy(request.app.state.container)
    relation = service.review(
        actor.workspace_id, actor.user_id, relation_id, ReviewStatus(payload.status)
    )
    return hierarchy_link_output(relation, partial(service.locate, actor.workspace_id))
