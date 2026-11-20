"""The collections API (FR-4, FR-8): owner-made nested folders of sources, their memberships, and
the retention date every source output carries. Memory engine; names are invented."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from nodo_api.domain.sources import SourceKind
from tests.source_contract import OWNER, source

WS = "workspace-a"
OTHERS = (
    ("bob-token-0123456789", "user-b", "workspace-a"),
    ("carol-token-0123456789", "user-a", "workspace-b"),  # the same user id elsewhere
)


def add_source(app, source_id: str = "doc1", **extra) -> None:
    app.state.container.sources.add(WS, source(source_id, owner=OWNER, ws=WS, minutes=30, **extra))


def create(client, headers, name: str, parent_id: str | None = None):
    response = client.post(
        "/v1/collections", headers=headers, json={"name": name, "parent_id": parent_id}
    )
    assert response.status_code == 201, response.text
    return response.json()


def listed(client, headers, query: str = ""):
    response = client.get(f"/v1/collections{query}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def members(client, headers, collection_id: str):
    response = client.get(f"/v1/collections/{collection_id}/sources", headers=headers)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def put(client, headers, collection_id: str, source_id: str):
    return client.put(f"/v1/collections/{collection_id}/sources/{source_id}", headers=headers)


def test_create_nested_then_list_shows_the_tree(client, auth_headers) -> None:
    work = create(client, auth_headers, "Work")
    plans = create(client, auth_headers, "Plans", work["id"])
    archive = create(client, auth_headers, "Archive")
    assert work["parent_id"] is None
    assert work["source_count"] == 0
    assert set(work) == {"id", "parent_id", "name", "created_at", "source_count"}
    items = listed(client, auth_headers)
    assert [(i["id"], i["parent_id"], i["name"]) for i in items] == [
        (archive["id"], None, "Archive"),
        (work["id"], None, "Work"),
        (plans["id"], work["id"], "Plans"),
    ]


def test_rename_and_move_and_move_to_root(client, auth_headers) -> None:
    work = create(client, auth_headers, "Work")
    plans = create(client, auth_headers, "Plans")
    path = f"/v1/collections/{plans['id']}"

    renamed = client.patch(path, headers=auth_headers, json={"name": "Roadmaps"})
    assert renamed.status_code == 200
    assert (renamed.json()["name"], renamed.json()["parent_id"]) == ("Roadmaps", None)

    moved = client.patch(path, headers=auth_headers, json={"parent_id": work["id"]})
    assert (moved.json()["name"], moved.json()["parent_id"]) == ("Roadmaps", work["id"])

    untouched = client.patch(path, headers=auth_headers, json={"name": "Plans"})
    assert untouched.json()["parent_id"] == work["id"]  # absent parent_id leaves it

    root = client.patch(path, headers=auth_headers, json={"parent_id": None})
    assert root.status_code == 200
    assert (root.json()["name"], root.json()["parent_id"]) == ("Plans", None)


def test_move_into_own_child_is_409_would_create_cycle(client, auth_headers) -> None:
    top = create(client, auth_headers, "Top")
    child = create(client, auth_headers, "Child", top["id"])
    for parent in (child["id"], top["id"]):
        response = client.patch(
            f"/v1/collections/{top['id']}", headers=auth_headers, json={"parent_id": parent}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "would_create_cycle"


def test_seven_levels_then_too_deep_is_409(client, auth_headers) -> None:
    parent = None
    for level in range(7):
        parent = create(client, auth_headers, f"Level {level}", parent)["id"]
    response = client.post(
        "/v1/collections", headers=auth_headers, json={"name": "Level 7", "parent_id": parent}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "too_deep"


def test_duplicate_sibling_name_is_409(client, auth_headers) -> None:
    create(client, auth_headers, "Café")
    response = client.post("/v1/collections", headers=auth_headers, json={"name": " cafe "})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_name"
    other = create(client, auth_headers, "Other")
    renamed = client.patch(
        f"/v1/collections/{other['id']}", headers=auth_headers, json={"name": "CAFÉ"}
    )
    assert renamed.status_code == 409
    assert renamed.json()["error"]["code"] == "duplicate_name"


def test_too_many_collections_is_409(app, client, auth_headers) -> None:
    app.state.container.collections._max = 1
    create(client, auth_headers, "Only")
    response = client.post("/v1/collections", headers=auth_headers, json={"name": "Second"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "too_many_collections"


def test_delete_collection_keeps_its_sources_listed_in_the_library(
    app, client, auth_headers
) -> None:
    add_source(app)
    top = create(client, auth_headers, "Top")
    child = create(client, auth_headers, "Child", top["id"])
    assert put(client, auth_headers, child["id"], "doc1").status_code == 204
    response = client.delete(f"/v1/collections/{top['id']}", headers=auth_headers)
    assert response.status_code == 204
    assert listed(client, auth_headers) == []
    library = client.get("/v1/sources", headers=auth_headers).json()["items"]
    assert "doc1" in {item["id"] for item in library}
    assert client.get("/v1/sources/doc1", headers=auth_headers).status_code == 200
    gone = client.get(f"/v1/collections/{child['id']}/sources", headers=auth_headers)
    assert gone.status_code == 404


def test_add_and_remove_a_source_and_list_collection_sources(app, client, auth_headers) -> None:
    add_source(app, "doc1")
    add_source(app, "doc2", kind=SourceKind.NOTE)
    box = create(client, auth_headers, "Box")
    for sid in ("doc1", "doc2", "doc1"):  # the second add of doc1 changes nothing
        assert put(client, auth_headers, box["id"], sid).status_code == 204
    assert sorted(members(client, auth_headers, box["id"])) == ["doc1", "doc2"]
    assert listed(client, auth_headers)[0]["source_count"] == 2

    page = client.get(f"/v1/collections/{box['id']}/sources?limit=1", headers=auth_headers).json()
    assert len(page["items"]) == 1
    assert page["next_cursor"]
    rest = client.get(
        f"/v1/collections/{box['id']}/sources",
        headers=auth_headers,
        params={"limit": 1, "cursor": page["next_cursor"]},
    ).json()
    assert {page["items"][0]["id"], rest["items"][0]["id"]} == {"doc1", "doc2"}
    assert rest["next_cursor"] is None

    path = f"/v1/collections/{box['id']}/sources/doc1"
    assert client.delete(path, headers=auth_headers).status_code == 204
    assert client.delete(path, headers=auth_headers).status_code == 204  # idempotent
    assert members(client, auth_headers, box["id"]) == ["doc2"]
    assert put(client, auth_headers, box["id"], "no-such-source").status_code == 404
    bad = client.get(f"/v1/collections/{box['id']}/sources?limit=101", headers=auth_headers)
    assert bad.status_code == 422


def test_collections_filtered_by_source_show_its_memberships(app, client, auth_headers) -> None:
    add_source(app, "doc1")
    first = create(client, auth_headers, "First")
    second = create(client, auth_headers, "Second")
    create(client, auth_headers, "Third")
    for box in (first, second):
        put(client, auth_headers, box["id"], "doc1")
    shown = listed(client, auth_headers, "?source_id=doc1")
    assert sorted(item["id"] for item in shown) == sorted([first["id"], second["id"]])
    assert listed(client, auth_headers, "?source_id=seed-m1") == []
    missing = client.get("/v1/collections?source_id=no-such-source", headers=auth_headers)
    assert missing.status_code == 404
    long = client.get(f"/v1/collections?source_id={'x' * 65}", headers=auth_headers)
    assert long.status_code == 422


def test_filter_by_another_users_source_is_404(app, client, auth_headers) -> None:
    app.state.container.sources.add(WS, source("bob-doc", owner="user-b", ws=WS))
    create(client, auth_headers, "Mine")
    response = client.get("/v1/collections?source_id=bob-doc", headers=auth_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_source_output_carries_retention_until(app, client, auth_headers) -> None:
    until = datetime(2026, 12, 1, 8, 30, tzinfo=UTC)
    add_source(app, "audio1", kind=SourceKind.AUDIO, retention_until=until)
    add_source(app, "doc1")
    audio = client.get("/v1/sources/audio1", headers=auth_headers).json()
    assert audio["retention_until"] == "2026-12-01T08:30:00Z"
    assert client.get("/v1/sources/doc1", headers=auth_headers).json()["retention_until"] is None
    library = client.get("/v1/sources", headers=auth_headers).json()["items"]
    assert {item["id"]: item["retention_until"] for item in library}["audio1"] == (
        "2026-12-01T08:30:00Z"
    )
    box = create(client, auth_headers, "Box")
    put(client, auth_headers, box["id"], "audio1")
    inside = client.get(f"/v1/collections/{box['id']}/sources", headers=auth_headers).json()
    assert inside["items"][0]["retention_until"] == "2026-12-01T08:30:00Z"


def test_deleted_source_disappears_from_its_collections(app, client, auth_headers) -> None:
    add_source(app, "doc1")
    box = create(client, auth_headers, "Box")
    put(client, auth_headers, box["id"], "doc1")
    assert client.delete("/v1/sources/doc1", headers=auth_headers).status_code == 204
    assert members(client, auth_headers, box["id"]) == []
    assert listed(client, auth_headers)[0]["source_count"] == 0
    assert client.get("/v1/collections?source_id=doc1", headers=auth_headers).status_code == 404
    assert put(client, auth_headers, box["id"], "doc1").status_code == 404


def test_meeting_in_a_collection(client, auth_headers) -> None:
    box = create(client, auth_headers, "Meetings")
    assert put(client, auth_headers, box["id"], "seed-m1").status_code == 204
    assert members(client, auth_headers, box["id"]) == ["seed-m1"]
    assert [i["id"] for i in listed(client, auth_headers, "?source_id=seed-m1")] == [box["id"]]
    assert client.delete("/v1/meetings/seed-m1", headers=auth_headers).status_code == 204
    assert members(client, auth_headers, box["id"]) == []


@pytest.mark.parametrize(("token", "user", "workspace"), OTHERS)
def test_other_user_and_other_workspace_get_404_on_every_route(
    app, client, auth_headers, token, user, workspace
) -> None:
    add_source(app, "doc1")
    box = create(client, auth_headers, "Mine")
    put(client, auth_headers, box["id"], "doc1")
    app.state.container.auth.add_dev_token(token, user, workspace)
    other = {"Authorization": f"Bearer {token}"}
    path = f"/v1/collections/{box['id']}"
    attempts = (
        ("get", "/v1/collections?source_id=doc1", None),
        ("post", "/v1/collections", {"name": "Child", "parent_id": box["id"]}),
        ("patch", path, {"name": "Taken"}),
        ("delete", path, None),
        ("get", f"{path}/sources", None),
        ("put", f"{path}/sources/doc1", None),
        ("delete", f"{path}/sources/doc1", None),
    )
    for method, url, body in attempts:
        response = client.request(method, url, headers=other, json=body)
        assert response.status_code == 404, (method, url)
        assert response.json()["error"]["code"] == "not_found"
    assert client.get("/v1/collections", headers=other).json()["items"] == []
    # their own collection can not take my source either
    theirs = create(client, other, "Theirs")
    assert put(client, other, theirs["id"], "doc1").status_code == 404
    # and nothing of mine changed
    assert [i["name"] for i in listed(client, auth_headers)] == ["Mine"]
    assert members(client, auth_headers, box["id"]) == ["doc1"]


BAD_NAMES = ("   ", "n" * 121, "bad\x07name", "bad" + chr(0x202E) + "name")


@pytest.mark.parametrize("name", BAD_NAMES)
def test_bad_names_are_422_and_the_body_never_echoes_them(client, auth_headers, name) -> None:
    box = create(client, auth_headers, "Fine")
    responses = (
        client.post("/v1/collections", headers=auth_headers, json={"name": name}),
        client.patch(f"/v1/collections/{box['id']}", headers=auth_headers, json={"name": name}),
    )
    for response in responses:
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert "bad" not in response.text
        assert "nnnn" not in response.text
    assert [i["name"] for i in listed(client, auth_headers)] == ["Fine"]


def test_empty_name_is_422(client, auth_headers) -> None:
    response = client.post("/v1/collections", headers=auth_headers, json={"name": ""})
    assert response.status_code == 422


def test_collection_names_never_reach_logs(client, auth_headers, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    box = create(client, auth_headers, "Secret Ledger Alpha")
    client.patch(
        f"/v1/collections/{box['id']}", headers=auth_headers, json={"name": "Secret Ledger Beta"}
    )
    client.post("/v1/collections", headers=auth_headers, json={"name": "Secret\x07Gamma"})
    client.delete(f"/v1/collections/{box['id']}", headers=auth_headers)
    assert "Secret" not in caplog.text
    assert "Ledger" not in caplog.text


ROUTES = (
    ("get", "/v1/collections", None),
    ("post", "/v1/collections", {"name": "Any"}),
    ("patch", "/v1/collections/anything", {"name": "Any"}),
    ("delete", "/v1/collections/anything", None),
    ("get", "/v1/collections/anything/sources", None),
    ("put", "/v1/collections/anything/sources/doc1", None),
    ("delete", "/v1/collections/anything/sources/doc1", None),
)


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_azure_engine_answers_501_for_every_collection_route(
    app, client, auth_headers, method, path, body
) -> None:
    app.state.container.collections = None
    response = client.request(method, path, headers=auth_headers, json=body)
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_available_on_engine"


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_collection_routes_require_authentication(client, method, path, body) -> None:
    assert client.request(method, path, json=body).status_code == 401


def test_openapi_lists_the_collection_paths(app) -> None:
    paths = app.openapi()["paths"]
    assert set(paths["/v1/collections"]) == {"get", "post"}
    assert set(paths["/v1/collections/{collection_id}"]) == {"patch", "delete"}
    assert set(paths["/v1/collections/{collection_id}/sources"]) == {"get"}
    assert set(paths["/v1/collections/{collection_id}/sources/{source_id}"]) == {"put", "delete"}
    output = app.openapi()["components"]["schemas"]["SourceOutput"]
    assert "retention_until" in output["properties"]
    assert "retention_until" not in output.get("required", [])
