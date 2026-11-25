"""Local-engine routes (ADR-014), plus the source upload capability used by memory development.

Upload routes are not behind the bearer token: each link is the capability (random, one object,
one use, short life), exactly like an Azure blob SAS link. The rest needs the token."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from nodo_api.adapters.local.blob import UploadRejected
from nodo_api.adapters.local.model_catalog import CATALOG, HardwareTier, installed, remove
from nodo_api.adapters.local.models import data_dir
from nodo_api.api.v1.routes import PrincipalDep
from nodo_api.api.v1.schemas import FrozenModel
from nodo_api.domain.source_files import UploadRejected as SourceUploadRejected
from nodo_api.errors import NotAvailableOnEngine, ValidationFailed

router = APIRouter(prefix="/v1/local")
source_upload_router = APIRouter(prefix="/v1/local")


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


class LocalModelOutput(FrozenModel):
    id: str
    task: str
    name: str
    publisher: str
    version: str
    size_mb: int
    licence: str
    licence_url: str
    hardware: tuple[str, ...]
    sha256: str | None
    installable: bool
    installed: bool


class LocalModelsOutput(FrozenModel):
    hardware_tier: str
    models: tuple[LocalModelOutput, ...]


class LocalModelRemove(FrozenModel):
    approved: bool = False


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


@source_upload_router.put("/source-uploads/{token}", status_code=status.HTTP_204_NO_CONTENT)
async def source_upload(token: str, request: Request) -> Response:
    """The random one-use link is the credential; document bytes never need a bearer token."""
    blobs = request.app.state.container.source_blobs
    if blobs is None:
        raise NotAvailableOnEngine
    try:
        with blobs.open_upload(token) as target:
            async for chunk in request.stream():
                target.write(chunk)
    except SourceUploadRejected as error:
        if error.code == "too_large":
            raise HTTPException(status_code=413) from error
        if error.code == "low_disk":
            raise HTTPException(status_code=507) from error
        if error.code == "size_mismatch":  # a short body: the link stays usable for a new try
            raise HTTPException(status_code=400) from error
        raise HTTPException(status_code=403) from error  # unknown, used, expired or revoked
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


@router.get("/models", response_model=LocalModelsOutput)
def local_models(_: PrincipalDep, request: Request) -> LocalModelsOutput:
    """Catalog metadata only. Installation is deliberately a separate, confirmed operation."""
    settings = request.app.state.container.settings
    root = settings.local_models_dir or data_dir() / "models"
    # VRAM must not be guessed from the presence of CUDA; until a probe is added, CPU is safest.
    tier = HardwareTier.CPU
    return LocalModelsOutput(
        hardware_tier=tier.value,
        models=tuple(
            LocalModelOutput(
                id=entry.id,
                task=entry.task,
                name=entry.name,
                publisher=entry.publisher,
                version=entry.version,
                size_mb=entry.size_mb,
                licence=entry.licence,
                licence_url=entry.licence_url,
                hardware=tuple(item.value for item in entry.hardware),
                sha256=entry.sha256,
                installable=entry.installable,
                installed=installed(entry, root),
            )
            for entry in CATALOG
        ),
    )


@router.delete("/models/{model_id}", response_model=LocalModelOutput)
def remove_local_model(
    model_id: str, body: LocalModelRemove, _: PrincipalDep, request: Request
) -> LocalModelOutput:
    if not body.approved:
        raise ValidationFailed("Model removal requires explicit approval")
    entry = next((item for item in CATALOG if item.id == model_id), None)
    if entry is None or not entry.installable:
        raise NotAvailableOnEngine
    root = request.app.state.container.settings.local_models_dir or data_dir() / "models"
    remove(entry, root)
    return LocalModelOutput(
        id=entry.id, task=entry.task, name=entry.name, publisher=entry.publisher,
        version=entry.version, size_mb=entry.size_mb, licence=entry.licence,
        licence_url=entry.licence_url, hardware=tuple(item.value for item in entry.hardware),
        sha256=entry.sha256, installable=entry.installable, installed=False,
    )


WEB_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Permissions-Policy", "microphone=(self), camera=(), geolocation=(), payment=()"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)
IMMUTABLE_ASSETS = "_next/static/"
POLICY_FILE = "csp.json"


def load_policy(directory: Path) -> str | None:
    """The content security policy the web build sealed next to its files.

    apps/web/scripts/seal-csp.mjs writes it; a hand-made folder has none and goes without."""
    try:
        return str(
            json.loads((directory / POLICY_FILE).read_text(encoding="utf-8"))["policy"]["local"]
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


class SinglePageFiles(StaticFiles):
    """Static files where an unknown path without an extension is a route of the app.

    Sent with the headers that keep a page a page: a content policy that allows only its own
    scripts, no framing, no referrer, no sniffing. Hashed build files are cached for good, pages
    never."""

    def __init__(self, *args, policy: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._policy = policy

    async def get_response(self, path, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code == 404 and "." not in path.rsplit("/", 1)[-1]:
                response = await super().get_response("index.html", scope)
            else:
                raise
        for name, value in WEB_HEADERS:
            response.headers[name] = value
        if self._policy:
            response.headers["Content-Security-Policy"] = self._policy
        immutable = path.replace("\\", "/").startswith(IMMUTABLE_ASSETS)  # Windows: backslashes
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if immutable else "no-cache"
        )
        return response


def mount_web(app, directory: Path) -> None:
    """Serve the exported web app from the engine. Mount last: API routes match first."""
    files = SinglePageFiles(directory=directory, html=True, policy=load_policy(directory))
    app.mount("/", files, name="web")
