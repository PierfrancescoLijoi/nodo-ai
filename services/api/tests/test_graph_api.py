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
