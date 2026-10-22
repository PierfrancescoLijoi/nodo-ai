from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from nodo_api.api.v1 import router
from nodo_api.auth import AuthManager
from nodo_api.bootstrap import build_adapters
from nodo_api.config import Settings
from nodo_api.errors import NodoError
from nodo_api.jwks import JwksCache
from nodo_api.middleware import EdgeProtection
from nodo_api.resilience import Bulkhead, DependencyUnavailable, is_transient
from nodo_api.services import EvidenceValidator, GraphService, MeetingService, QueryService
from nodo_api.services.exports import ExportService
from nodo_api.services.sync_import import SyncImportService
from nodo_api.telemetry import configure, configure_logging, instrument_app, request_id_var

logger = logging.getLogger("nodo_api")

API_VERSION = "0.1.0"
SYNC_BODY_LIMIT = 40 * 1024 * 1024
LOCAL_UPLOAD_LIMIT = 2 * 1024**3

_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{8,64}")


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    auth: AuthManager
    meetings: Any
    transcripts: Any
    blobs: Any
    jobs: Any
    retriever: Any
    graph: Any
    llm: Any
    audit: Any
    meeting_service: MeetingService
    query_service: QueryService
    graph_service: GraphService
    sync_import: SyncImportService
    export_service: ExportService
    runtime: Any = None
    readiness: tuple[tuple[str, Callable[[], None]], ...] = ()
    query_gate: Bulkhead | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    runtime = app.state.container.runtime
    if runtime is not None:
        runtime.start()
    try:
        yield
    finally:
        if runtime is not None:
            runtime.stop()


def create_app(
    settings: Settings | None = None,
    oidc_key_provider: Callable[[], dict[str, Any]] | None = None,
) -> FastAPI:
    effective_settings = settings or Settings()
    if oidc_key_provider is None and effective_settings.auth_mode == "oidc":
        oidc_key_provider = JwksCache(
            effective_settings.oidc_issuer or "", effective_settings.oidc_jwks_url
        )
    if effective_settings.backend == "azure":
        configure_logging()
        configure(
            role=effective_settings.service,
            environment=os.environ.get("NODO_AZURE_ENVIRONMENT", "prod"),
            version=API_VERSION,
            connection_string=os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"),
            sampling_ratio=effective_settings.telemetry_sampling_ratio,
        )
    app = FastAPI(title="NODO AI API", version=API_VERSION, lifespan=_lifespan)
    instrument_app(app)
    app.state.container = _container(effective_settings, oidc_key_provider)
    app.include_router(router)
    _configure_cors(app, effective_settings)
    _configure_middleware(app)
    _configure_errors(app)
    app.add_middleware(
        EdgeProtection,
        max_in_flight=effective_settings.max_in_flight_requests,
        body_limits=(
            ("/v1/sync/", SYNC_BODY_LIMIT),
            ("/v1/local/uploads/", LOCAL_UPLOAD_LIMIT),
        ),
        # The local engine also serves the web app, which needs scripts and styles.
        security_headers=effective_settings.backend != "local",
    )

    @app.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readiness():
        container = app.state.container
        failed = []
        for name, check in container.readiness:
            try:
                await run_in_threadpool(check)
            except Exception as error:
                logger.warning("not_ready check=%s type=%s", name, type(error).__name__)
                failed.append(name)
        if failed:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "failed": failed},
                headers={"Retry-After": "5"},
            )
        return {"status": "ready"}

    if effective_settings.backend == "local":
        from nodo_api.api import local

        _only_loopback_hosts(app, effective_settings)

        app.include_router(local.router)
        web = effective_settings.local_web_dir
        if web is not None and web.is_dir():
            local.mount_web(app, web)  # last: it matches everything the API did not

    return app


def _only_loopback_hosts(app: FastAPI, settings: Settings) -> None:
    """Refuse requests whose Host header is not this computer's loopback address. A web page on the
    internet can make a browser send requests to 127.0.0.1 under its own name (DNS rebinding); the
    secret already stops it, and this closes the door a second time."""
    port = settings.local_public_url.rsplit(":", 1)[1]
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}", "testserver"}

    @app.middleware("http")
    async def host_check(request: Request, call_next):
        if request.headers.get("host", "") not in allowed:
            return JSONResponse(status_code=421, content={"error": {"code": "misdirected"}})
        return await call_next(request)


def _container(settings: Settings, key_provider) -> AppContainer:
    a = build_adapters(settings)
    meeting_service = MeetingService(
        a.meetings,
        a.transcripts,
        a.blobs,
        a.jobs,
        a.retriever,
        a.graph,
        a.audit,
        settings.attestation_hmac_key,
        settings.known_notice_versions,
        settings.known_retention_policies,
    )
    query_service = QueryService(
        a.meetings,
        a.retriever,
        a.graph,
        a.llm,
        EvidenceValidator(a.transcripts),
        settings.graph_node_limit,
    )
    return AppContainer(
        settings,
        AuthManager(settings, key_provider),
        a.meetings,
        a.transcripts,
        a.blobs,
        a.jobs,
        a.retriever,
        a.graph,
        a.llm,
        a.audit,
        meeting_service,
        query_service,
        GraphService(a.graph),
        SyncImportService(a.meetings, a.transcripts, a.retriever, a.graph, meeting_service),
        ExportService(a.exports, a.meetings, a.transcripts, a.graph, a.blobs, a.jobs, a.audit),
        a.runtime,
        a.readiness,
        Bulkhead("query", settings.max_concurrent_queries, wait_seconds=0.0),
    )


def _configure_cors(app: FastAPI, settings: Settings) -> None:
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        )


def _configure_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def correlation_id(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        # Only well-formed ids are echoed; anything else is replaced to keep logs clean.
        request_id = supplied if _REQUEST_ID.fullmatch(supplied) else str(uuid4())
        request.state.request_id = request_id
        request_id_var.set(request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "request_id": request_id,
                }
            )
        )
        return response


def _configure_errors(app: FastAPI) -> None:
    @app.exception_handler(NodoError)
    async def nodo_error(request: Request, exc: NodoError):
        response = _error_response(request, exc.status_code, exc.code, exc.public_message)
        retry_after = getattr(exc, "retry_after", None)
        if retry_after:
            response.headers["Retry-After"] = str(retry_after)  # clients and the gateway back off
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return _error_response(request, 422, "validation_error", "Request validation failed")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = "not_found" if exc.status_code == 404 else "http_error"
        message = "Resource not found" if exc.status_code == 404 else "HTTP request failed"
        return _error_response(request, exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        if is_transient(exc):  # a dependency hiccup that was not wrapped: say so, do not blame us
            logger.warning("transient_dependency_error type=%s", type(exc).__name__)
            response = _error_response(
                request, 503, "dependency_unavailable", DependencyUnavailable.public_message
            )
            response.headers["Retry-After"] = "5"
            return response
        logger.error(
            "unhandled_request_error",
            extra={
                "request_id": getattr(request.state, "request_id", "unknown"),
                "error_type": type(exc).__name__,
            },
        )
        return _error_response(request, 500, "internal_error", "Internal server error")


def _error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
    )
