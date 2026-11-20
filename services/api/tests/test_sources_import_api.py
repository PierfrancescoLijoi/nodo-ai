from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nodo_api.config import Settings
from nodo_api.main import create_app
from tests.test_source_import import doc


def _create(client, headers, *, key="source-key-1", filename="plan.md", size=4):
    return client.post(
        "/v1/sources",
        headers={**headers, "Idempotency-Key": key},
        json={"filename": filename, "size_bytes": size, "origin_note": "team notes"},
    )


def _upload_memory(app, created: dict, body: bytes, parsed=None, client=None) -> str:
    container = app.state.container
    source_id = created["source"]["id"]
    token = created["upload_url"].rsplit("/", 1)[-1]
    if client is None:
        with container.source_blobs.open_upload(token) as writer:
            writer.write(body)
    else:
        assert client.put(created["upload_url"], content=body).status_code == 204
    if parsed is not None:
        path = container.source_blobs.path_for("workspace-a", source_id, 1)
        container.source_import._parser.register(path, parsed)
    return source_id


def test_create_upload_complete_then_status_reaches_ready(tmp_path) -> None:
    from nodo_api.adapters.local.fragment_index import SqliteFragmentIndex
    from tests.test_local_adapters import HashEmbedder

    app, client, headers = _local_client(tmp_path, max_bytes=1000)
    try:
        container = app.state.container
        # Exercise the real HTTP upload, queue, worker and subprocess parser without requiring the
        # optional embedding or language models on the test machine.
        container.source_import._index = SqliteFragmentIndex(container.runtime.db, HashEmbedder())
        container.source_import._version = None
        content = b"Queue ingestion plan.\n"
        created = _create(
            client, headers, filename="plan.txt", size=len(content), key="source-local-e2e"
        )
        assert created.status_code == 201
        body = created.json()
        assert set(body) == {"source", "upload_url", "expires_in_seconds"}
        assert "workspace_id" not in body["source"] and "owner_id" not in body["source"]
        source_id = body["source"]["id"]
        assert client.put(body["upload_url"], content=content).status_code == 204

        completed = client.post(f"/v1/sources/{source_id}/complete", headers=headers)
        assert completed.status_code == 202
        assert completed.json()["import"]["state"] == "queued"
        assert container.runtime.loop.run_pending() == 1

        imported = client.get(f"/v1/sources/{source_id}/import", headers=headers)
        versions = client.get(f"/v1/sources/{source_id}/versions", headers=headers)
        assert imported.status_code == versions.status_code == 200
        assert imported.json()["import"]["state"] == "ready"
        assert versions.json()["items"][0]["version"] == 1
        assert set(versions.json()["items"][0]) == {
            "version",
            "content_hash_prefix",
            "parser",
            "created_at",
            "size_bytes",
        }
    finally:
        client.close()
        app.state.container.runtime.db.close()


def test_second_import_of_the_same_bytes_returns_unchanged_and_the_first_source(
    app, client, auth_headers
) -> None:
    first = _create(client, auth_headers, key="source-key-a").json()
    first_id = _upload_memory(app, first, b"same", doc(("A", ["same text"])), client)
    client.post(f"/v1/sources/{first_id}/complete", headers=auth_headers)
    app.state.container.source_import.process("workspace-a", first_id)

    second = _create(client, auth_headers, key="source-key-b").json()
    second_id = _upload_memory(app, second, b"same")
    response = client.post(f"/v1/sources/{second_id}/complete", headers=auth_headers)

    assert response.status_code == 202
    assert response.json()["import"]["unchanged"] is True
    assert response.json()["import"]["existing_source_id"] == first_id
    assert client.get(f"/v1/sources/{second_id}", headers=auth_headers).status_code == 404


def test_idempotency_key_with_a_different_body_is_409(client, auth_headers) -> None:
    assert _create(client, auth_headers, filename="first.md").status_code == 201
    response = _create(client, auth_headers, filename="second.md")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_other_users_import_status_is_404(app, client, auth_headers) -> None:
    source_id = _create(client, auth_headers).json()["source"]["id"]
    app.state.container.auth.add_dev_token("bob-token-0123456789", "user-b", "workspace-a")
    response = client.get(
        f"/v1/sources/{source_id}/import",
        headers={"Authorization": "Bearer bob-token-0123456789"},
    )
    assert response.status_code == 404


def _local_client(
    tmp_path: Path, *, max_bytes: int = 4
) -> tuple[object, TestClient, dict[str, str]]:
    token = "local-test-token-0123456789-0123456789"
    app = create_app(
        Settings(
            environment="prod",
            backend="local",
            auth_mode="local",
            local_token=token,
            local_data_dir=tmp_path,
            local_import_max_bytes=max_bytes,
            attestation_hmac_key="local-test-attestation-key-0123456789",
        )
    )
    return app, TestClient(app), {"Authorization": f"Bearer {token}"}


def test_upload_over_the_cap_is_413(tmp_path) -> None:
    app, client, headers = _local_client(tmp_path)
    try:
        accepted = _create(client, headers, key="source-local-ok").json()
        assert client.put(accepted["upload_url"], content=b"1234").status_code == 204

        created = _create(client, headers, key="source-local-big").json()
        response = client.put(created["upload_url"], content=b"12345")
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"
    finally:
        client.close()
        app.state.container.runtime.db.close()


def test_low_disk_is_507(tmp_path) -> None:
    app, client, headers = _local_client(tmp_path)
    try:
        app.state.container.source_blobs._free_space = lambda _: 0
        response = _create(client, headers, key="source-low-disk")
        assert response.status_code == 507
    finally:
        client.close()
        app.state.container.runtime.db.close()


def test_reserved_filename_is_422(client, auth_headers) -> None:
    response = _create(client, auth_headers, filename="CON.md")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_cancel_and_retry_transitions(app, client, auth_headers) -> None:
    created = _create(client, auth_headers).json()
    source_id = _upload_memory(app, created, b"plan")
    client.post(f"/v1/sources/{source_id}/complete", headers=auth_headers)

    cancelled = client.post(f"/v1/sources/{source_id}/cancel", headers=auth_headers)
    retried = client.post(f"/v1/sources/{source_id}/retry", headers=auth_headers)
    assert cancelled.status_code == retried.status_code == 202
    assert cancelled.json()["import"]["state"] == "cancelled"
    assert retried.json()["import"]["state"] == "queued"


def test_delete_source_returns_204_and_it_disappears_from_the_list(client, auth_headers) -> None:
    source_id = _create(client, auth_headers).json()["source"]["id"]
    response = client.delete(f"/v1/sources/{source_id}", headers=auth_headers)
    assert response.status_code == 204
    assert source_id not in {
        item["id"] for item in client.get("/v1/sources", headers=auth_headers).json()["items"]
    }


def test_delete_meeting_through_sources_is_422_with_guidance(client, auth_headers) -> None:
    response = client.delete("/v1/sources/seed-m1", headers=auth_headers)
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "use the meetings API"


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("post", "/v1/sources"),
        ("post", "/v1/sources/anything/complete"),
        ("get", "/v1/sources/anything/import"),
        ("post", "/v1/sources/anything/cancel"),
        ("post", "/v1/sources/anything/retry"),
        ("get", "/v1/sources/anything/versions"),
        ("delete", "/v1/sources/anything"),
        ("put", "/v1/local/source-uploads/anything"),
    ),
)
def test_azure_engine_answers_501_for_every_new_route(
    app, client, auth_headers, method, path
) -> None:
    app.state.container.source_import = None
    app.state.container.source_service = None
    app.state.container.source_blobs = None
    response = client.request(
        method,
        path,
        headers={**auth_headers, "Idempotency-Key": "source-key-1"},
        json={"filename": "plan.md", "size_bytes": 4} if path == "/v1/sources" else None,
    )
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_available_on_engine"


def test_source_list_filters_state_and_title(app, client, auth_headers) -> None:
    one = _create(client, auth_headers, key="source-filter-a", filename="Alpha.md").json()
    two = _create(client, auth_headers, key="source-filter-b", filename="Beta.md").json()
    _upload_memory(app, two, b"beta")
    client.post(f"/v1/sources/{two['source']['id']}/complete", headers=auth_headers)

    queued = client.get("/v1/sources?state=queued&q=alpha", headers=auth_headers).json()
    assert [item["id"] for item in queued["items"]] == [one["source"]["id"]]


def test_openapi_differs_from_main_by_additions_only(app) -> None:
    contract = json.loads(
        (
            Path(__file__).resolve().parents[3] / "packages" / "contracts" / "openapi.json"
        ).read_text()
    )
    assert contract == app.openapi()
    assert "/v1/sources/{source_id}/versions" in contract["paths"]
    assert "/v1/local/source-uploads/{token}" in contract["paths"]


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("post", "/v1/sources"),
        ("post", "/v1/sources/anything/complete"),
        ("get", "/v1/sources/anything/import"),
        ("post", "/v1/sources/anything/cancel"),
        ("post", "/v1/sources/anything/retry"),
        ("get", "/v1/sources/anything/versions"),
        ("delete", "/v1/sources/anything"),
    ),
)
def test_import_routes_require_authentication(client, method, path) -> None:
    response = client.request(
        method,
        path,
        headers={"Idempotency-Key": "source-key-1"},
        json={"filename": "plan.md", "size_bytes": 4} if path == "/v1/sources" else None,
    )
    assert response.status_code == 401


# --------------------------------------------------------------------------- Task 13 review


def _missing(before, after, where="#"):
    """Every place where `after` drops or changes something `before` had (additions are fine)."""
    if isinstance(before, dict):
        if not isinstance(after, dict):
            yield where
            return
        for key, value in before.items():
            if key not in after:
                yield f"{where}/{key}"
            else:
                yield from _missing(value, after[key], f"{where}/{key}")
    elif isinstance(before, list):
        if not isinstance(after, list):
            yield where
            return
        for item in before:
            if item not in after and not any(
                not list(_missing(item, other)) for other in after if isinstance(item, dict)
            ):
                yield f"{where}[{json.dumps(item, sort_keys=True)[:60]}]"
    elif before != after:
        yield where


def test_openapi_keeps_everything_the_contract_had_before_the_library_api(app) -> None:
    before = json.loads(
        (Path(__file__).parent / "openapi_before_sources_api.json").read_text(encoding="utf-8")
    )
    after = app.openapi()
    assert list(_missing(before, after)) == []
    # the check itself notices a removed field, a changed type and a removed list entry
    schemas = after["components"]["schemas"]
    broken = json.loads(json.dumps(after))
    del broken["components"]["schemas"]["SourceOutput"]["properties"]["title"]
    assert "#/components/schemas/SourceOutput/properties/title" in list(_missing(before, broken))
    retyped = json.loads(json.dumps(after))
    retyped["components"]["schemas"]["SourceOutput"]["properties"]["version"] = {"type": "string"}
    assert list(_missing(before, retyped))
    trimmed = json.loads(json.dumps(after))
    trimmed["paths"]["/v1/sources"]["get"]["parameters"].pop(0)
    assert list(_missing(before, trimmed))
    assert "SourceOutput" in schemas


OTHERS = (
    ("bob-token-0123456789", "user-b", "workspace-a"),
    ("carol-token-0123456789", "user-a", "workspace-b"),  # the same user id elsewhere
)


@pytest.mark.parametrize(("token", "user", "workspace"), OTHERS)
@pytest.mark.parametrize(
    ("method", "suffix"),
    (
        ("post", "/complete"),
        ("get", "/import"),
        ("post", "/cancel"),
        ("post", "/retry"),
        ("get", "/versions"),
        ("delete", ""),
        ("get", ""),
    ),
)
def test_every_source_route_is_404_for_another_owner_or_workspace(
    app, client, auth_headers, token, user, workspace, method, suffix
) -> None:
    created = _create(client, auth_headers).json()
    source_id = _upload_memory(app, created, b"plan", doc(("A", ["plan text"])), client)
    client.post(f"/v1/sources/{source_id}/complete", headers=auth_headers)
    app.state.container.auth.add_dev_token(token, user, workspace)
    response = client.request(
        method, f"/v1/sources/{source_id}{suffix}", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404
    assert client.get(f"/v1/sources/{source_id}", headers=auth_headers).status_code == 200


def test_versions_of_a_meeting_id_are_404_like_the_other_import_routes(
    client, auth_headers
) -> None:
    for suffix in ("/versions", "/import", "/complete", "/cancel", "/retry"):
        method = "get" if suffix in ("/versions", "/import") else "post"
        response = client.request(method, f"/v1/sources/seed-m1{suffix}", headers=auth_headers)
        assert response.status_code == 404, suffix


def test_an_upload_link_works_once_and_not_after_it_expires(app, client, auth_headers) -> None:
    from datetime import UTC, datetime, timedelta

    used = _create(client, auth_headers, key="source-key-used").json()
    assert client.put(used["upload_url"], content=b"plan").status_code == 204
    assert client.put(used["upload_url"], content=b"plan").status_code == 403

    stale = _create(client, auth_headers, key="source-key-stale").json()
    blobs = app.state.container.source_blobs
    later = datetime.now(UTC) + timedelta(seconds=stale["expires_in_seconds"] + 1)
    blobs._now = lambda: later
    assert client.put(stale["upload_url"], content=b"plan").status_code == 403
    source_id = stale["source"]["id"]
    assert not blobs.path_for("workspace-a", source_id, 1).exists()


def test_chunked_upload_is_accepted_and_a_chunked_body_over_the_size_is_413(
    client, auth_headers
) -> None:
    def chunks(*parts: bytes):
        yield from parts

    fits = _create(client, auth_headers, key="source-key-chunks").json()
    assert client.put(fits["upload_url"], content=chunks(b"pl", b"an")).status_code == 204
    over = _create(client, auth_headers, key="source-key-chunks-over").json()
    response = client.put(over["upload_url"], content=chunks(b"pla", b"n!"))
    assert response.status_code == 413


def test_a_short_body_is_400_and_the_link_can_be_used_again(client, auth_headers) -> None:
    created = _create(client, auth_headers).json()
    response = client.put(created["upload_url"], content=b"pl")
    assert response.status_code == 400
    assert client.put(created["upload_url"], content=b"plan").status_code == 204


def test_double_complete_answers_the_same_import(app, client, auth_headers) -> None:
    created = _create(client, auth_headers).json()
    source_id = _upload_memory(app, created, b"plan", doc(("A", ["plan text"])), client)
    first = client.post(f"/v1/sources/{source_id}/complete", headers=auth_headers)
    second = client.post(f"/v1/sources/{source_id}/complete", headers=auth_headers)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    app.state.container.source_import.process("workspace-a", source_id)
    third = client.post(f"/v1/sources/{source_id}/complete", headers=auth_headers)
    assert third.json()["import"]["state"] == "ready"


def test_a_put_after_cancel_is_refused_and_leaves_no_file(app, client, auth_headers) -> None:
    created = _create(client, auth_headers).json()
    source_id = created["source"]["id"]
    cancelled = client.post(f"/v1/sources/{source_id}/cancel", headers=auth_headers)
    assert cancelled.json()["import"]["state"] == "cancelled"
    assert client.put(created["upload_url"], content=b"plan").status_code == 403
    assert not app.state.container.source_blobs.path_for("workspace-a", source_id, 1).exists()
    assert _create(client, auth_headers).status_code == 409  # no new link by replay either


def test_a_replayed_link_can_not_replace_the_hashed_original(app, client, auth_headers) -> None:
    first = _create(client, auth_headers).json()
    second = _create(client, auth_headers).json()  # documented replay: a second link
    source_id = _upload_memory(app, first, b"plan", doc(("A", ["plan text"])), client)
    assert _create(client, auth_headers).status_code == 409  # bytes stored: no third link
    client.post(f"/v1/sources/{source_id}/complete", headers=auth_headers)
    assert client.put(second["upload_url"], content=b"evil").status_code == 403
    app.state.container.source_import.process("workspace-a", source_id)
    status = client.get(f"/v1/sources/{source_id}/import", headers=auth_headers).json()
    assert status["import"]["state"] == "ready"
    stored = app.state.container.source_blobs.path_for("workspace-a", source_id, 1)
    assert stored.read_bytes() == b"plan"


def test_too_many_open_links_on_create_is_409(app, client, auth_headers) -> None:
    app.state.container.source_blobs._max_links = 0
    response = _create(client, auth_headers)
    assert response.status_code == 409
    assert client.get("/v1/sources", headers=auth_headers).json()["items"][0]["kind"] == "meeting"


def test_upload_tokens_never_reach_logs_or_spans(tmp_path, caplog) -> None:
    import logging

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from nodo_api.telemetry import instrument_app

    exporter = InMemorySpanExporter()
    provider = TracerProvider()  # its own: the global provider is set once per process
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    caplog.set_level(logging.DEBUG)
    app, client, headers = _local_client(tmp_path)
    FastAPIInstrumentor.uninstrument_app(app)
    instrument_app(app, tracer_provider=provider)  # the app's own setup, on the test provider
    app.middleware_stack = None  # uninstrument built the stack: build it again, instrumented
    try:
        created = _create(client, headers, key="source-log-token").json()
        token = created["upload_url"].rsplit("/", 1)[-1]
        meeting_token = "m" * 43
        assert client.put(created["upload_url"], content=b"12").status_code == 400
        assert client.put(f"/v1/local/uploads/{meeting_token}", content=b"x").status_code == 403
    finally:
        client.close()
        app.state.container.runtime.db.close()
    # the test client logs the URLs it calls; the server must not
    server = [r for r in caplog.records if not r.name.startswith(("httpx", "httpcore"))]
    text = "\n".join(r.getMessage() for r in server)
    assert '"path": "/v1/local/source-uploads/{token}"' in text
    assert '"path": "/v1/local/uploads/{token}"' in text
    spans = [s for s in exporter.get_finished_spans() if s.kind.name == "SERVER"]
    assert any("source-uploads" in json.dumps(dict(s.attributes)) for s in spans)
    recorded = text + json.dumps([[s.name, dict(s.attributes)] for s in spans], default=str)
    for secret in (token, meeting_token):
        assert secret not in recorded
