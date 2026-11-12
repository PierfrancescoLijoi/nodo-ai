from __future__ import annotations

from dataclasses import replace

import pytest

from tests.source_contract import OWNER, fragment, section, source


def _add_document(app, source_id: str = "doc1", *, minutes: int = 20) -> None:
    container = app.state.container
    value = replace(
        source(source_id, owner=OWNER, minutes=minutes, ws="workspace-a"),
        title="Private handbook",
        origin="handbook.pdf",
    )
    container.sources.add("workspace-a", value)
    container.fragments.add_version(
        "workspace-a",
        source_id,
        (section("intro", source_id, 0),),
        (
            fragment("f1", source_id, 0, "First paragraph", "intro"),
            fragment("f2", source_id, 1, "Second paragraph", "intro"),
        ),
    )


def test_lists_meeting_as_source(client, auth_headers) -> None:
    response = client.get("/v1/sources", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    meeting = next(item for item in body["items"] if item["id"] == "seed-m1")
    assert meeting == {
        "id": "seed-m1",
        "kind": "meeting",
        "title": "Seed meeting 1",
        "state": "ready",
        "created_at": "2026-10-01T00:00:00Z",
        "origin": None,
        "language": None,
        "version": 1,
    }


def test_source_list_paginates(app, client, auth_headers) -> None:
    for number in range(3):
        _add_document(app, f"doc{number}", minutes=20 + number)

    first = client.get("/v1/sources?limit=2", headers=auth_headers).json()
    assert [item["id"] for item in first["items"]] == ["doc2", "doc1"]
    assert first["next_cursor"]

    second = client.get(
        "/v1/sources", headers=auth_headers, params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert [item["id"] for item in second["items"]] == ["doc0", "seed-m3"]


def test_get_source_of_other_user_is_404(app, client) -> None:
    _add_document(app)
    app.state.container.auth.add_dev_token("bob-token-0123456789", "user-b", "workspace-a")
    bob = {"Authorization": "Bearer bob-token-0123456789"}

    for path in ("/v1/sources/doc1", "/v1/sources/doc1/outline", "/v1/sources/doc1/fragments"):
        response = client.get(path, headers=bob)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


def test_outline_and_fragments_for_a_document(app, client, auth_headers) -> None:
    _add_document(app)

    detail = client.get("/v1/sources/doc1", headers=auth_headers)
    outline = client.get("/v1/sources/doc1/outline", headers=auth_headers)
    fragments = client.get("/v1/sources/doc1/fragments", headers=auth_headers)

    assert detail.status_code == outline.status_code == fragments.status_code == 200
    assert detail.json()["origin"] == "handbook.pdf"
    assert outline.json() == {
        "sections": [
            {"id": "intro", "parent_id": None, "ordinal": 0, "level": 1, "title": "heading intro"}
        ]
    }
    page = fragments.json()
    assert page["version"] == 1
    assert [item["id"] for item in page["fragments"]] == ["f1", "f2"]
    assert page["fragments"][0]["locator"] == {"chars": [0, 15], "page": 1}


def test_fragments_of_meeting_have_time_locators(client, auth_headers) -> None:
    response = client.get("/v1/sources/seed-m1/fragments", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    assert body["fragments"][0]["id"] == "seg-001"
    assert body["fragments"][0]["locator"] == {"time_ms": [1000, 1800]}


def test_kind_filter(app, client, auth_headers) -> None:
    _add_document(app)

    documents = client.get("/v1/sources?kind=document", headers=auth_headers)
    meetings = client.get("/v1/sources?kind=meeting", headers=auth_headers)

    assert [item["id"] for item in documents.json()["items"]] == ["doc1"]
    assert all(item["kind"] == "meeting" for item in meetings.json()["items"])
    assert client.get("/v1/sources?kind=invalid", headers=auth_headers).status_code == 422


@pytest.mark.parametrize(
    "path",
    (
        "/v1/sources",
        "/v1/sources/anything",
        "/v1/sources/anything/outline",
        "/v1/sources/anything/fragments",
    ),
)
def test_azure_engine_answers_501(app, client, auth_headers, path) -> None:
    app.state.container.sources = None
    app.state.container.fragments = None
    app.state.container.source_service = None

    response = client.get(path, headers=auth_headers)

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_available_on_engine"


def test_sources_require_authentication(client) -> None:
    assert client.get("/v1/sources").status_code == 401


def test_openapi_lists_source_paths(app) -> None:
    paths = app.openapi()["paths"]
    assert {path for path in paths if path.startswith("/v1/sources")} == {
        "/v1/sources",
        "/v1/sources/{source_id}",
        "/v1/sources/{source_id}/outline",
        "/v1/sources/{source_id}/fragments",
    }
