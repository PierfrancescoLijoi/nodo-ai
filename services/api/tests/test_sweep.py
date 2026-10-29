"""The two sweeps that find work a failure left behind (ADR-022):

* a job recorded in the outbox but never sent to the queue (the sender was down, the process died
  between the record and the send);
* a meeting that has been processing for longer than any legitimate run and has nothing left in
  flight (a lost message), which is started again from the top - the processing is idempotent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nodo_api.adapters.memory import MemoryJobQueue, MemoryMaintenanceStore
from nodo_api.domain.maintenance import PendingJob
from nodo_api.domain.models import MeetingState
from nodo_api.main import AppContainer
from nodo_api.services.sweep import SweepService

WORKSPACE, USER = "workspace-a", "user-a"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


class Recorder:
    """A queue that records what it is asked to publish, and can be made to fail for one job."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, str, dict]] = []
        self.fail_for: set[str] = set()

    def publish_once(self, workspace_id, job_key, job_type, payload):
        if job_key in self.fail_for:
            raise ConnectionError("queue unavailable")
        self.published.append((workspace_id, job_key, job_type, payload))
        return "id"


class Rig:
    def __init__(self, app, attestation) -> None:
        self.container: AppContainer = app.state.container
        self.attestation = attestation
        self.queue = MemoryJobQueue()
        self.store = MemoryMaintenanceStore(self.container.meetings, self.queue)
        self.out = Recorder()
        self.audit: list[tuple[str, dict]] = []
        audit = self.container.audit
        original = audit.append
        audit.append = lambda ws, kind, meta: (
            self.audit.append((kind, meta)),
            original(ws, kind, meta),
        )[1]
        self.service = SweepService(
            self.store,
            self.out,
            audit,
            clock=lambda: NOW,
            outbox_grace_seconds=600,
            stuck_after_seconds=10_800,
        )

    def processing_meeting(self, key: str, *, started: datetime) -> str:
        meeting = self.container.meeting_service.create(WORKSPACE, USER, key, self.attestation)
        self.container.meeting_service.complete(WORKSPACE, USER, meeting.id)
        self.queue.publish_once(WORKSPACE, meeting.id, "transcribe", {"meeting_id": meeting.id})
        self.queue.backdate(WORKSPACE, meeting.id, "transcribe", started)
        return meeting.id


def test_a_job_that_was_recorded_but_never_sent_is_sent_again(app, attestation) -> None:
    rig = Rig(app, attestation)
    rig.queue.record_unsent(
        WORKSPACE, "m1", "transcribe", {"meeting_id": "m1"}, NOW - timedelta(hours=1)
    )
    report = rig.service.run()
    assert rig.out.published == [(WORKSPACE, "m1", "transcribe", {"meeting_id": "m1"})]
    assert report.republished == 1 and report.failed == []


def test_a_job_recorded_a_moment_ago_is_left_to_the_process_that_is_sending_it(
    app, attestation
) -> None:
    rig = Rig(app, attestation)
    rig.queue.record_unsent(WORKSPACE, "m1", "transcribe", {}, NOW - timedelta(seconds=30))
    assert rig.service.run().republished == 0 and rig.out.published == []


def test_a_meeting_stuck_for_hours_is_started_again_once_a_day(app, attestation) -> None:
    rig = Rig(app, attestation)
    stuck = rig.processing_meeting("sweep-1", started=NOW - timedelta(hours=5))
    report = rig.service.run()
    day = NOW.strftime("%Y%m%d")
    assert rig.out.published == [
        (WORKSPACE, f"{stuck}:sweep:{day}", "transcribe", {"meeting_id": stuck})
    ]
    assert report.restarted == 1
    assert ("meeting_restarted", {"meeting_id": stuck}) in rig.audit


def test_a_meeting_that_is_merely_busy_is_not_disturbed(app, attestation) -> None:
    rig = Rig(app, attestation)
    rig.processing_meeting("sweep-2", started=NOW - timedelta(minutes=40))
    assert rig.service.run().restarted == 0 and rig.out.published == []


def test_a_meeting_that_reached_review_is_never_restarted(app, attestation) -> None:
    rig = Rig(app, attestation)
    done = rig.processing_meeting("sweep-3", started=NOW - timedelta(days=2))
    meeting = rig.container.meetings.get_owned(WORKSPACE, USER, done)
    from dataclasses import replace

    rig.container.meetings.save(WORKSPACE, replace(meeting, state=MeetingState.REVIEW))
    assert rig.service.run().restarted == 0


def test_one_failing_job_does_not_stop_the_sweep(app, attestation) -> None:
    rig = Rig(app, attestation)
    rig.queue.record_unsent(WORKSPACE, "bad", "transcribe", {}, NOW - timedelta(hours=1))
    rig.queue.record_unsent(WORKSPACE, "good", "transcribe", {}, NOW - timedelta(hours=1))
    rig.out.fail_for.add("bad")
    report = rig.service.run()
    assert [p[1] for p in rig.out.published] == ["good"]
    assert report.republished == 1
    assert [(f.meeting_id, f.error) for f in report.failed] == [("bad", "ConnectionError")]


def test_pending_jobs_carry_only_identifiers() -> None:
    job = PendingJob("ws", "k", "transcribe", {"meeting_id": "m"})
    assert job.payload == {"meeting_id": "m"}
