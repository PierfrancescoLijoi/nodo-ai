"""Copying a local archive to a cloud deployment: the local engine on one side, the real cloud API
(memory adapters) on the other, joined through HTTP."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from nodo_api.adapters.local.sync import SyncReport, check_cloud_url, content_hash
from nodo_api.config import Settings
from nodo_api.main import create_app
from tests.test_local_app import HEADERS, _record
from tests.test_local_app import app as app
from tests.test_local_app import client as client

CLOUD_URL = "http://127.0.0.1:9"
CLOUD_HEADERS = {"Authorization": "Bearer test-dev-token-0123456789"}
CLOUD_TOKEN = "test-dev-token-0123456789"


@pytest.fixture
def cloud():
    settings = Settings(
        environment="test",
        auth_mode="dev",
        dev_token=CLOUD_TOKEN,
        dev_user_id="user-a",
        dev_workspace_id="workspace-a",
        attestation_hmac_key="cloud-test-attestation-key-0123456789",
    )
    application = create_app(settings)
    with TestClient(application) as cloud_client:
        yield application, cloud_client


def _sync(app, cloud_client) -> SyncReport:
    sync = app.state.container.runtime.sync
    sync._http = lambda: _Borrowed(cloud_client)
    return sync.run(CLOUD_URL, CLOUD_TOKEN)


class _Borrowed:
    """Lets the sync service use the test client without closing it."""

    def __init__(self, inner: TestClient) -> None:
        self._inner = inner

    def __enter__(self):
        return self._inner

    def __exit__(self, *args) -> None:
        return None


def _shape(insights: dict) -> dict:
    """Insights without ids, which depend on the workspace and so differ by design."""
    return {
        "people": sorted(p["label"] for p in insights["people"]),
        "topics": sorted(t["label"] for t in insights["topics"]),
        "decisions": sorted((d["label"], d["status"]) for d in insights["decisions"]),
        "actions": sorted((a["label"], a["owner_label"], a["status"]) for a in insights["actions"]),
    }


def _processed(app, client):
    meeting_id = _record(client)
    app.state.container.runtime.loop.run_pending()
    return meeting_id


def test_a_processed_meeting_arrives_in_the_cloud_the_way_it_is_here(app, client, cloud) -> None:
    meeting_id = _processed(app, client)
    client.patch(
        f"/v1/meetings/{meeting_id}/speakers/S2", headers=HEADERS, json={"name": "Zuzanna"}
    )
    app.state.container.runtime.loop.run_pending()
    local = client.get(f"/v1/meetings/{meeting_id}", headers=HEADERS).json()

    cloud_app, cloud_client = cloud
    report = _sync(app, cloud_client)
    assert (report.state, report.pushed, report.failed) == ("done", 1, [])

    remote = cloud_client.get(f"/v1/meetings/{meeting_id}", headers=CLOUD_HEADERS).json()
    assert remote["id"] == meeting_id and remote["title"] == local["title"]
    assert remote["state"] == local["state"] and remote["summary"] == local["summary"]
    assert [(s["id"], s["name"]) for s in remote["speakers"]] == [
        (s["id"], s["name"]) for s in local["speakers"]
    ]
    assert _shape(remote["insights"]) == _shape(local["insights"])
    assert "Zuzanna" in _shape(remote["insights"])["people"]
    page = "/transcript?limit=100"
    assert (
        cloud_client.get(f"/v1/meetings/{meeting_id}{page}", headers=CLOUD_HEADERS).json()["items"]
        == client.get(f"/v1/meetings/{meeting_id}{page}", headers=HEADERS).json()["items"]
    )
    # the cloud signed its own attestation but kept when the owner acknowledged the notice
    assert remote["recording_attestation"]["acknowledged_at"].startswith(
        local["recording_attestation"]["acknowledged_at"][:19]
    )
    assert (
        remote["recording_attestation"]["signature"] != local["recording_attestation"]["signature"]
    )
    hits = cloud_app.state.container.retriever.search("workspace-a", "user-a", "ingestion", None, 5)
    assert hits and all(h.meeting_id == meeting_id for h in hits)


def test_review_decisions_travel_with_the_meeting(app, client, cloud) -> None:
    meeting_id = _processed(app, client)
    graph = app.state.container.graph
    edges = graph.neighborhood(
        "local",
        "local-user",
        [e.id for e in graph.overview("local", "local-user", 50)],
        depth=1,
        node_limit=200,
        fanout=50,
        include_proposed=True,
        as_of=None,
    ).edges
    from nodo_api.domain.graph import RelationKind, ReviewStatus

    approved = [e for e in edges if e.kind is not RelationKind.MENTIONED_IN][:1]
    assert approved, "the sample meeting has at least one proposed relation"
    graph.review_relation("local", "local-user", approved[0].id, ReviewStatus.APPROVED)
    local = client.get(f"/v1/meetings/{meeting_id}", headers=HEADERS).json()["insights"]

    _, cloud_client = cloud
    _sync(app, cloud_client)
    remote = cloud_client.get(f"/v1/meetings/{meeting_id}", headers=CLOUD_HEADERS).json()[
        "insights"
    ]
    assert _shape(remote) == _shape(local)


def test_a_second_run_sends_nothing_and_a_change_sends_only_that_meeting(
    app, client, cloud
) -> None:
    meeting_id = _processed(app, client)
    _, cloud_client = cloud
    assert _sync(app, cloud_client).pushed == 1
    again = _sync(app, cloud_client)
    assert (again.pushed, again.unchanged) == (0, 1)
    client.patch(f"/v1/meetings/{meeting_id}/speakers/S1", headers=HEADERS, json={"name": "Marco"})
    app.state.container.runtime.loop.run_pending()
    third = _sync(app, cloud_client)
    assert third.pushed == 1
    remote = cloud_client.get(f"/v1/meetings/{meeting_id}", headers=CLOUD_HEADERS).json()
    assert {s["id"]: s["name"] for s in remote["speakers"]}["S1"] == "Marco"


def test_a_corrected_transcript_adds_a_version_in_the_cloud(app, client, cloud) -> None:
    meeting_id = _processed(app, client)
    _, cloud_client = cloud
    _sync(app, cloud_client)
    first = client.get(f"/v1/meetings/{meeting_id}/transcript", headers=HEADERS).json()["items"][0]
    reviewed = client.patch(
        f"/v1/meetings/{meeting_id}/review",
        headers=HEADERS,
        json={"corrections": [{"segment_id": first["id"], "text": "Corrected sentence"}]},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert _sync(app, cloud_client).pushed == 1
    items = cloud_client.get(f"/v1/meetings/{meeting_id}/transcript", headers=CLOUD_HEADERS).json()
    assert items["version"] == 2 and items["items"][0]["text"] == "Corrected sentence"


def test_a_meeting_deleted_here_is_deleted_there(app, client, cloud) -> None:
    meeting_id = _processed(app, client)
    _, cloud_client = cloud
    _sync(app, cloud_client)
    assert client.delete(f"/v1/meetings/{meeting_id}", headers=HEADERS).status_code == 204
    report = _sync(app, cloud_client)
    assert report.deleted == 1 and report.failed == []
    assert cloud_client.get(f"/v1/meetings/{meeting_id}", headers=CLOUD_HEADERS).status_code == 404
    assert _sync(app, cloud_client).deleted == 0  # remembered as done


def test_unfinished_meetings_are_not_sent(app, client, cloud) -> None:
    client.post(
        "/v1/meetings",
        headers={**HEADERS, "Idempotency-Key": "unfinished-key-1"},
        json={
            "recording_attestation": {
                "notice_version": "2026-10-v1",
                "locale": "en",
                "retention_policy_id": "retention-30d",
                "acknowledged": True,
            }
        },
    )
    _, cloud_client = cloud
    report = _sync(app, cloud_client)
    assert (report.pushed, report.total) == (0, 0)


def test_the_cloud_refuses_a_meeting_that_belongs_to_someone_else(app, client, cloud) -> None:
    meeting_id = _processed(app, client)
    cloud_app, cloud_client = cloud
    cloud_app.state.container.auth.add_dev_token("other-token-0123456789", "user-b", "workspace-a")
    sync = app.state.container.runtime.sync
    sync._http = lambda: _Borrowed(cloud_client)
    assert sync.run(CLOUD_URL, "other-token-0123456789").pushed == 1  # user-b gets their own copy
    report = sync.run(
        CLOUD_URL, CLOUD_TOKEN, force=True
    )  # user-a now collides with user-b's meeting id
    assert report.failed == [{"meeting_id": meeting_id, "reason": "http 409"}]


def test_the_cloud_rejects_a_bad_package_and_a_wrong_token(app, client, cloud) -> None:
    meeting_id = _processed(app, client)
    _, cloud_client = cloud
    bad_token = app.state.container.runtime.sync
    bad_token._http = lambda: _Borrowed(cloud_client)
    assert bad_token.run(CLOUD_URL, "x" * 30).failed[0]["reason"] == "http 401"
    other = cloud_client.put(
        f"/v1/sync/meetings/{meeting_id}",
        headers=CLOUD_HEADERS,
        json={"meeting_id": "different-id-1"},
    )
    assert other.status_code == 422


def test_the_api_over_http_starts_and_reports_a_sync(app, client, monkeypatch) -> None:
    started = []
    sync = app.state.container.runtime.sync
    monkeypatch.setattr(
        sync, "start", lambda url, token, force: started.append((url, token, force)) or True
    )
    reply = client.post(
        "/v1/local/sync",
        headers=HEADERS,
        json={"cloud_url": "https://nodo.example.com", "access_token": "t" * 40},
    )
    assert reply.status_code == 202 and started == [("https://nodo.example.com", "t" * 40, False)]
    assert client.get("/v1/local/sync", headers=HEADERS).json()["state"] == "idle"
    assert client.get("/v1/local/sync").status_code == 401


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "ftp://x.y",
        "https://user:pw@host.com",
        "https://h.com/?a=1",
        "https://",
    ],
)
def test_the_cloud_address_must_be_a_plain_https_address(url) -> None:
    with pytest.raises(ValueError):
        check_cloud_url(url)


def test_a_loopback_address_is_allowed_for_tests_and_the_hash_is_stable(app, client) -> None:
    assert check_cloud_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"
    meeting_id = _processed(app, client)
    runtime = app.state.container.runtime
    meeting = app.state.container.meetings.get_owned("local", "local-user", meeting_id)
    from nodo_api.adapters.local.sync import build_package

    one = build_package(
        meeting, app.state.container.transcripts, app.state.container.graph, runtime.sync._source
    )
    two = build_package(
        meeting, app.state.container.transcripts, app.state.container.graph, runtime.sync._source
    )
    assert content_hash(one) == content_hash(two)


def test_the_cloud_refuses_an_oversized_package_before_reading_it_all(cloud) -> None:
    _, cloud_client = cloud
    big = b"x" * (41 * 1024 * 1024)
    reply = cloud_client.put(
        "/v1/sync/meetings/some-meeting-id", headers=CLOUD_HEADERS, content=big
    )
    assert reply.status_code == 413
