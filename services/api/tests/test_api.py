from __future__ import annotations


def test_health_and_readiness(client) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}


def test_meeting_requires_attestation(client, auth_headers) -> None:
    response = client.post(
        "/v1/meetings", headers={**auth_headers, "Idempotency-Key": "missing-attestation"}, json={}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_meeting_flow_and_pagination(client, auth_headers, attestation) -> None:
    created = client.post(
        "/v1/meetings",
        headers={**auth_headers, "Idempotency-Key": "api-flow"},
        json={"recording_attestation": attestation},
    )
    assert created.status_code == 201
    meeting_id = created.json()["id"]
    upload = client.post(f"/v1/meetings/{meeting_id}/upload-sessions", headers=auth_headers)
    assert upload.status_code == 201
    assert upload.json()["upload_url"].startswith("memory://")
    complete = client.post(f"/v1/meetings/{meeting_id}/complete", headers=auth_headers)
    assert complete.status_code == 200
    assert client.get(f"/v1/meetings/{meeting_id}", headers=auth_headers).status_code == 200

    transcript = client.get(
        "/v1/meetings/seed-m1/transcript?limit=1&offset=0", headers=auth_headers
    )
    assert transcript.status_code == 200
    assert len(transcript.json()["items"]) == 1
    assert transcript.json()["total"] >= 1


def test_query_contract_and_voice_query(client, auth_headers) -> None:
    payload = {
        "question": "What changed in the ingestion pipeline?",
        "ui_locale": "it",
        "filters": {"project": "NODO"},
    }
    response = client.post("/v1/query", headers=auth_headers, json=payload)
    body = response.json()
    assert response.status_code == 200
    assert set(body) == {
        "answer",
        "answerability",
        "citations",
        "related_entities",
        "query_trace_id",
        "model_version",
        "retrieval_version",
        "degraded",
    }
    assert body["answerability"] == "supported"
    voice = client.post(
        "/v1/voice-query",
        headers=auth_headers,
        json={"transcript_text": payload["question"], "ui_locale": "da"},
    )
    assert voice.status_code == 200
    assert voice.json()["answerability"] == "supported"


def test_review_graph_export_and_delete(client, auth_headers) -> None:
    review = client.patch(
        "/v1/meetings/seed-m1/review",
        headers=auth_headers,
        json={"corrections": [{"segment_id": "seg-001", "text": "Updated text"}]},
    )
    assert review.status_code == 200
    assert review.json()["version"] == 2
    graph = client.get("/v1/graph/subgraph?entity=ingestion&depth=2", headers=auth_headers)
    assert graph.status_code == 200
    assert graph.json()["nodes"]
    export = client.post("/v1/export", headers=auth_headers, json={})
    assert export.status_code == 202
    assert export.json()["status"] == "queued"
    assert client.delete("/v1/meetings/seed-m1", headers=auth_headers).status_code == 204


def test_acl_negative_for_all_sensitive_endpoints(app, client, auth_headers) -> None:
    app.state.container.auth.add_dev_token("workspace-b-token", "user-b", "workspace-b")
    workspace_b = {"Authorization": "Bearer workspace-b-token"}
    assert client.get("/v1/meetings/seed-m1", headers=workspace_b).status_code == 404
    assert client.get("/v1/meetings/seed-m1/transcript", headers=workspace_b).status_code == 404
    assert client.delete("/v1/meetings/seed-m1", headers=workspace_b).status_code == 404
    query = client.post(
        "/v1/query",
        headers=workspace_b,
        json={"question": "ingestion", "ui_locale": "en"},
    ).json()
    assert query["citations"] == []
    assert query["related_entities"] == []
    export = client.post("/v1/export", headers=workspace_b, json={"meeting_id": "seed-m1"})
    assert export.status_code == 404
    assert client.get("/v1/meetings/seed-m1", headers=auth_headers).status_code == 200


def test_uniform_errors_and_request_id(client, auth_headers) -> None:
    response = client.get(
        "/v1/meetings/does-not-exist",
        headers={**auth_headers, "X-Request-ID": "request-123"},
    )
    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "request-123"
    assert response.json() == {
        "error": {"code": "not_found", "message": "Resource not found", "request_id": "request-123"}
    }
    unauthorized = client.get("/v1/meetings/seed-m1")
    assert unauthorized.status_code == 401
    assert "error" in unauthorized.json()
