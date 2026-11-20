"""The concept hierarchy API (ADR-030): a concept with its parents and children, the parent links
waiting for review, and their review. Memory engine, seeded the way imports and reviews write."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from nodo_api.domain.graph import (
    EntityType,
    Extraction,
    MeetingRef,
    RelationKind,
    SourceRef,
    entity_id,
    normalize_label,
    relation_id,
    source_entity_id,
)
from nodo_api.domain.sources import Fragment, Locator, Source, SourceKind

WS = "workspace-a"
USER = "user-a"
T = EntityType.TOPIC
K = RelationKind
WHEN = datetime(2026, 11, 1, tzinfo=UTC)
OTHERS = (
    ("bob-token-0123456789", "user-b", "workspace-a"),
    ("carol-token-0123456789", "user-a", "workspace-b"),  # the same user id elsewhere
)


def cid(name: str) -> str:
    return entity_id(WS, USER, T, normalize_label(f"Concept {name}"))


def link(child: str, parent: str) -> str:
    return relation_id(WS, USER, cid(child), K.CHILD_OF, cid(parent))


def seed(app, pairs, *, source: str = "doc-h1", title: str = "Handbook", fragments=("f1",)):
    """One document whose fragments `fragments` support `child` under `parent` for each pair."""
    from tests.graph_contract import ent, rel

    container = app.state.container
    container.sources.add(
        WS, Source(source, WS, USER, SourceKind.DOCUMENT, title, "ready", WHEN, content_hash=source)
    )
    container.fragments.add_version(
        WS,
        source,
        (),
        tuple(
            Fragment(
                f, source, None, i, f"Invented text {i}", Locator(chars=(i * 10, i * 10 + 9)), 1
            )
            for i, f in enumerate(fragments)
        ),
    )
    names = list(dict.fromkeys(n for pair in pairs for n in pair))
    extraction = Extraction(
        "",
        tuple(ent(n, T, f"Concept {n}", fragments[0]) for n in names),
        tuple(rel(c, K.CHILD_OF, p, *fragments) for c, p in pairs),
    )
    container.graph.replace_source_evidence(
        WS, USER, SourceRef(source, title, WHEN, SourceKind.DOCUMENT), extraction
    )


def review(client, headers, relation: str, status: str):
    return client.patch(f"/v1/hierarchy/links/{relation}", headers=headers, json={"status": status})


def walk(client, headers, name: str, query: str = ""):
    return client.get(f"/v1/concepts/{cid(name)}/hierarchy{query}", headers=headers)


def test_concept_with_two_approved_parents_shows_both(app, client, auth_headers) -> None:
    seed(app, [("a", "b"), ("a", "c")])
    for parent in ("b", "c"):
        assert review(client, auth_headers, link("a", parent), "approved").status_code == 200
    response = walk(client, auth_headers, "a", "?direction=parents")
    assert response.status_code == 200
    body = response.json()
    assert body["concept"] == {
        "id": cid("a"),
        "type": "topic",
        "label": "Concept a",
        "status": None,
    }
    assert {n["id"] for n in body["nodes"]} == {cid("a"), cid("b"), cid("c")}
    assert {(item["child_id"], item["parent_id"]) for item in body["links"]} == {
        (cid("a"), cid("b")),
        (cid("a"), cid("c")),
    }
    assert all(item["status"] == "approved" for item in body["links"])
    assert body["truncated"] is False


def test_proposed_links_need_include_proposed(app, client, auth_headers) -> None:
    seed(app, [("a", "b")])
    assert walk(client, auth_headers, "a").json()["links"] == []
    shown = walk(client, auth_headers, "a", "?include_proposed=true").json()["links"]
    assert [(item["id"], item["status"]) for item in shown] == [(link("a", "b"), "proposed")]


def test_evidence_carries_the_fragment_locator(app, client, auth_headers) -> None:
    seed(app, [("a", "b")])
    body = walk(client, auth_headers, "a", "?include_proposed=true").json()
    assert body["links"][0]["evidence"] == [
        {"source_id": "doc-h1", "fragment_id": "f1", "locator": {"chars": [0, 9]}}
    ]


def test_evidence_is_capped_at_three_per_link(app, client, auth_headers) -> None:
    seed(app, [("a", "b")], fragments=("f1", "f2", "f3", "f4", "f5"))
    body = walk(client, auth_headers, "a", "?include_proposed=true").json()
    assert len(body["links"][0]["evidence"]) == 3
    proposals = client.get("/v1/hierarchy/proposals", headers=auth_headers).json()
    assert len(proposals["items"][0]["evidence"]) == 3


def test_mentioned_in_shows_current_titles_of_sources_and_meetings(
    app, client, auth_headers
) -> None:
    from tests.graph_contract import ent

    container = app.state.container
    seed(app, [("a", "b")])
    seed(app, [("a", "c")], source="doc-gone", title="Old notes")
    meeting = container.meetings.get_owned(WS, USER, "seed-m1")
    container.graph.upsert_extraction(
        WS,
        USER,
        MeetingRef("seed-m1", meeting.title or "Meeting", meeting.created_at),
        Extraction("", (ent("a", T, "Concept a", "seg-001"),), ()),
    )
    container.sources.update(WS, "doc-h1", title="Handbook renamed")
    container.meetings.save(WS, replace(meeting, title="Planning renamed"))
    gone = container.sources.get_owned(WS, USER, "doc-gone")
    container.sources.save(WS, replace(gone, tombstoned_at=WHEN))

    mentioned = walk(client, auth_headers, "a").json()["mentioned_in"]
    assert sorted(mentioned, key=lambda m: m["source_id"]) == [
        {"source_id": "doc-h1", "title": "Handbook renamed"},
        {"source_id": "seed-m1", "title": "Planning renamed"},
    ]


def test_proposals_list_then_approve_moves_the_link_out_of_the_list(
    app, client, auth_headers
) -> None:
    seed(app, [("a", "b")])
    listed = client.get("/v1/hierarchy/proposals", headers=auth_headers).json()
    assert [item["id"] for item in listed["items"]] == [link("a", "b")]
    assert {n["id"] for n in listed["nodes"]} == {cid("a"), cid("b")}
    assert listed["truncated"] is False
    approved = review(client, auth_headers, link("a", "b"), "approved")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["child_id"] == cid("a") and approved.json()["parent_id"] == cid("b")
    assert client.get("/v1/hierarchy/proposals", headers=auth_headers).json()["items"] == []


def test_reject_then_reapprove_into_a_cycle_is_409_would_create_cycle(
    app, client, auth_headers
) -> None:
    seed(app, [("a", "b")])
    assert review(client, auth_headers, link("a", "b"), "rejected").status_code == 200
    seed(app, [("b", "a")], source="doc-h2", title="Second")
    response = review(client, auth_headers, link("a", "b"), "approved")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "would_create_cycle"
    assert "Concept" not in response.text


def test_reject_then_reapprove_beyond_six_levels_is_409_too_deep(app, client, auth_headers) -> None:
    seed(app, [("d", "e")])
    assert review(client, auth_headers, link("d", "e"), "rejected").status_code == 200
    chain = [("a", "b"), ("b", "c"), ("c", "d"), ("e", "f"), ("f", "g"), ("g", "h")]
    seed(app, chain, source="doc-h2", title="Second")
    response = review(client, auth_headers, link("d", "e"), "approved")  # 7 links from a to h
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "too_deep"
    assert "Concept" not in response.text


def test_patch_on_a_non_hierarchy_relation_is_404(app, client, auth_headers) -> None:
    seed(app, [("a", "b")])
    mention = relation_id(WS, USER, cid("a"), K.MENTIONED_IN, source_entity_id(WS, USER, "doc-h1"))
    assert review(client, auth_headers, mention, "rejected").status_code == 404
    assert review(client, auth_headers, "no-such-relation", "approved").status_code == 404


@pytest.mark.parametrize(("token", "user", "workspace"), OTHERS)
def test_other_user_and_other_workspace_get_404(
    app, client, auth_headers, token, user, workspace
) -> None:
    seed(app, [("a", "b")])
    app.state.container.auth.add_dev_token(token, user, workspace)
    other = {"Authorization": f"Bearer {token}"}
    assert walk(client, other, "a").status_code == 404
    assert review(client, other, link("a", "b"), "approved").status_code == 404
    assert client.get("/v1/hierarchy/proposals", headers=other).json()["items"] == []
    assert walk(client, auth_headers, "a").status_code == 200


def test_depth_seven_is_422(app, client, auth_headers) -> None:
    seed(app, [("a", "b")])
    assert walk(client, auth_headers, "a", "?depth=7").status_code == 422
    assert walk(client, auth_headers, "a", "?depth=6").status_code == 200
    assert walk(client, auth_headers, "a", "?direction=sideways").status_code == 422


def test_review_is_audited_with_ids_only(app, client, auth_headers) -> None:
    seed(app, [("a", "b")])
    log = []
    audit = app.state.container.graph_service._audit
    original = audit.append
    audit.append = lambda ws, kind, meta: (log.append((kind, meta)), original(ws, kind, meta))[1]
    review(client, auth_headers, link("a", "b"), "approved")
    assert log == [("relation_reviewed", {"relation_id": link("a", "b"), "status": "approved"})]
    assert "Concept" not in repr(log)


def test_legacy_graph_reviews_still_hide_child_of(app, client, auth_headers) -> None:
    seed(app, [("a", "b")])
    inbox = client.get("/v1/graph/reviews?limit=200", headers=auth_headers).json()
    assert all(edge["kind"] != "CHILD_OF" for edge in inbox["edges"])
    assert link("a", "b") not in {edge["id"] for edge in inbox["edges"]}
    legacy = client.patch(
        f"/v1/graph/relations/{link('a', 'b')}", headers=auth_headers, json={"status": "approved"}
    )
    assert legacy.status_code == 404


ROUTES = (
    ("get", "/v1/concepts/anything/hierarchy", None),
    ("get", "/v1/hierarchy/proposals", None),
    ("patch", "/v1/hierarchy/links/anything", {"status": "approved"}),
)


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_azure_engine_answers_501_for_every_hierarchy_route(
    app, client, auth_headers, method, path, body
) -> None:
    app.state.container.hierarchy = None
    response = client.request(method, path, headers=auth_headers, json=body)
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_available_on_engine"


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_hierarchy_routes_require_authentication(client, method, path, body) -> None:
    assert client.request(method, path, json=body).status_code == 401


def test_sqlite_titles_reads_current_titles_of_live_owned_sources_and_meetings(tmp_path) -> None:
    from nodo_api.adapters.local.db import LocalDatabase
    from nodo_api.adapters.local.repos import SqliteMeetingRepository
    from nodo_api.adapters.local.sources import SqliteSourceRepository
    from nodo_api.domain.models import Locale, Meeting, MeetingState, RecordingAttestation

    db = LocalDatabase(tmp_path / "nodo.db")
    try:
        sources, meetings = SqliteSourceRepository(db), SqliteMeetingRepository(db)
        attestation = RecordingAttestation(
            USER, "m1", "2026-10-v1", WHEN, "retention-30d", Locale.EN, "a", "sig"
        )
        meeting = Meeting("m1", WS, USER, WHEN, MeetingState.UPLOADING, attestation)
        meetings.add(WS, meeting, "key-m1", "hash")
        meetings.save(WS, replace(meeting, title="Planning renamed"))
        for source_id, owner in (("d1", USER), ("d2", USER), ("d3", "user-b")):
            sources.add(
                WS,
                Source(source_id, WS, owner, SourceKind.DOCUMENT, "Old", "ready", WHEN),
            )
        sources.update(WS, "d1", title="Handbook renamed")
        sources.save(WS, replace(sources.get_owned(WS, USER, "d2"), tombstoned_at=WHEN))
        found = sources.titles(WS, USER, ["d1", "d2", "d3", "m1", "missing", "d1"])
        assert found == {"d1": "Handbook renamed", "m1": "Planning renamed"}
        assert sources.titles(WS, USER, []) == {}
        assert sources.titles("workspace-b", USER, ["d1"]) == {}
    finally:
        db.close()


def test_local_engine_serves_the_hierarchy_routes(tmp_path) -> None:
    from tests.test_sources_import_api import _local_client

    app, client, headers = _local_client(tmp_path)
    try:
        assert app.state.container.hierarchy is not None
        listed = client.get("/v1/hierarchy/proposals", headers=headers)
        assert listed.status_code == 200
        assert listed.json() == {"items": [], "nodes": [], "truncated": False}
        assert client.get("/v1/concepts/missing/hierarchy", headers=headers).status_code == 404
    finally:
        client.close()
        app.state.container.runtime.db.close()
