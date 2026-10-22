"""Regression tests for the findings of the security review (22 October 2026)."""

from __future__ import annotations

import pytest

from nodo_api.config import DEV_ATTESTATION_KEY, Settings
from nodo_api.domain.models import Answerability, Citation, GraphNode
from nodo_api.main import AppContainer, create_app
from nodo_api.services.query import EvidenceValidator, GeneratedAnswer

WORKSPACE = "workspace-a"


def _second_user(app) -> dict[str, str]:
    app.state.container.auth.add_dev_token("bob-token-0123456789", "user-bob", WORKSPACE)
    return {"Authorization": "Bearer bob-token-0123456789"}


# --- fail-closed configuration -------------------------------------------------------------------


def test_default_settings_refuse_to_start() -> None:
    with pytest.raises(ValueError, match="OIDC"):
        Settings()


def test_dev_auth_requires_a_development_environment() -> None:
    with pytest.raises(ValueError, match="Dev auth"):
        Settings(auth_mode="dev", attestation_hmac_key="k" * 40)


def test_demo_data_requires_a_development_environment() -> None:
    with pytest.raises(ValueError, match="Demo data"):
        Settings(
            seed_demo_data=True,
            oidc_issuer="https://issuer.example/",
            oidc_audience="nodo-api",
            attestation_hmac_key="k" * 40,
        )


@pytest.mark.parametrize("key", [DEV_ATTESTATION_KEY, "short-but-sixteen!!"])
def test_production_rejects_weak_attestation_keys(key: str) -> None:
    with pytest.raises(ValueError, match="HMAC key"):
        Settings(
            oidc_issuer="https://issuer.example/",
            oidc_audience="nodo-api",
            attestation_hmac_key=key,
        )


def test_empty_dev_token_is_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(environment="test", auth_mode="dev", dev_token="")


def test_oidc_app_without_demo_data_has_no_seeded_meetings() -> None:
    settings = Settings(
        environment="test", auth_mode="dev", attestation_hmac_key="test-attestation-key-123"
    )
    container: AppContainer = create_app(settings).state.container
    assert container.meetings.all_for_workspace("workspace-a") == ()


# --- idempotency ---------------------------------------------------------------------------------


def test_idempotency_keys_are_per_user(app, client, auth_headers, attestation) -> None:
    bob = _second_user(app)
    body = {"recording_attestation": attestation}
    alice_meeting = client.post(
        "/v1/meetings", headers={**auth_headers, "Idempotency-Key": "shared-key-1"}, json=body
    ).json()
    bob_meeting = client.post(
        "/v1/meetings", headers={**bob, "Idempotency-Key": "shared-key-1"}, json=body
    ).json()
    assert alice_meeting["id"] != bob_meeting["id"]
    assert bob_meeting["owner_id"] == "user-bob"
    assert (
        client.post(f"/v1/meetings/{alice_meeting['id']}/complete", headers=bob).status_code == 404
    )


def test_same_key_with_a_different_request_conflicts(client, auth_headers, attestation) -> None:
    headers = {**auth_headers, "Idempotency-Key": "reuse-key-1"}
    first = client.post(
        "/v1/meetings", headers=headers, json={"recording_attestation": attestation}
    )
    assert first.status_code == 201
    changed = {**attestation, "retention_policy_id": "retention-7d"}
    second = client.post("/v1/meetings", headers=headers, json={"recording_attestation": changed})
    assert second.status_code == 409


def test_a_deleted_meeting_is_not_returned_for_its_old_key(
    client, auth_headers, attestation
) -> None:
    headers = {**auth_headers, "Idempotency-Key": "delete-key-1"}
    body = {"recording_attestation": attestation}
    first = client.post("/v1/meetings", headers=headers, json=body).json()
    assert client.delete(f"/v1/meetings/{first['id']}", headers=auth_headers).status_code == 204
    again = client.post("/v1/meetings", headers=headers, json=body)
    assert again.status_code == 201
    assert again.json()["id"] != first["id"]


@pytest.mark.parametrize("key", ["short", "has spaces in it", "x" * 129])
def test_invalid_idempotency_keys_are_rejected(client, auth_headers, attestation, key) -> None:
    response = client.post(
        "/v1/meetings",
        headers={**auth_headers, "Idempotency-Key": key},
        json={"recording_attestation": attestation},
    )
    assert response.status_code == 422


# --- ownership inside a workspace ----------------------------------------------------------------


def test_other_user_in_the_same_workspace_cannot_touch_my_meeting(
    app, client, auth_headers
) -> None:
    bob = _second_user(app)
    assert client.get("/v1/meetings/seed-m1", headers=bob).status_code == 404
    assert client.get("/v1/meetings/seed-m1/transcript", headers=bob).status_code == 404
    assert (
        client.patch(
            "/v1/meetings/seed-m1/review",
            headers=bob,
            json={"corrections": [{"segment_id": "seg-001", "text": "x"}]},
        ).status_code
        == 404
    )
    assert client.post("/v1/export", headers=bob, json={"meeting_id": "seed-m1"}).status_code == 404
    assert client.delete("/v1/meetings/seed-m1", headers=bob).status_code == 404
    # still there for the owner
    assert client.get("/v1/meetings/seed-m1", headers=auth_headers).status_code == 200


def test_other_user_gets_no_evidence_or_entities(app, client) -> None:
    bob = _second_user(app)
    body = client.post(
        "/v1/query", headers=bob, json={"question": "ingestion pipeline", "ui_locale": "en"}
    ).json()
    assert body["answerability"] == "unanswerable"
    assert body["citations"] == []
    assert body["related_entities"] == []
    graph = client.get("/v1/graph/subgraph?entity=ingestion", headers=bob).json()
    assert graph["nodes"] == []


# --- erasure -------------------------------------------------------------------------------------


def test_delete_can_be_retried_after_a_partial_failure(app) -> None:
    container: AppContainer = app.state.container
    calls = {"count": 0}
    real_erase = container.retriever.erase

    def flaky(workspace_id: str, meeting_id: str) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("index unavailable")
        real_erase(workspace_id, meeting_id)

    container.retriever.erase = flaky
    with pytest.raises(RuntimeError):
        container.meeting_service.delete(WORKSPACE, "user-a", "seed-m1")
    container.meeting_service.delete(WORKSPACE, "user-a", "seed-m1")  # the retry finishes the job
    assert container.blobs.was_erased(WORKSPACE, "seed-m1")
    assert container.graph.lookup(WORKSPACE, "ingestion", 2, 50, frozenset({"seed-m1"})) == ()
    with pytest.raises(Exception, match=""):
        container.transcripts.latest(WORKSPACE, "seed-m1")


# --- the evidence validator ----------------------------------------------------------------------


def test_related_entities_must_be_backed_by_cited_segments(app) -> None:
    container: AppContainer = app.state.container
    segment = container.transcripts.latest(WORKSPACE, "seed-m1")[0]
    citation = Citation(
        segment.meeting_id, segment.id, segment.start_ms, segment.end_ms, segment.text
    )
    backed = GraphNode("n-1", "ingestion", segment.meeting_id, segment.id)
    unrelated = GraphNode("n-2", "payroll", "seed-m2", "seg-002")
    generated = GeneratedAnswer(
        "Grounded",
        Answerability.SUPPORTED,
        (citation,),
        ("ingestion", "payroll", "<script>alert(1)</script>", "x" * 200),
    )
    validated = EvidenceValidator(container.transcripts).validate(
        WORKSPACE, generated, (segment,), (backed, unrelated), frozenset({segment.meeting_id})
    )
    assert validated.related_entities == ("ingestion",)


def test_citation_to_a_meeting_the_caller_does_not_own_is_dropped(app) -> None:
    container: AppContainer = app.state.container
    segment = container.transcripts.latest(WORKSPACE, "seed-m1")[0]
    citation = Citation(
        segment.meeting_id, segment.id, segment.start_ms, segment.end_ms, segment.text
    )
    generated = GeneratedAnswer("Grounded", Answerability.SUPPORTED, (citation,), ())
    validated = EvidenceValidator(container.transcripts).validate(
        WORKSPACE, generated, (segment,), (), frozenset({"seed-m2"})
    )
    assert validated.answerability is Answerability.UNANSWERABLE


def test_segment_ids_are_scoped_to_their_meeting(app) -> None:
    container: AppContainer = app.state.container
    assert container.transcripts.get_segment(WORKSPACE, "seed-m2", "seg-001") is None
    assert container.transcripts.get_segment(WORKSPACE, "seed-m1", "seg-001") is not None


# --- review, attestation, bounds -----------------------------------------------------------------


def test_review_rejects_unknown_segments_and_stale_versions(client, auth_headers) -> None:
    unknown = client.patch(
        "/v1/meetings/seed-m1/review",
        headers=auth_headers,
        json={"corrections": [{"segment_id": "nope", "text": "x"}]},
    )
    assert unknown.status_code == 422
    stale = client.patch(
        "/v1/meetings/seed-m1/review",
        headers=auth_headers,
        json={"corrections": [{"segment_id": "seg-001", "text": "x"}], "base_version": 7},
    )
    assert stale.status_code == 409


@pytest.mark.parametrize(
    "override",
    [
        {"notice_version": "invented-notice"},
        {"retention_policy_id": "never-delete"},
    ],
)
def test_attestation_must_reference_known_notices_and_policies(
    client, auth_headers, attestation, override
) -> None:
    response = client.post(
        "/v1/meetings",
        headers={**auth_headers, "Idempotency-Key": "registry-key-1"},
        json={"recording_attestation": {**attestation, **override}},
    )
    assert response.status_code == 422


def test_oversized_inputs_are_rejected(client, auth_headers) -> None:
    too_long = client.post(
        "/v1/query", headers=auth_headers, json={"question": "x" * 2001, "ui_locale": "en"}
    )
    assert too_long.status_code == 422
    graph = client.get(f"/v1/graph/subgraph?entity={'a' * 129}", headers=auth_headers)
    assert graph.status_code == 422


def test_untrusted_request_ids_are_replaced(client, auth_headers) -> None:
    response = client.get("/healthz", headers={**auth_headers, "X-Request-ID": "x" * 5000})
    assert response.headers["X-Request-ID"] != "x" * 5000
    assert len(response.headers["X-Request-ID"]) == 36


def test_each_export_request_is_a_separate_job(client, auth_headers) -> None:
    first = client.post("/v1/export", headers=auth_headers, json={}).json()["job_id"]
    second = client.post("/v1/export", headers=auth_headers, json={}).json()["job_id"]
    assert first != second
