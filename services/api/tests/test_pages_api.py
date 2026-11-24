from datetime import UTC, datetime

from nodo_api.domain.pages import (
    Page,
    PageCitation,
    PageClaim,
    PageKind,
    PageProposal,
    PageProvenance,
    PageStatus,
    claim_text_hash,
)
from nodo_api.domain.sources import Locator


def _proposal(app, page_id: str = "wiki-page"):
    now = datetime(2026, 11, 24, tzinfo=UTC)
    page = Page(
        page_id, "workspace-a", "user-a", PageKind.CONCEPT, "concept-a", PageStatus.PROPOSED, now
    )
    claim = PageClaim(
        0,
        (0, 4),
        "Fact",
        claim_text_hash("Fact"),
        (PageCitation("source-a", "fragment-a", 1, Locator(chars=(0, 4)), "Fact"),),
        True,
    )
    app.state.container.pages.put_proposal(
        page,
        PageProposal("proposal-a", page.id, "Fact", (claim,), PageProvenance("m", "p"), now),
    )
    return page


def test_pages_are_owner_scoped_and_rejection_is_repeatable(app, client, auth_headers):
    page = _proposal(app)
    app.state.container.auth.add_dev_token("page-bob-token-0123456789", "user-b", "workspace-a")
    bob = {"Authorization": "Bearer page-bob-token-0123456789", "Idempotency-Key": "page-key-1"}

    assert client.get(f"/v1/pages/{page.id}", headers=bob).status_code == 404
    assert client.post(f"/v1/pages/{page.id}/reject", headers=bob).status_code == 404

    headers = {**auth_headers, "Idempotency-Key": "page-key-1"}
    first = client.post(f"/v1/pages/{page.id}/reject", headers=headers)
    again = client.post(f"/v1/pages/{page.id}/reject", headers=headers)

    assert first.status_code == again.status_code == 200
    assert first.json()["status"] == again.json()["status"] == "rejected"
    assert client.get("/v1/pages", headers=auth_headers).json()["items"][0]["id"] == page.id
    assert client.get("/v1/pages/health", headers=auth_headers).json() == {"items": []}


def test_page_commands_require_authentication_and_idempotency_key(app, client):
    page = _proposal(app)

    assert client.post(f"/v1/pages/{page.id}/approve").status_code == 401
    response = client.post(
        f"/v1/pages/{page.id}/approve",
        headers={"Authorization": "Bearer test-dev-token-0123456789"},
    )
    assert response.status_code == 422


def test_human_edit_appends_a_revision_and_returns_its_claims(app, client, auth_headers):
    page = _proposal(app)
    command = {**auth_headers, "Idempotency-Key": "page-key-2"}
    assert client.post(f"/v1/pages/{page.id}/approve", headers=command).status_code == 200

    edited = client.patch(
        f"/v1/pages/{page.id}",
        headers=command,
        json={"body_md": "Note", "claims": [{"span_start": 0, "span_end": 4}]},
    )
    repeated = client.patch(
        f"/v1/pages/{page.id}",
        headers=command,
        json={"body_md": "Note", "claims": [{"span_start": 0, "span_end": 4}]},
    )
    revisions = client.get(f"/v1/pages/{page.id}/revisions", headers=auth_headers)

    assert edited.status_code == repeated.status_code == 200
    assert [item["number"] for item in revisions.json()] == [2, 1]
    assert revisions.json()[0]["claims"] == [
        {
            "ordinal": 0,
            "span_start": 0,
            "span_end": 4,
            "text": "Note",
            "machine_generated": False,
            "citations": [],
        }
    ]
