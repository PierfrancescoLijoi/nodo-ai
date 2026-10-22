from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from nodo_api.adapters.memory import (
    MemoryAuditLog,
    MemoryBlobStore,
    MemoryGraphStore,
    MemoryJobQueue,
    MemoryMeetingRepository,
    MemoryTranscriptRepository,
    MockLlm,
    MockRetriever,
    seed_memory_adapters,
)
from nodo_api.api.v1 import router
from nodo_api.auth import AuthManager
from nodo_api.config import Settings
from nodo_api.errors import NodoError
from nodo_api.services import EvidenceValidator, MeetingService, QueryService

logger = logging.getLogger("nodo_api")

_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{8,64}")


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    auth: AuthManager
    meetings: MemoryMeetingRepository
    transcripts: MemoryTranscriptRepository
    blobs: MemoryBlobStore
    jobs: MemoryJobQueue
    retriever: MockRetriever
    graph: MemoryGraphStore
    llm: MockLlm
    audit: MemoryAuditLog
    meeting_service: MeetingService
    query_service: QueryService


def create_app(
    settings: Settings | None = None,
    oidc_key_provider: Callable[[], dict[str, Any]] | None = None,
) -> FastAPI:
    effective_settings = settings or Settings()
    app = FastAPI(title="NODO AI API", version="0.1.0")
    app.state.container = _container(effective_settings, oidc_key_provider)
    app.include_router(router)
    _configure_cors(app, effective_settings)
    _configure_middleware(app)
    _configure_errors(app)

    @app.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readiness() -> dict[str, str]:
        return {"status": "ready"}

    return app


def _container(settings: Settings, key_provider) -> AppContainer:
    meetings = MemoryMeetingRepository()
    transcripts = MemoryTranscriptRepository()
    blobs = MemoryBlobStore()
    jobs = MemoryJobQueue()
    retriever = MockRetriever()
    graph = MemoryGraphStore()
    llm = MockLlm()
    audit = MemoryAuditLog()
    if settings.seed_demo_data:
        seed_memory_adapters(meetings, transcripts, retriever, graph)
    meeting_service = MeetingService(
        meetings,
        transcripts,
        blobs,
        jobs,
        retriever,
        graph,
        audit,
        settings.attestation_hmac_key,
        settings.known_notice_versions,
        settings.known_retention_policies,
    )
    query_service = QueryService(
        meetings,
        retriever,
        graph,
        llm,
        EvidenceValidator(transcripts),
        settings.graph_node_limit,
    )
    return AppContainer(
        settings,
        AuthManager(settings, key_provider),
        meetings,
        transcripts,
        blobs,
        jobs,
        retriever,
        graph,
        llm,
        audit,
        meeting_service,
        query_service,
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
        return _error_response(request, exc.status_code, exc.code, exc.public_message)

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
