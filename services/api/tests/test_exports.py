"""Handing a person their data back: request, build, download, and who may do what."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nodo_api.domain.exports import FORMAT, ExportState
from nodo_api.worker import Worker

WS, USER = "workspace-a", "user-a"


class Receiver:
    def __init__(self) -> None:
        self.outcome: list[str] = []

    def complete_message(self, m) -> None:
        self.outcome.append("complete")

    def abandon_message(self, m) -> None:
        self.outcome.append("abandon")

    def dead_letter_message(self, m, reason=None, error_description=None) -> None:
        self.outcome.append(f"dead_letter:{reason}")


def _run(app, export_id: str, deliveries: int = 1) -> Receiver:
    container = app.state.container
    worker = Worker(SimpleNamespace(mark_failed=lambda *a: None), exports=container.export_service)
    body = {"job_type": "export", "workspace_id": WS, "meeting_id": "", "export_id": export_id}
    message = SimpleNamespace(body=[json.dumps(body).encode()], delivery_count=deliveries)
    receiver = Receiver()
    worker.handle(receiver, message)
    return receiver


def _request(client, headers, meeting_id=None):
    reply = client.post("/v1/export", headers=headers, json={"meeting_id": meeting_id})
    assert reply.status_code == 202, reply.text
    return reply.json()


def test_an_export_goes_from_request_to_a_download_with_everything_in_it(
    app, client, auth_headers
) -> None:
    requested = _request(client, auth_headers)
    assert requested["status"] == "queued" and requested["download_path"] is None
    assert _run(app, requested["job_id"]).outcome == ["complete"]
    done = client.get(f"/v1/export/{requested['job_id']}", headers=auth_headers).json()
    assert done["status"] == "ready" and done["download_path"].endswith("/download")

    download = client.get(done["download_path"], headers=auth_headers)
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    document = download.json()
    assert document["format"] == FORMAT and document["owner"] == USER
    assert {m["id"] for m in document["meetings"]} == {"seed-m1", "seed-m2", "seed-m3"}
    first = next(m for m in document["meetings"] if m["id"] == "seed-m1")
    assert first["transcript_versions"][0]["segments"][0]["text"]
    assert first["recording_attestation"]["retention_policy_id"]
    assert "signature" not in first["recording_attestation"]  # an internal integrity value
    assert first["insights"]["decisions"], "what the graph holds about the meeting comes too"


def test_an_export_of_one_meeting_contains_only_that_meeting(app, client, auth_headers) -> None:
    requested = _request(client, auth_headers, "seed-m2")
    _run(app, requested["job_id"])
    document = client.get(f"/v1/export/{requested['job_id']}/download", headers=auth_headers).json()
    assert [m["id"] for m in document["meetings"]] == ["seed-m2"]


def test_a_second_run_of_the_same_job_changes_nothing(app, client, auth_headers) -> None:
    requested = _request(client, auth_headers)
    _run(app, requested["job_id"])
    first = client.get(f"/v1/export/{requested['job_id']}/download", headers=auth_headers).content
    assert _run(app, requested["job_id"]).outcome == ["complete"]
    assert (
        client.get(f"/v1/export/{requested['job_id']}/download", headers=auth_headers).content
        == first
    )


def test_an_export_that_is_not_ready_cannot_be_downloaded(client, auth_headers) -> None:
    requested = _request(client, auth_headers)
    assert (
        client.get(f"/v1/export/{requested['job_id']}/download", headers=auth_headers).status_code
        == 409
    )


def test_nobody_else_can_see_or_download_it(app, client, auth_headers) -> None:
    requested = _request(client, auth_headers)
    _run(app, requested["job_id"])
    app.state.container.auth.add_dev_token("bob-token-0123456789", "bob", WS)
    bob = {"Authorization": "Bearer bob-token-0123456789"}
    assert client.get(f"/v1/export/{requested['job_id']}", headers=bob).status_code == 404
    assert client.get(f"/v1/export/{requested['job_id']}/download", headers=bob).status_code == 404
    assert client.get(f"/v1/export/{requested['job_id']}").status_code == 401


def test_an_export_of_someone_elses_meeting_is_refused_at_the_request(app, client) -> None:
    app.state.container.auth.add_dev_token("bob-token-0123456789", "bob", WS)
    bob = {"Authorization": "Bearer bob-token-0123456789"}
    assert client.post("/v1/export", headers=bob, json={"meeting_id": "seed-m1"}).status_code == 404


def test_an_expired_export_is_gone(app, client, auth_headers) -> None:
    requested = _request(client, auth_headers)
    _run(app, requested["job_id"])
    store = app.state.container.export_service._exports
    item = store.get(WS, USER, requested["job_id"])
    store._items[(WS, item.id)] = replace(item, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert (
        client.get(f"/v1/export/{requested['job_id']}/download", headers=auth_headers).status_code
        == 404
    )


def test_the_requests_and_downloads_are_audited_by_id_only(app, client, auth_headers) -> None:
    log = []
    audit = app.state.container.export_service._audit
    original = audit.append
    audit.append = lambda ws, kind, meta: (log.append((kind, meta)), original(ws, kind, meta))[1]
    requested = _request(client, auth_headers)
    _run(app, requested["job_id"])
    client.get(f"/v1/export/{requested['job_id']}/download", headers=auth_headers)
    kinds = [k for k, _ in log]
    assert kinds == ["data_export_requested", "data_export_downloaded"]
    assert all(set(meta) == {"export_id"} for _, meta in log)


def test_a_job_that_keeps_failing_ends_as_failed_not_as_forever_queued(
    app, client, auth_headers
) -> None:
    requested = _request(client, auth_headers)
    app.state.container.export_service._blobs.put_export = lambda *a: (_ for _ in ()).throw(
        OSError("full")
    )
    assert _run(app, requested["job_id"], deliveries=1).outcome == ["abandon"]
    assert _run(app, requested["job_id"], deliveries=5).outcome == ["dead_letter:max_deliveries"]
    state = app.state.container.export_service._exports.get(WS, USER, requested["job_id"]).state
    assert state is ExportState.FAILED
    assert (
        client.get(f"/v1/export/{requested['job_id']}", headers=auth_headers).json()["status"]
        == "failed"
    )


def test_an_unknown_export_id_is_not_found(client, auth_headers) -> None:
    assert client.get("/v1/export/does-not-exist", headers=auth_headers).status_code == 404


@pytest.mark.parametrize("name", ["../escape", "a/b", "a\\b", "", ".."])
def test_the_local_store_refuses_unsafe_export_names(tmp_path, name) -> None:
    from nodo_api.adapters.local.blob import LocalBlobStore
    from nodo_api.adapters.local.db import LocalDatabase

    db = LocalDatabase(tmp_path / "n.db")
    blobs = LocalBlobStore(tmp_path / "audio", db, lambda: "http://127.0.0.1:1")
    with pytest.raises(ValueError):
        blobs.put_export("local", name, b"{}")
    db.close()
