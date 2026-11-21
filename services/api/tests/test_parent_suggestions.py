from nodo_api.domain.graph import EntityType, Extraction, SourceRef
from nodo_api.domain.sources import Fragment, Locator, Source, SourceKind
from tests.graph_contract import ent
from tests.test_hierarchy_api import USER, WHEN, WS, cid


def seed_candidates(app):
    container = app.state.container
    source_id = "doc-suggestions"
    container.sources.add(
        WS,
        Source(source_id, WS, USER, SourceKind.DOCUMENT, "Guide", "ready", WHEN, content_hash="x"),
    )
    fragments = (
        Fragment(
            "f-shared",
            source_id,
            None,
            0,
            "Concept a is part of Concept b.",
            Locator(page=1),
            1,
        ),
    )
    container.fragments.add_version(WS, source_id, (), fragments)
    extraction = Extraction(
        "",
        (
            ent("a", EntityType.TOPIC, "Concept a", "f-shared"),
            ent("b", EntityType.TOPIC, "Concept b", "f-shared"),
        ),
        (),
    )
    container.graph.replace_source_evidence(
        WS, USER, SourceRef(source_id, "Guide", WHEN, SourceKind.DOCUMENT), extraction
    )


def test_suggestions_become_reviewable_proposals(app, client, auth_headers):
    seed_candidates(app)
    response = client.post(
        f"/v1/concepts/{cid('a')}/parent-suggestions",
        headers=auth_headers,
        json={"locale": "en"},
    )
    assert response.status_code == 200
    body = response.json()
    assert [(item["child_id"], item["parent_id"], item["status"]) for item in body["items"]] == [
        (cid("a"), cid("b"), "proposed")
    ]
    assert {node["id"] for node in body["nodes"]} == {cid("a"), cid("b")}
    assert body["truncated"] is False


def test_no_candidates_returns_an_empty_list(app, client, auth_headers):
    seed_candidates(app)
    container = app.state.container
    container.graph.erase(WS, "doc-suggestions")
    response = client.post(
        f"/v1/concepts/{cid('a')}/parent-suggestions", headers=auth_headers, json={}
    )
    assert response.status_code == 404
