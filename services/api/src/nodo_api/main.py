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

from nodo_api.api.v1 import router
from nodo_api.auth import AuthManager
from nodo_api.bootstrap import build_adapters
from nodo_api.config import Settings
from nodo_api.errors import NodoError
from nodo_api.services import EvidenceValidator, GraphService, MeetingService, QueryService

logger = logging.getLogger("nodo_api")

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
