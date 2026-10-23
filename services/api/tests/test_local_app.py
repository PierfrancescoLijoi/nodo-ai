"""The local backend behind the real HTTP API: record, process, name speakers, ask, delete."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from nodo_api.adapters.memory import MemorySpeech
from nodo_api.adapters.memory.extractor import KeywordExtractor
from nodo_api.config import Settings
from nodo_api.errors import ModelUnavailable
from nodo_api.main import create_app
from nodo_api.services.ingestion import IngestionService
from nodo_api.worker import Worker
from tests.test_local_adapters import HashEmbedder

TOKEN = "local-test-token-0123456789-0123456789"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
ATTESTATION = {
    "notice_version": "2026-10-v1",
    "locale": "en",
    "retention_policy_id": "retention-30d",
    "acknowledged": True,
}


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "environment": "prod",
        "backend": "local",
        "auth_mode": "local",
        "local_token": TOKEN,
        "local_data_dir": tmp_path,
        "attestation_hmac_key": "local-test-attestation-key-0123456789",
    }
    return Settings(**{**values, **overrides})


@pytest.fixture
def app(tmp_path):
    application = create_app(_settings(tmp_path))
    container = application.state.container
    container.retriever._embedder = HashEmbedder()  # no model download in a unit test
    ingestion = IngestionService(
        container.meetings,
        container.transcripts,
        container.blobs,
        container.retriever,
        MemorySpeech(),
        ("en-GB",),
        KeywordExtractor(),
        container.graph,
    )
    container.runtime.loop._worker = Worker(ingestion)
    container.runtime.start = lambda: None  # tests drive the queue by hand, not from a thread
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def _record(client) -> str:
    created = client.post(
        "/v1/meetings",
        headers={**HEADERS, "Idempotency-Key": "local-key-0001"},
        json={"recording_attestation": ATTESTATION, "title": "Planning"},
    )
    assert created.status_code == 201, created.text
    meeting_id = created.json()["id"]
    session = client.post(f"/v1/meetings/{meeting_id}/upload-sessions", headers=HEADERS).json()
    path = "/" + session["upload_url"].split("/", 3)[3]
    assert client.put(path, content=b"fake audio bytes").status_code == 204
    assert client.post(f"/v1/meetings/{meeting_id}/complete", headers=HEADERS).status_code == 200
    return meeting_id


def test_a_recording_goes_from_upload_to_a_reviewable_meeting(app, client) -> None:
    meeting_id = _record(client)
    assert app.state.container.runtime.loop.run_pending() == 1
    meeting = client.get(f"/v1/meetings/{meeting_id}", headers=HEADERS).json()
    assert meeting["state"] == "review" and meeting["title"] == "Planning"
    assert [s["id"] for s in meeting["speakers"]] == ["S1", "S2"]
    transcript = client.get(f"/v1/meetings/{meeting_id}/transcript", headers=HEADERS).json()
    assert transcript["total"] == 3
    listed = client.get("/v1/meetings", headers=HEADERS).json()["items"]
    assert [m["id"] for m in listed] == [meeting_id]


def test_naming_a_speaker_reaches_the_graph_through_the_local_queue(app, client) -> None:
    meeting_id = _record(client)
    loop = app.state.container.runtime.loop
    loop.run_pending()
    named = client.patch(
        f"/v1/meetings/{meeting_id}/speakers/S2", headers=HEADERS, json={"name": "Zuzanna"}
    )
    assert named.status_code == 200
    assert loop.run_pending() == 1
    people = client.get(f"/v1/meetings/{meeting_id}", headers=HEADERS).json()["insights"]["people"]
    assert "Zuzanna" in [p["label"] for p in people]


def test_search_and_graph_are_filled_by_the_pipeline(app, client) -> None:
    _record(client)
    app.state.container.runtime.loop.run_pending()
    assert client.get("/v1/graph/subgraph?depth=1", headers=HEADERS).json()["nodes"]
    hits = app.state.container.retriever.search("local", "local-user", "ingestion queue", None, 5)
    assert hits


def test_a_question_without_an_installed_model_says_so_instead_of_failing(app, client) -> None:
    class MissingModel:
        def answer(self, *args, **kwargs):
            raise ModelUnavailable

    app.state.container.query_service._llm = MissingModel()
    reply = client.post(
        "/v1/query", headers=HEADERS, json={"question": "What changed?", "ui_locale": "en"}
    )
    assert reply.status_code == 503 and reply.json()["error"]["code"] == "model_unavailable"


def test_deleting_a_meeting_erases_audio_text_search_and_graph(app, client) -> None:
    meeting_id = _record(client)
    app.state.container.runtime.loop.run_pending()
    blobs = app.state.container.blobs
    assert not blobs.was_erased("local", meeting_id)
    assert client.delete(f"/v1/meetings/{meeting_id}", headers=HEADERS).status_code == 204
    assert blobs.was_erased("local", meeting_id)
    assert app.state.container.retriever.search("local", "local-user", "ingestion", None, 5) == ()
    assert client.get("/v1/graph/subgraph?depth=2", headers=HEADERS).json()["nodes"] == []


def test_the_upload_link_is_single_use_and_everything_else_needs_the_token(client) -> None:
    created = client.post(
        "/v1/meetings",
        headers={**HEADERS, "Idempotency-Key": "local-key-0002"},
        json={"recording_attestation": ATTESTATION},
    ).json()
    session = client.post(f"/v1/meetings/{created['id']}/upload-sessions", headers=HEADERS).json()
    path = "/" + session["upload_url"].split("/", 3)[3]
    assert client.put(path, content=b"x").status_code == 204
    assert client.put(path, content=b"y").status_code == 403
    assert client.put("/v1/local/uploads/nope", content=b"y").status_code == 403
    assert client.get("/v1/meetings").status_code == 401
    assert client.get("/v1/meetings", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/local/status", headers=HEADERS).json()["backend"] == "local"


@pytest.mark.parametrize(
    "overrides",
    [
        {"local_token": None},
        {"local_token": "short"},
        {"local_public_url": "http://example.com:8765"},
        {"backend": "memory"},
    ],
)
def test_the_local_mode_refuses_unsafe_settings(tmp_path, overrides) -> None:
    with pytest.raises(ValueError):
        _settings(tmp_path, **overrides)


def test_the_web_app_is_served_with_a_single_page_fallback(tmp_path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>NODO</title>", encoding="utf-8")
    (web / "app.js").write_text("console.log(1)", encoding="utf-8")
    settings = _settings(tmp_path / "data", local_web_dir=web)
    with TestClient(create_app(settings)) as client:
        assert "NODO" in client.get("/").text
        assert "NODO" in client.get("/meetings/some-route").text  # a client side route
        assert client.get("/app.js").status_code == 200
        assert client.get("/missing.png").status_code == 404
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/v1/meetings").status_code == 401  # the API still wins over the files


def test_requests_for_a_foreign_host_name_are_refused(client) -> None:
    reply = client.get("/healthz", headers={"Host": "evil.example.com:8765"})
    assert reply.status_code == 421
    assert client.get("/healthz", headers={"Host": "127.0.0.1:8765"}).status_code == 200


def _wait_for_index_run(client, timeout: float = 10.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    while True:
        progress = client.get("/v1/local/index", headers=HEADERS).json()
        if progress["state"] != "running" or time.monotonic() > deadline:
            return progress
        time.sleep(0.02)


def test_the_archive_can_check_and_rebuild_its_own_search_index(app, client) -> None:
    meeting_id = _record(client)
    app.state.container.runtime.loop.run_pending()
    retriever = app.state.container.retriever
    idle = client.get("/v1/local/index", headers=HEADERS).json()
    assert idle["state"] == "idle"

    assert (
        client.post("/v1/local/index", headers=HEADERS, json={"mode": "verify"}).status_code == 202
    )
    clean = _wait_for_index_run(client)
    assert (clean["state"], clean["findings"], clean["failed"]) == ("done", [], [])
    assert clean["checked"] == 1

    retriever.erase("local", meeting_id)  # the index is lost; the archive is not
    client.post("/v1/local/index", headers=HEADERS, json={"mode": "verify"})
    found = _wait_for_index_run(client)
    assert found["findings"] == [{"kind": "missing_from_index", "meeting_id": meeting_id}]
    assert retriever.count("local", meeting_id) == 0  # verify changes nothing

    client.post("/v1/local/index", headers=HEADERS, json={"mode": "repair"})
    repaired = _wait_for_index_run(client)
    assert (repaired["state"], repaired["repaired"], repaired["failed"]) == ("done", 1, [])
    assert retriever.count("local", meeting_id) == 3
    assert retriever.search("local", "local-user", "ingestion queue", None, 5)


def test_the_index_routes_need_the_token_and_a_known_mode(client) -> None:
    assert client.post("/v1/local/index", json={"mode": "verify"}).status_code == 401
    assert client.get("/v1/local/index").status_code == 401
    assert client.post("/v1/local/index", headers=HEADERS, json={"mode": "nuke"}).status_code == 422
