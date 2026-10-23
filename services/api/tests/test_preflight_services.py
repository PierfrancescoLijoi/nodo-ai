"""Preflight checks that talk to storage, the queue, search, speech, the models and the edge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nodo_api.preflight import Status
from tests.test_preflight import context, result


class FakeHttp:
    def __init__(self, statuses=None) -> None:
        self.store: dict[str, bytes] = {}
        self.statuses = statuses or {}

    def put(self, url, content=None, headers=None):
        assert headers == {"x-ms-blob-type": "BlockBlob"}
        self.store[url.split("?")[0]] = content
        return SimpleNamespace(status_code=self.statuses.get("put", 201))

    def get(self, url, params=None, headers=None):
        if "speechtotext" in url:
            return SimpleNamespace(status_code=self.statuses.get("speech", 200))
        if url.endswith("/readyz"):
            return SimpleNamespace(status_code=self.statuses.get("ready", 200))
        if "?" not in url:  # no link at all
            return SimpleNamespace(status_code=self.statuses.get("bare", 404), content=b"")
        base = url.split("?")[0]
        if base in self.store:
            return SimpleNamespace(
                status_code=self.statuses.get("get", 200), content=self.store[base]
            )
        return SimpleNamespace(status_code=404, content=b"")


class FakeBlobs:
    def __init__(self) -> None:
        self.erased: list[str] = []

    def create_upload_session(self, ws, meeting):
        return {"upload_url": f"https://stnodo.blob.core.windows.net/audio/{meeting}?sig=W"}

    def create_read_url(self, ws, meeting):
        return f"https://stnodo.blob.core.windows.net/audio/{meeting}?sig=R"

    def erase(self, ws, meeting):
        self.erased.append(meeting)


def test_blob_links_are_proved_and_the_test_object_is_always_removed() -> None:
    blobs = FakeBlobs()
    done = result(context(blobs=blobs, http=FakeHttp()), "blob_roundtrip")
    assert done.status is Status.OK and len(blobs.erased) == 1

    bad = FakeBlobs()
    failed = result(context(blobs=bad, http=FakeHttp({"put": 403})), "blob_roundtrip")
    assert failed.status is Status.FAIL and "403" in failed.detail and len(bad.erased) == 1

    public = FakeBlobs()
    leak = result(context(blobs=public, http=FakeHttp({"bare": 200})), "blob_roundtrip")
    assert leak.status is Status.FAIL and "anonymous" in leak.detail


def test_the_queue_is_proved_without_ever_delivering_a_message() -> None:
    events: list[str] = []

    class Sender:
        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def schedule_messages(self, message, when):
            events.append("schedule")
            assert when > datetime.now(UTC) + timedelta(minutes=50)
            return [7]

        def cancel_scheduled_messages(self, numbers):
            events.append(f"cancel{numbers}")

    class Receiver(Sender):
        def peek_messages(self, max_message_count):
            events.append("peek")
            return []

    class Bus:
        def get_queue_sender(self, queue):
            return Sender()

        def get_queue_receiver(self, queue, max_wait_time):
            return Receiver()

    bus = Bus()
    assert result(context(servicebus=bus), "servicebus_send").status is Status.OK
    assert result(context(servicebus=bus, role="worker"), "servicebus_receive").status is Status.OK
    assert events == ["schedule", "cancel[7]", "peek"]


def test_the_index_must_have_the_fields_the_dimension_and_the_analyzers_the_code_expects() -> None:
    from nodo_api.adapters.azure.search import build_index

    good = SimpleNamespace(get_index=lambda name: build_index(name, 3))
    assert result(context(search_index=good), "search_index").status is Status.OK
    wrong_size = SimpleNamespace(get_index=lambda name: build_index(name, 1536))
    failed = result(context(search_index=wrong_size), "search_index")
    assert failed.status is Status.FAIL and "1536" in failed.detail

    thin = build_index("x", 3)
    thin.fields = [f for f in thin.fields if f.name != "owner_id"]
    missing = result(
        context(search_index=SimpleNamespace(get_index=lambda n: thin)), "search_index"
    )
    assert "owner_id" in missing.detail


class FakeRetriever:
    def __init__(self, keeps: bool = False) -> None:
        self.docs: dict[str, object] = {}
        self.keeps = keeps

    def index(self, ws, owner, segments):
        for s in segments:
            self.docs[s.meeting_id] = s

    def count(self, ws, meeting):
        return 1 if meeting in self.docs else 0

    def search(self, ws, owner, query, filters, limit):
        return tuple(self.docs.values())

    def erase(self, ws, meeting):
        if not self.keeps:
            self.docs.pop(meeting, None)


def test_search_is_proved_by_writing_finding_and_removing_a_document(monkeypatch) -> None:
    monkeypatch.setattr("nodo_api.preflight.time.sleep", lambda s: None)
    assert result(context(retriever=FakeRetriever()), "search_roundtrip").status is Status.OK
    monkeypatch.setattr("nodo_api.preflight.PROPAGATION_SECONDS", 0)
    stuck = result(context(retriever=FakeRetriever(keeps=True)), "search_roundtrip")
    assert stuck.status is Status.FAIL and "not removed" in stuck.detail


def test_speech_and_the_public_edge_are_asked_with_the_right_credentials() -> None:
    http = FakeHttp()
    assert result(context(role="worker", http=http), "speech").status is Status.OK
    denied = result(context(role="worker", http=FakeHttp({"speech": 403})), "speech")
    assert denied.status is Status.FAIL and "403" in denied.detail

    assert result(context(http=http), "gateway").status is Status.SKIP  # no public edge: basic tier
    assert result(context(http=http, public_host="api.example.com"), "gateway").status is Status.OK
    down = result(context(http=FakeHttp({"ready": 502}), public_host="api.example.com"), "gateway")
    assert down.status is Status.FAIL and "502" in down.detail


def test_the_chat_checks_use_the_production_adapters_and_demand_grounded_output() -> None:
    from nodo_api.domain.models import Citation

    grounded = SimpleNamespace(
        answer=lambda *a, **k: SimpleNamespace(citations=(Citation("m", "seg-1", 0, 1, "q"),))
    )
    assert result(context(llm=grounded), "model_answer").status is Status.OK
    refusing = SimpleNamespace(answer=lambda *a, **k: SimpleNamespace(citations=()))
    assert result(context(llm=refusing), "model_answer").status is Status.FAIL

    extractor = SimpleNamespace(extract=lambda *a, **k: SimpleNamespace(entities=("a", "b")))
    assert (
        result(context(role="worker", extractor=extractor), "model_extraction").status is Status.OK
    )


def test_the_admin_command_prints_the_report_and_fails_the_job_when_a_check_fails(
    monkeypatch, capsys
) -> None:
    from nodo_api import admin, preflight
    from nodo_api.config import Settings

    results = [
        preflight.CheckResult("identity", Status.OK, "fine", "", 3),
        preflight.CheckResult("blob_roundtrip", Status.FAIL, "403", "check the role", 5),
    ]
    monkeypatch.setattr(
        preflight, "build_context", lambda settings, role: SimpleNamespace(role=role)
    )
    monkeypatch.setattr(preflight, "run_checks", lambda ctx: results)
    settings = Settings(
        environment="prod",
        backend="azure",
        oidc_issuer="https://i.example/",
        oidc_audience="a",
        attestation_hmac_key="k" * 40,
        azure_storage_account="s",
        azure_servicebus_fqdn="b.servicebus.windows.net",
        azure_postgres_host="p.postgres.database.azure.com",
        azure_postgres_user="u",
        azure_search_endpoint="https://x.search.windows.net",
        azure_openai_endpoint="https://o.openai.azure.com/",
        azure_speech_endpoint="https://sp.cognitiveservices.azure.com/",
    )
    with pytest.raises(SystemExit) as stop:
        admin.run("preflight", settings, "api")
    assert stop.value.code == 1
    assert "blob_roundtrip" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        admin.run("preflight", settings, "nobody")
