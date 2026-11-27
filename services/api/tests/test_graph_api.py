from __future__ import annotations

import pytest

ZERO_ID = "00000000-0000-0000-0000-000000000000"


def _other_user(app) -> dict[str, str]:
    app.state.container.auth.add_dev_token("bob-token-0123456789", "bob", "workspace-a")
    return {"Authorization": "Bearer bob-token-0123456789"}


# --- meetings list and insights ------------------------------------------------------------------


def test_list_returns_newest_first_with_what_each_meeting_produced(client, auth_headers) -> None:
    body = client.get("/v1/meetings", headers=auth_headers).json()
    assert [m["id"] for m in body["items"]] == ["seed-m3", "seed-m2", "seed-m1"]
    assert body["next_cursor"] is None
    oldest = body["items"][2]
    assert oldest["decision_count"] == 1 and oldest["people"] and oldest["topics"]


def test_pages_follow_a_cursor_without_gaps_or_repeats(client, auth_headers) -> None:
    seen: list[str] = []
    cursor = None
    for _ in range(5):
        url = "/v1/meetings?limit=1" + (f"&cursor={cursor}" if cursor else "")
        body = client.get(url, headers=auth_headers).json()
        seen += [m["id"] for m in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert seen == ["seed-m3", "seed-m2", "seed-m1"]


@pytest.mark.parametrize("cursor", ["not-base64!!", "Zm9v", "x" * 300])
def test_a_tampered_cursor_is_rejected(client, auth_headers, cursor) -> None:
    assert client.get(f"/v1/meetings?cursor={cursor}", headers=auth_headers).status_code == 422


def test_a_user_only_lists_their_own_meetings(app, client) -> None:
    body = client.get("/v1/meetings", headers=_other_user(app)).json()
    assert body == {"items": [], "next_cursor": None}


def test_meeting_detail_carries_insights_with_review_status(client, auth_headers) -> None:
    newest = client.get("/v1/meetings/seed-m3", headers=auth_headers).json()["insights"]
    assert newest["decisions"][0]["status"] == "approved"
    oldest = client.get("/v1/meetings/seed-m1", headers=auth_headers).json()["insights"]
    assert oldest["decisions"][0]["status"] == "superseded"
    actions = client.get("/v1/meetings/seed-m2", headers=auth_headers).json()["insights"]["actions"]
    assert actions and actions[0]["owner_label"]


def test_create_accepts_a_title(client, auth_headers, attestation) -> None:
    created = client.post(
        "/v1/meetings",
        headers={**auth_headers, "Idempotency-Key": "title-key-0001"},
        json={"recording_attestation": attestation, "title": "Planning sync"},
    )
    assert created.status_code in (200, 201)
    assert created.json()["title"] == "Planning sync"
    too_long = client.post(
        "/v1/meetings",
        headers={**auth_headers, "Idempotency-Key": "title-key-0002"},
        json={"recording_attestation": attestation, "title": "x" * 121},
    )
    assert too_long.status_code == 422


# --- graph ---------------------------------------------------------------------------------------


def test_overview_returns_recent_entities_with_evidence(client, auth_headers) -> None:
    body = client.get("/v1/graph/subgraph?depth=1", headers=auth_headers).json()
    assert body["nodes"] and body["edges"] and body["truncated"] is False
    assert all(edge["evidence"] for edge in body["edges"])


def test_searching_by_name_finds_the_neighbourhood(client, auth_headers) -> None:
    body = client.get("/v1/graph/subgraph?entity=queue&depth=1", headers=auth_headers).json()
    assert body["nodes"]
    assert any(node["type"] == "meeting" and node["meeting_id"] for node in body["nodes"])


def test_depth_two_reaches_further_than_depth_one_and_three_is_refused(
    client, auth_headers
) -> None:
    one = client.get("/v1/graph/subgraph?entity=queue&depth=1", headers=auth_headers).json()
    two = client.get("/v1/graph/subgraph?entity=queue&depth=2", headers=auth_headers).json()
    assert len(two["nodes"]) >= len(one["nodes"])
    assert client.get("/v1/graph/subgraph?depth=3", headers=auth_headers).status_code == 422


def test_seed_id_expands_one_node(client, auth_headers) -> None:
    found = client.get("/v1/graph/subgraph?entity=queue&depth=1", headers=auth_headers).json()
    seed = found["nodes"][0]["id"]
    body = client.get(f"/v1/graph/subgraph?seed_id={seed}&depth=1", headers=auth_headers).json()
    assert any(node["id"] == seed for node in body["nodes"])


def test_node_limit_truncates_and_says_so(client, auth_headers) -> None:
    body = client.get("/v1/graph/subgraph?depth=2&limit=3", headers=auth_headers).json()
    assert len(body["nodes"]) <= 3 and body["truncated"] is True


def test_another_user_sees_an_empty_graph_and_cannot_review(app, client, auth_headers) -> None:
    bob = _other_user(app)
    seen = client.get("/v1/graph/subgraph?depth=1", headers=auth_headers).json()
    edge = seen["edges"][0]["id"]
    assert client.get("/v1/graph/subgraph?depth=1", headers=bob).json()["nodes"] == []
    expand = f"/v1/graph/subgraph?seed_id={seen['nodes'][0]['id']}"
    assert client.get(expand, headers=bob).json()["nodes"] == []
    assert client.get("/v1/graph/changes", headers=bob).json() == {"items": []}
    denied = client.patch(f"/v1/graph/relations/{edge}", headers=bob, json={"status": "rejected"})
    assert denied.status_code == 404


def test_rejecting_a_relation_removes_it_from_the_graph(client, auth_headers) -> None:
    body = client.get("/v1/graph/subgraph?depth=1", headers=auth_headers).json()
    edge = next(e for e in body["edges"] if e["kind"] != "MENTIONED_IN")
    reply = client.patch(
        f"/v1/graph/relations/{edge['id']}", headers=auth_headers, json={"status": "rejected"}
    )
    assert reply.status_code == 200
    after = client.get("/v1/graph/subgraph?depth=1", headers=auth_headers).json()
    assert all(e["id"] != edge["id"] for e in after["edges"])


@pytest.mark.parametrize("status", ["proposed", "maybe", ""])
def test_review_accepts_only_approved_or_rejected(client, auth_headers, status) -> None:
    reply = client.patch(
        f"/v1/graph/relations/{ZERO_ID}", headers=auth_headers, json={"status": status}
    )
    assert reply.status_code == 422


def test_review_of_an_unknown_relation_is_not_found(client, auth_headers) -> None:
    reply = client.patch(
        f"/v1/graph/relations/{ZERO_ID}", headers=auth_headers, json={"status": "approved"}
    )
    assert reply.status_code == 404


def test_changes_list_the_replaced_decision(client, auth_headers) -> None:
    items = client.get("/v1/graph/changes?limit=5", headers=auth_headers).json()["items"]
    assert len(items) == 1
    assert items[0]["meeting_id"] == "seed-m3"
    assert items[0]["new_label"] != items[0]["old_label"]


def test_deleting_a_meeting_removes_what_only_it_contributed(client, auth_headers) -> None:
    before = {
        n["label"]
        for n in client.get("/v1/graph/subgraph?depth=2&limit=200", headers=auth_headers).json()[
            "nodes"
        ]
    }
    old = client.get("/v1/meetings/seed-m1", headers=auth_headers).json()["insights"]["decisions"][
        0
    ]
    assert old["label"] in before
    assert client.delete("/v1/meetings/seed-m1", headers=auth_headers).status_code in (200, 204)
    after = {
        n["label"]
        for n in client.get("/v1/graph/subgraph?depth=2&limit=200", headers=auth_headers).json()[
            "nodes"
        ]
    }
    assert old["label"] not in after
    assert client.get("/v1/graph/changes", headers=auth_headers).json()["items"] == []


# --- the inbox of proposals nobody has decided (ADR-025) ------------------------------------------


def test_the_review_inbox_lists_proposals_and_a_decision_empties_it(
    app, client, auth_headers
) -> None:
    from nodo_api.domain.graph import MeetingRef
    from tests.graph_contract import queue_story

    first, _ = queue_story()
    when = app.state.container.meetings.get_owned("workspace-a", "user-a", "seed-m1").created_at
    app.state.container.graph.upsert_extraction(
        "workspace-a", "user-a", MeetingRef("review-m", "Review me", when), first
    )
    inbox = client.get("/v1/graph/reviews?limit=50", headers=auth_headers).json()
    proposed = [e for e in inbox["edges"] if e["status"] == "proposed"]
    assert len(proposed) == len(inbox["edges"]) >= 3
    labels = {n["id"]: n["label"] for n in inbox["nodes"]}
    assert all(e["source"] in labels and e["target"] in labels for e in inbox["edges"])
    assert all(e["evidence"] for e in inbox["edges"])

    target = inbox["edges"][0]["id"]
    decided = client.patch(
        f"/v1/graph/relations/{target}", headers=auth_headers, json={"status": "approved"}
    )
    assert decided.status_code == 200
    after = client.get("/v1/graph/reviews?limit=50", headers=auth_headers).json()
    assert target not in {e["id"] for e in after["edges"]}
    assert len(after["edges"]) == len(inbox["edges"]) - 1


def test_the_inbox_is_the_callers_own_and_needs_a_token(app, client) -> None:
    assert client.get("/v1/graph/reviews").status_code == 401
    other = client.get("/v1/graph/reviews", headers=_other_user(app)).json()
    assert other["edges"] == [] and other["nodes"] == []


@pytest.mark.parametrize("limit", [0, 201, "x"])
def test_the_inbox_limit_is_bounded(client, auth_headers, limit) -> None:
    assert client.get(f"/v1/graph/reviews?limit={limit}", headers=auth_headers).status_code == 422


def test_a_decision_about_a_proposal_is_written_to_the_audit_trail_by_id_only(
    app, client, auth_headers
) -> None:
    log = []
    audit = app.state.container.graph_service._audit
    original = audit.append
    audit.append = lambda ws, kind, meta: (log.append((kind, meta)), original(ws, kind, meta))[1]
    inbox = client.get("/v1/graph/reviews?limit=50", headers=auth_headers).json()
    if not inbox["edges"]:  # the demo data may hold none: make one
        from nodo_api.domain.graph import MeetingRef
        from tests.graph_contract import queue_story

        when = app.state.container.meetings.get_owned("workspace-a", "user-a", "seed-m1").created_at
        app.state.container.graph.upsert_extraction(
            "workspace-a", "user-a", MeetingRef("review-a", "Review", when), queue_story()[0]
        )
        inbox = client.get("/v1/graph/reviews?limit=50", headers=auth_headers).json()
    target = inbox["edges"][0]["id"]
    client.patch(f"/v1/graph/relations/{target}", headers=auth_headers, json={"status": "rejected"})
    assert ("relation_reviewed", {"relation_id": target, "status": "rejected"}) in log


# --- sources stay out of the legacy graph view (ADR-029, ADR-030) ---------------------------------

LEGACY_NODE_TYPES = ["person", "project", "system", "topic", "decision", "action", "meeting"]
LEGACY_EDGE_KINDS = [
    "MENTIONED_IN",
    "ABOUT",
    "DECIDED_IN",
    "PROPOSED_BY",
    "ASSIGNED_TO",
    "DEPENDS_ON",
    "SUPERSEDES",
    "CONTRADICTS",
]


def _write_document_graph(app) -> None:
    """A document that mentions a seed topic and two of its own, a hierarchy between them, a
    dependency only the document supports and a decision that supersedes another."""
    from nodo_api.domain.graph import EntityType, Extraction, RelationKind, SourceRef
    from tests.graph_contract import ent, rel
    from tests.source_contract import source

    container = app.state.container
    stored = container.sources.add("workspace-a", source("doc1", ws="workspace-a"))
    seed_topic = container.graph.overview("workspace-a", "user-a", 1)[0]
    t, d, k = EntityType.TOPIC, EntityType.DECISION, RelationKind
    extraction = Extraction(
        "",
        (
            ent("s", seed_topic.type, seed_topic.label, "f1"),
            ent("c", t, "Ingestion stage", "f1"),
            ent("p", t, "Platform layer", "f2"),
            ent("old", d, "Keep the cron job", "f2"),
            ent("new", d, "Replace the cron job", "f2"),
        ),
        (
            rel("c", k.CHILD_OF, "p", "f1"),
            rel("c", k.DEPENDS_ON, "p", "f2"),
            rel("new", k.SUPERSEDES, "old", "f2"),
        ),
    )
    ref = SourceRef(stored.id, stored.title, stored.created_at, stored.kind)
    container.graph.replace_source_evidence("workspace-a", "user-a", ref, extraction)


def _assert_legacy(body) -> None:
    assert {n["type"] for n in body["nodes"]} <= set(LEGACY_NODE_TYPES)
    assert {e["kind"] for e in body["edges"]} <= set(LEGACY_EDGE_KINDS)
    ids = {n["id"] for n in body["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in body["edges"])
    assert all(ev["meeting_id"] != "doc1" for e in body["edges"] for ev in e["evidence"])
    assert all(n["meeting_id"] != "doc1" for n in body["nodes"])


LEGACY_URLS = [
    "/v1/graph/subgraph?depth=2&limit=200&include_proposed=true",
    "/v1/graph/subgraph?depth=1",
    "/v1/graph/subgraph?entity=queue&depth=1",
    "/v1/graph/subgraph?entity=platform&depth=2&include_proposed=true",
    "/v1/graph/reviews?limit=200",
    "/v1/graph/reviews?limit=2",
    "/v1/graph/changes?limit=50",
]


def _hidden_ids(app) -> tuple[list[str], list[str]]:
    """The document-only node ids and the relation ids the legacy view must never show."""
    from nodo_api.domain.graph import (
        EntityType,
        RelationKind,
        entity_id,
        normalize_label,
        relation_id,
        source_entity_id,
    )

    ws, user = "workspace-a", "user-a"
    seed_topic = app.state.container.graph.overview(ws, user, 1)[0]

    def topic(label: str) -> str:
        return entity_id(ws, user, EntityType.TOPIC, normalize_label(label))

    node = source_entity_id(ws, user, "doc1")
    child, parent = topic("Ingestion stage"), topic("Platform layer")
    nodes = [node, child, parent]
    relations = [
        relation_id(ws, user, child, RelationKind.CHILD_OF, parent),
        relation_id(ws, user, child, RelationKind.DEPENDS_ON, parent),
        relation_id(ws, user, seed_topic.id, RelationKind.MENTIONED_IN, node),
    ]
    return nodes, relations


def test_legacy_graph_hides_source_nodes_and_child_of_edges(app, client, auth_headers) -> None:
    before = [client.get(url, headers=auth_headers).json() for url in LEGACY_URLS]
    _write_document_graph(app)
    nodes, relations = _hidden_ids(app)

    after = [client.get(url, headers=auth_headers) for url in LEGACY_URLS]
    assert [r.status_code for r in after] == [200] * len(LEGACY_URLS)
    # Meeting data reads exactly as before: documents take no page, fanout or seed slot.
    assert [r.json() for r in after] == before
    for reply in after[:-1]:
        _assert_legacy(reply.json())

    refused = [
        client.patch(
            f"/v1/graph/relations/{rid}", headers=auth_headers, json={"status": "approved"}
        )
        for rid in relations
    ]
    assert [r.status_code for r in refused] == [404] * len(relations)
    pending = {
        e.id for e in app.state.container.graph.pending_review("workspace-a", "user-a", 500).edges
    }
    assert set(relations[:2]) <= pending  # the stored review is unchanged
    shown = "".join(r.text for r in (*after, *refused))
    assert not [i for i in (*nodes, *relations) if i in shown]


def test_openapi_graph_literals_are_unchanged(app) -> None:
    import json
    from pathlib import Path

    contract = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "openapi.json"
    for schemas in (
        app.openapi()["components"]["schemas"],
        json.loads(contract.read_text(encoding="utf-8"))["components"]["schemas"],
    ):
        assert schemas["GraphNodeOutput"]["properties"]["type"]["enum"] == LEGACY_NODE_TYPES
        assert schemas["GraphEdgeOutput"]["properties"]["kind"]["enum"] == LEGACY_EDGE_KINDS
        assert set(schemas["EvidenceOutput"]["properties"]) == {"meeting_id", "segment_id"}
