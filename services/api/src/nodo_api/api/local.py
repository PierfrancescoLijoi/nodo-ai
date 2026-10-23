"""Routes that exist only in the local backend (ADR-014): the audio upload link and engine status.

The upload route is not behind the bearer token: the link itself is the capability (random, one
meeting, one use, short life), exactly like an Azure blob SAS link. The rest needs the token."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from nodo_api.adapters.local.blob import UploadRejected
from nodo_api.api.v1.routes import PrincipalDep
from nodo_api.api.v1.schemas import FrozenModel
from nodo_api.errors import ValidationFailed

router = APIRouter(prefix="/v1/local")


class SyncStart(FrozenModel):
    cloud_url: str = Field(min_length=8, max_length=300)
    access_token: str = Field(min_length=20, max_length=8_000, repr=False)
    force: bool = False  # send every meeting again, e.g. after connecting a different account


class SyncProgress(FrozenModel):
    state: str
    total: int
    done: int
    pushed: int
    unchanged: int
    deleted: int
    failed: tuple[dict[str, str], ...]
    message: str


def _progress(report) -> SyncProgress:
    return SyncProgress(**{**report.__dict__, "failed": tuple(report.failed)})


class IndexStart(FrozenModel):
    mode: Literal["verify", "repair"]


class IndexProgressOut(FrozenModel):
    state: str
    mode: str
    checked: int
    skipped: int
    repaired: int
    findings: tuple[dict[str, str], ...]
    failed: tuple[dict[str, str], ...]
    message: str


def _index_progress(progress) -> IndexProgressOut:
    return IndexProgressOut(
        **{
            **progress.__dict__,
            "findings": tuple(progress.findings),
            "failed": tuple(progress.failed),
        }
    )


class LocalStatus(FrozenModel):
    backend: str
    missing: tuple[str, ...]
    pending_jobs: int
    gpu: bool


@router.put("/uploads/{token}", status_code=status.HTTP_204_NO_CONTENT)
async def upload(token: str, request: Request) -> Response:
    blobs = request.app.state.container.runtime.blobs
    try:
        with blobs.open_upload(token) as target:
            async for chunk in request.stream():
                target.write(chunk)
    except UploadRejected as error:
        raise HTTPException(status_code=403) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sync", response_model=SyncProgress, status_code=status.HTTP_202_ACCEPTED)
def start_sync(body: SyncStart, _: PrincipalDep, request: Request) -> SyncProgress:
    """Copy the archive to Azure (ADR-015). The token is used for this run only and not stored."""
    sync = request.app.state.container.runtime.sync
    try:
        sync.start(body.cloud_url, body.access_token, body.force)
    except ValueError as error:
        raise ValidationFailed from error
    return _progress(sync.report())


@router.get("/sync", response_model=SyncProgress)
def sync_progress(_: PrincipalDep, request: Request) -> SyncProgress:
    return _progress(request.app.state.container.runtime.sync.report())


@router.post("/index", response_model=IndexProgressOut, status_code=status.HTTP_202_ACCEPTED)
def start_index_run(body: IndexStart, _: PrincipalDep, request: Request) -> IndexProgressOut:
    """Compare the search index and the graph with the archive (ADR-021). `verify` reports and
    changes nothing; `repair` rebuilds what is missing or stale. Runs in the background."""
    index = request.app.state.container.runtime.index
    index.start(body.mode)
    return _index_progress(index.progress())


@router.get("/index", response_model=IndexProgressOut)
def index_progress(_: PrincipalDep, request: Request) -> IndexProgressOut:
    return _index_progress(request.app.state.container.runtime.index.progress())


@router.get("/status", response_model=LocalStatus)
def local_status(_: PrincipalDep, request: Request) -> LocalStatus:
    runtime = request.app.state.container.runtime
    gpu = False
    try:
        import ctranslate2

        gpu = ctranslate2.get_cuda_device_count() > 0
    except ImportError:
        pass
    return LocalStatus(
        backend="local",
        missing=tuple(runtime.models.missing()),
        pending_jobs=runtime.queue.pending(),
        gpu=gpu,
    )


class SinglePageFiles(StaticFiles):
    """Static files where an unknown path without an extension is a route of the app."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code == 404 and "." not in path.rsplit("/", 1)[-1]:
                return await super().get_response("index.html", scope)
            raise


def mount_web(app, directory: Path) -> None:
    """Serve the exported web app from the engine. Mount last: API routes match first."""
    app.mount("/", SinglePageFiles(directory=directory, html=True), name="web")
