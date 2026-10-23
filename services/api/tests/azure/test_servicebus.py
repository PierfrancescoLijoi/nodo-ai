from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from nodo_api.adapters.azure.servicebus import ServiceBusJobQueue


class FakeOutbox:
    def __init__(self) -> None:
        self.jobs: dict[tuple[str, str, str], dict] = {}

    def record(self, workspace_id, job_key, job_type, payload):
        key = (workspace_id, job_key, job_type)
        job = self.jobs.setdefault(key, {"id": f"job-{len(self.jobs) + 1}", "published": False})
        return job["id"], job["published"]

    def mark_published(self, workspace_id, job_key, job_type) -> None:
        self.jobs[(workspace_id, job_key, job_type)]["published"] = True


class FakeSender:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list = []
        self.fail = fail

    def send_messages(self, message) -> None:
        if self.fail:
            raise ConnectionError("service bus unavailable")
        self.sent.append(message)


def _queue(sender: FakeSender, outbox: FakeOutbox | None = None):
    @contextmanager
    def factory():
        yield sender

    return ServiceBusJobQueue(outbox or FakeOutbox(), factory)


def test_a_job_is_sent_once_with_its_identity_as_message_id() -> None:
    sender = FakeSender()
    queue = _queue(sender)
    first = queue.publish_once("ws-a", "m1", "transcribe", {"meeting_id": "m1"})
    second = queue.publish_once("ws-a", "m1", "transcribe", {"meeting_id": "m1"})
    assert first == second
    assert len(sender.sent) == 1
    message = sender.sent[0]
    assert message.message_id == "ws-a:m1:transcribe"
    body = json.loads(str(message))
    assert body["workspace_id"] == "ws-a"
    assert body["job_type"] == "transcribe"
    assert body["meeting_id"] == "m1"
    assert set(body) == {"job_id", "workspace_id", "job_key", "job_type", "meeting_id", "trace"}


def test_a_failed_send_is_retried_on_the_next_call() -> None:
    outbox = FakeOutbox()
    broken = FakeSender(fail=True)
    with pytest.raises(ConnectionError):
        _queue(broken, outbox).publish_once("ws-a", "m1", "transcribe", {"meeting_id": "m1"})
    assert outbox.jobs[("ws-a", "m1", "transcribe")]["published"] is False
    healthy = FakeSender()
    _queue(healthy, outbox).publish_once("ws-a", "m1", "transcribe", {"meeting_id": "m1"})
    assert len(healthy.sent) == 1
    assert outbox.jobs[("ws-a", "m1", "transcribe")]["published"] is True


class SchedulingSender(FakeSender):
    def __init__(self) -> None:
        super().__init__()
        self.scheduled: list[tuple] = []

    def schedule_messages(self, message, schedule_time_utc):
        self.scheduled.append((message, schedule_time_utc))
        return [1]


def test_a_later_job_is_scheduled_not_sent_and_needs_no_outbox_row() -> None:
    from datetime import UTC, datetime, timedelta

    sender, outbox = SchedulingSender(), FakeOutbox()
    before = datetime.now(UTC)
    _queue(sender, outbox).publish_later(
        "ws-a", "m1:poll:2", "transcribe", {"meeting_id": "m1"}, 135
    )
    assert sender.sent == [] and outbox.jobs == {}
    ((message, when),) = sender.scheduled
    assert before + timedelta(seconds=134) <= when <= datetime.now(UTC) + timedelta(seconds=136)
    assert (
        message.message_id == "ws-a:m1:poll:2:transcribe"
    )  # one per poll: never deduplicated away
    body = json.loads(str(message))
    assert (body["job_type"], body["meeting_id"], body["workspace_id"]) == (
        "transcribe",
        "m1",
        "ws-a",
    )
    assert "trace" in body
