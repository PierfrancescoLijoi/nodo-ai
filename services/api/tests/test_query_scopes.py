from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nodo_api.domain.models import Answerability
from nodo_api.domain.retrieval import (
    CitationFormat,
    QueryScope,
    QueryScopeMode,
    resolve_query_scope,
)
from nodo_api.domain.sources import Source, SourceKind
from nodo_api.errors import NotFoundError
from nodo_api.services.query import QueryResult

WS = "workspace-a"
OWNER = "user-a"


def document(source_id: str, owner: str = OWNER) -> Source:
    return Source(
        id=source_id,
        workspace_id=WS,
        owner_id=owner,
        kind=SourceKind.DOCUMENT,
        title="Fictional runbook",
        state="ready",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class CaptureQuery:
    def __init__(self) -> None:
        self.request = None

    def query(self, workspace_id, owner_id, request):
        self.request = request
        return QueryResult("", Answerability.UNANSWERABLE, (), (), "trace-scope")


def test_legacy_query_keeps_the_existing_scope_and_citation_format(
    app, client, auth_headers
) -> None:
    capture = CaptureQuery()
    app.state.container.query_service = capture

    response = client.post(
        "/v1/query", headers=auth_headers, json={"question": "What changed?", "ui_locale": "en"}
    )

    assert response.status_code == 200
    assert capture.request.scope.mode is QueryScopeMode.LEGACY
    assert capture.request.scope.source_ids is None
    assert capture.request.citation_format is CitationFormat.LEGACY


def test_selected_sources_are_authorized_before_the_request_reaches_query(
    app, client, auth_headers
) -> None:
    app.state.container.sources.add(WS, document("doc-scope"))
    capture = CaptureQuery()
    app.state.container.query_service = capture

    response = client.post(
        "/v1/query",
        headers=auth_headers,
        json={
            "question": "What changed?",
            "ui_locale": "en",
            "citation_format": "source",
            "scope": {"mode": "sources", "source_ids": ["doc-scope"]},
        },
    )

    assert response.status_code == 200
    assert capture.request.scope.mode is QueryScopeMode.SOURCES
    assert capture.request.scope.source_ids == frozenset({"doc-scope"})
    assert capture.request.citation_format is CitationFormat.SOURCE


def test_collection_scope_resolves_only_owned_live_members(app, client, auth_headers) -> None:
    source = app.state.container.sources.add(WS, document("doc-member"))
    collection = app.state.container.collections.create(WS, OWNER, name="Plans", parent_id=None)
    app.state.container.collections.add_source(WS, OWNER, collection.id, source.id)
    capture = CaptureQuery()
    app.state.container.query_service = capture

    response = client.post(
        "/v1/query",
        headers=auth_headers,
        json={
            "question": "What changed?",
            "ui_locale": "en",
            "citation_format": "source",
            "scope": {"mode": "collection", "collection_id": collection.id},
        },
    )

    assert response.status_code == 200
    assert capture.request.scope.source_ids == frozenset({source.id})


@pytest.mark.parametrize("mode", ["all", "sources_only"])
def test_all_source_scopes_do_not_silently_page_authorized_sources(
    app, client, auth_headers, mode
) -> None:
    capture = CaptureQuery()
    app.state.container.query_service = capture

    response = client.post(
        "/v1/query",
        headers=auth_headers,
        json={
            "question": "What changed?",
            "ui_locale": "en",
            "citation_format": "source",
            "scope": {"mode": mode},
        },
    )

    assert response.status_code == 200
    assert capture.request.scope.source_ids is None
    assert capture.request.scope.sources_only is (mode == "sources_only")


def test_nonlegacy_scope_requires_source_citations(client, auth_headers) -> None:
    response = client.post(
        "/v1/query",
        headers=auth_headers,
        json={
            "question": "What changed?",
            "ui_locale": "en",
            "scope": {"mode": "all"},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_document_scopes_are_explicitly_unavailable_without_local_ports(
    app, client, auth_headers
) -> None:
    sources, collections = app.state.container.sources, app.state.container.collections
    app.state.container.sources = None
    app.state.container.collections = None
    try:
        response = client.post(
            "/v1/query",
            headers=auth_headers,
            json={
                "question": "What changed?",
                "ui_locale": "en",
                "citation_format": "source",
                "scope": {"mode": "all"},
            },
        )
    finally:
        app.state.container.sources = sources
        app.state.container.collections = collections
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_available_on_engine"


def test_foreign_and_missing_source_ids_have_the_same_failure(app) -> None:
    app.state.container.sources.add(WS, document("foreign", owner="user-b"))
    outcomes = []
    for source_id in ("foreign", "absent"):
        with pytest.raises(NotFoundError) as caught:
            resolve_query_scope(
                WS,
                OWNER,
                QueryScope(QueryScopeMode.SOURCES, source_ids=(source_id,)),
                CitationFormat.SOURCE,
                app.state.container.sources,
                app.state.container.collections,
            )
        outcomes.append((caught.value.code, caught.value.public_message))
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize(
    "scope",
    [
        {"mode": "collection"},
        {"mode": "sources", "source_ids": []},
        {"mode": "all", "collection_id": "unexpected"},
        {"mode": "sources", "source_ids": ["same", "same"]},
    ],
)
def test_invalid_scope_shapes_are_rejected_before_query(client, auth_headers, scope) -> None:
    response = client.post(
        "/v1/query",
        headers=auth_headers,
        json={"question": "What changed?", "ui_locale": "en", "scope": scope},
    )
    assert response.status_code == 422


def test_explicit_source_scope_has_a_hard_cap(client, auth_headers) -> None:
    response = client.post(
        "/v1/query",
        headers=auth_headers,
        json={
            "question": "What changed?",
            "ui_locale": "en",
            "citation_format": "source",
            "scope": {
                "mode": "sources",
                "source_ids": [f"source-{index}" for index in range(101)],
            },
        },
    )
    assert response.status_code == 422
