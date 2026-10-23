"""Speech to text in stages: submit and leave, poll on a schedule, finish when it is ready.

The worker never waits for the provider. What it knows between two messages is a row in the speech
job store (the provider's job handle), so a crash, a restart or a second worker costs nothing and
never submits (and pays for) the same audio twice."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nodo_api.adapters.memory import MemoryJobQueue, MemorySpeech, MemorySpeechJobStore
from nodo_api.adapters.memory.extractor import KeywordExtractor
from nodo_api.domain.models import MeetingState, SpeechPoll, SpeechStatus
from nodo_api.main import AppContainer
from nodo_api.services.ingestion import IngestionService
from nodo_api.worker import Worker

WORKSPACE, USER = "workspace-a", "user-a"
LOCALES = ("en-GB", "pl-PL")
START = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


class ScriptedSpeech:
    """A provider whose job reports the given statuses, one per poll, then the last one forever."""

    def __init__(self, *statuses: SpeechStatus, phrases=None) -> None:
        self.statuses = list(statuses)
        self.phrases = MemorySpeech().transcribe("u", LOCALES) if phrases is None else phrases
        self.submitted: list[str] = []
        self.polled: list[str] = []
        self.cancelled: list[str] = []
        self.fail_submit = False

    def submit(self, audio_url, candidate_locales):
        if self.fail_submit:
            raise ConnectionError("speech unavailable")
        self.submitted.append(audio_url)
        return f"job-{len(self.submitted)}"

    def poll(self, handle):
        self.polled.append(handle)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        phrases = self.phrases if status is SpeechStatus.SUCCEEDED else ()
        return SpeechPoll(status, phrases)

    def cancel(self, handle):
        self.cancelled.append(handle)

    def transcribe(self, audio_url, candidate_locales):  # the waiting way: must stay unused
        raise AssertionError("the staged path must not wait for the provider")


class Rig:
    def __init__(self, app, attestation, speech: ScriptedSpeech) -> None:
        self.container: AppContainer = app.state.container
        self.clock = Clock()
        self.speech = speech
        self.queue = MemoryJobQueue()
        self.jobs = MemorySpeechJobStore()
        meeting = self.container.meeting_service.create(WORKSPACE, USER, "staged-1", attestation)
        self.container.meeting_service.complete(WORKSPACE, USER, meeting.id)
        self.meeting_id = meeting.id
        self.ingestion = IngestionService(
            self.container.meetings,
            self.container.transcripts,
            self.container.blobs,
            self.container.retriever,
            speech,
            LOCALES,
            KeywordExtractor(),
            self.container.graph,
            staged_speech=speech,
            speech_jobs=self.jobs,
            jobs=self.queue,
            poll_seconds=60,
            poll_max_seconds=300,
            speech_timeout_seconds=7200,
            clock=self.clock,
        )

    def state(self) -> MeetingState:
        return self.container.meetings.get_owned(WORKSPACE, USER, self.meeting_id).state

    def process(self) -> bool:
        return self.ingestion.process(WORKSPACE, self.meeting_id)

    def waiting(self):
        return self.jobs.get(WORKSPACE, self.meeting_id)


def test_the_first_message_submits_and_leaves_without_waiting(app, attestation) -> None:
    rig = Rig(app, attestation, ScriptedSpeech(SpeechStatus.RUNNING))
    assert rig.process() is True
    assert len(rig.speech.submitted) == 1 and rig.speech.polled == []
    assert rig.state() is MeetingState.TRANSCRIBING
    assert rig.waiting().handle == "job-1" and rig.waiting().submitted_at == START
    (later,) = rig.queue.scheduled
    assert (later.job_type, later.delay_seconds) == ("transcribe", 60)
    assert later.payload == {"meeting_id": rig.meeting_id}
    assert later.job_key == f"{rig.meeting_id}:poll:0"


def test_a_running_job_is_polled_and_polls_back_off_to_a_ceiling(app, attestation) -> None:
    rig = Rig(app, attestation, ScriptedSpeech(SpeechStatus.RUNNING))
    rig.process()
    for _ in range(8):
        rig.clock.advance(seconds=30)
        assert rig.process() is True
    assert len(rig.speech.submitted) == 1  # never submitted again
    delays = [m.delay_seconds for m in rig.queue.scheduled]
    assert delays[:4] == [60, 90, 135, 202]
    assert max(delays) == 300 and delays == sorted(delays)
    keys = [m.job_key for m in rig.queue.scheduled]
    assert len(set(keys)) == len(keys), "every poll is a message of its own"
    assert rig.state() is MeetingState.TRANSCRIBING


def test_a_finished_job_becomes_a_transcript_and_the_meeting_reaches_review(
    app, attestation
) -> None:
    rig = Rig(app, attestation, ScriptedSpeech(SpeechStatus.RUNNING, SpeechStatus.SUCCEEDED))
    rig.process()
    rig.clock.advance(minutes=2)
    rig.process()  # still running
    rig.clock.advance(minutes=2)
    assert rig.process() is True
    assert rig.state() is MeetingState.REVIEW
    segments = rig.container.transcripts.latest(WORKSPACE, rig.meeting_id)
    assert [s.speaker_id for s in segments] == ["S1", "S2", "S1"]
    assert rig.container.retriever.search(WORKSPACE, USER, "ingestion queue", None, 10)
    assert rig.waiting() is None and rig.speech.cancelled == ["job-1"]
    scheduled = len(rig.queue.scheduled)
    assert rig.process() is False  # a late duplicate message does nothing
    assert len(rig.queue.scheduled) == scheduled and len(rig.speech.submitted) == 1


def test_a_provider_failure_ends_the_meeting_without_paying_again(app, attestation) -> None:
    rig = Rig(app, attestation, ScriptedSpeech(SpeechStatus.RUNNING, SpeechStatus.FAILED))
    rig.process()
    rig.clock.advance(minutes=1)
    rig.process()
    rig.clock.advance(minutes=1)
    assert rig.process() is True  # settled: the message completes, nothing is retried
    assert rig.state() is MeetingState.FAILED
    assert rig.waiting() is None and rig.speech.cancelled == ["job-1"]
    assert len(rig.speech.submitted) == 1


def test_a_job_that_takes_too_long_is_cancelled_and_the_meeting_fails(app, attestation) -> None:
    rig = Rig(app, attestation, ScriptedSpeech(SpeechStatus.RUNNING))
    rig.process()
    rig.clock.advance(hours=2, seconds=1)
    assert rig.process() is True
    assert rig.state() is MeetingState.FAILED
    assert rig.speech.cancelled == ["job-1"] and rig.waiting() is None
    assert rig.speech.polled == []  # no point asking: the budget is spent


def test_a_recording_without_speech_fails_the_meeting_instead_of_looping(app, attestation) -> None:
    rig = Rig(app, attestation, ScriptedSpeech(SpeechStatus.SUCCEEDED, phrases=()))
    rig.process()
    rig.clock.advance(minutes=1)
    assert rig.process() is True
    assert rig.state() is MeetingState.FAILED
    assert rig.waiting() is None and rig.speech.cancelled == ["job-1"]


def test_a_failed_submit_leaves_no_trace_so_the_retry_starts_clean(app, attestation) -> None:
    speech = ScriptedSpeech(SpeechStatus.RUNNING)
    rig = Rig(app, attestation, speech)
    speech.fail_submit = True
    with pytest.raises(ConnectionError):
        rig.process()
    assert rig.waiting() is None and rig.queue.scheduled == []
    speech.fail_submit = False
    rig.process()
    assert len(speech.submitted) == 1 and rig.waiting().handle == "job-1"


def test_a_crash_between_saving_the_transcript_and_cleaning_up_resubmits_nothing(
    app, attestation
) -> None:
    rig = Rig(app, attestation, ScriptedSpeech(SpeechStatus.SUCCEEDED))
    rig.process()
    rig.clock.advance(minutes=1)
    real_clear = rig.jobs.clear
    rig.jobs.clear = lambda *a: (_ for _ in ()).throw(RuntimeError("crash"))
    with pytest.raises(RuntimeError):
        rig.process()  # transcript saved, then the process died before forgetting the job
    rig.jobs.clear = real_clear
    assert rig.container.transcripts.latest(WORKSPACE, rig.meeting_id)
    assert rig.process() is True  # the redelivered message
    assert rig.state() is MeetingState.REVIEW
    assert len(rig.speech.submitted) == 1
    assert rig.waiting() is None and "job-1" in rig.speech.cancelled


def test_a_meeting_deleted_while_the_provider_works_cleans_up_after_itself(
    app, attestation
) -> None:
    rig = Rig(app, attestation, ScriptedSpeech(SpeechStatus.RUNNING))
    rig.process()
    rig.container.meeting_service.delete(WORKSPACE, USER, rig.meeting_id)
    rig.clock.advance(minutes=1)
    assert rig.process() is False
    assert rig.waiting() is None and rig.speech.cancelled == ["job-1"]
    assert rig.speech.polled == []


def test_without_staging_the_worker_still_transcribes_in_one_go(app, attestation) -> None:
    container: AppContainer = app.state.container
    meeting = container.meeting_service.create(WORKSPACE, USER, "sync-1", attestation)
    container.meeting_service.complete(WORKSPACE, USER, meeting.id)
    ingestion = IngestionService(
        container.meetings,
        container.transcripts,
        container.blobs,
        container.retriever,
        MemorySpeech(),
        LOCALES,
        KeywordExtractor(),
        container.graph,
    )
    assert ingestion.process(WORKSPACE, meeting.id) is True
    assert container.meetings.get_owned(WORKSPACE, USER, meeting.id).state is MeetingState.REVIEW


class Receiver:
    def __init__(self) -> None:
        self.outcome: list[str] = []

    def complete_message(self, m) -> None:
        self.outcome.append("complete")

    def abandon_message(self, m) -> None:
        self.outcome.append("abandon")

    def dead_letter_message(self, m, reason=None, error_description=None) -> None:
        self.outcome.append(f"dead_letter:{reason}")


def test_the_worker_completes_the_message_that_scheduled_the_next_poll(app, attestation) -> None:
    rig = Rig(app, attestation, ScriptedSpeech(SpeechStatus.RUNNING))
    body = {"job_type": "transcribe", "workspace_id": WORKSPACE, "meeting_id": rig.meeting_id}
    message = SimpleNamespace(body=[json.dumps(body).encode()], delivery_count=1)
    receiver = Receiver()
    assert Worker(rig.ingestion).handle(receiver, message) == "completed"
    assert receiver.outcome == ["complete"] and len(rig.queue.scheduled) == 1
