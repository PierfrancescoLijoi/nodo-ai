from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import UserDelegationKey

from nodo_api.adapters.azure.blob import AzureBlobStore

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


class FakeContainer:
    def __init__(self, names: list[str]) -> None:
        self.names = names
        self.deleted: list[str] = []

    def list_blobs(self, name_starts_with: str):
        if self.names is None:
            raise ResourceNotFoundError("no container")
        return [SimpleNamespace(name=n) for n in self.names if n.startswith(name_starts_with)]

    def delete_blob(self, name: str) -> None:
        self.deleted.append(name)


class FakeService:
    def __init__(self, container: FakeContainer) -> None:
        self.container = container
        self.key_requests = 0

    def get_user_delegation_key(self, start, expiry) -> UserDelegationKey:
        self.key_requests += 1
        key = UserDelegationKey()
        key.signed_oid = "00000000-0000-0000-0000-000000000001"
        key.signed_tid = "00000000-0000-0000-0000-000000000002"
        key.signed_start = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        key.signed_expiry = expiry.strftime("%Y-%m-%dT%H:%M:%SZ")
        key.signed_service = "b"
        key.signed_version = "2023-11-03"
        key.value = base64.b64encode(b"k" * 32).decode()
        return key

    def get_container_client(self, name: str) -> FakeContainer:
        assert name == "audio"
        return self.container


def _store(container: FakeContainer | None = None, seconds: int = 900):
    service = FakeService(container or FakeContainer([]))
    return AzureBlobStore("stnodotest", service, upload_seconds=seconds, clock=lambda: NOW), service


def test_upload_link_is_one_write_only_blob_with_a_short_expiry() -> None:
    store, _ = _store()
    session = store.create_upload_session("ws-a", "meeting-1")
    url = urlparse(session["upload_url"])
    query = parse_qs(url.query)
    assert url.netloc == "stnodotest.blob.core.windows.net"
    assert url.path == "/audio/ws-a/meeting-1/audio"
    assert query["sp"] == ["cw"]  # create and write only: no read, no list, no delete
    assert query["sr"] == ["b"]  # scoped to one blob
    assert session["expires_in_seconds"] == 900
    expiry = datetime.strptime(query["se"][0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert expiry == NOW + timedelta(seconds=900)
    assert "sig" in query and "skoid" in query  # signed with a user delegation key (no account key)


def test_read_link_is_read_only() -> None:
    store, _ = _store()
    query = parse_qs(urlparse(store.create_read_url("ws-a", "meeting-1")).query)
    assert query["sp"] == ["r"]


def test_the_delegation_key_is_reused_between_links() -> None:
    store, service = _store()
    store.create_upload_session("ws-a", "m1")
    store.create_upload_session("ws-a", "m2")
    assert service.key_requests == 1


def test_unsafe_blob_names_are_percent_encoded() -> None:
    store, _ = _store()
    url = store.create_read_url("ws-a", "meeting with space")
    assert "meeting%20with%20space" in url


def test_erase_deletes_only_the_meeting_prefix() -> None:
    container = FakeContainer(["ws-a/m1/audio", "ws-a/m1/extra", "ws-a/m10/audio", "ws-b/m1/audio"])
    store, _ = _store(container)
    store.erase("ws-a", "m1")
    assert container.deleted == ["ws-a/m1/audio", "ws-a/m1/extra"]


def test_erase_is_complete_when_nothing_was_uploaded() -> None:
    container = FakeContainer([])
    container.names = None  # listing raises ResourceNotFoundError
    store, _ = _store(container)
    store.erase("ws-a", "m1")


@pytest.mark.parametrize("seconds", [60, 3600])
def test_upload_window_follows_configuration(seconds: int) -> None:
    store, _ = _store(seconds=seconds)
    assert store.create_upload_session("w", "m")["expires_in_seconds"] == seconds


def test_the_audit_sink_creates_files_and_never_overwrites() -> None:
    from azure.core.exceptions import ResourceExistsError

    from nodo_api.adapters.azure.audit_archive_pg import AzureAuditSink

    class Blob:
        def __init__(self, store, name):
            self.store, self.name = store, name

        def upload_blob(self, data, overwrite, content_type):
            assert overwrite is False and content_type == "application/x-ndjson"
            if self.name in self.store:
                raise ResourceExistsError("exists")
            self.store[self.name] = data

    class Service:
        def __init__(self):
            self.store = {}

        def get_blob_client(self, container, name):
            assert container == "audit"
            return Blob(self.store, name)

    service = Service()
    sink = AzureAuditSink(service)
    assert sink.put("2026/10/23/a.jsonl", b"one") is True
    assert sink.put("2026/10/23/a.jsonl", b"two") is False
    assert service.store == {"2026/10/23/a.jsonl": b"one"}
