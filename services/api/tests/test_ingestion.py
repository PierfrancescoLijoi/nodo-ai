from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nodo_api.adapters.memory import MemorySpeech
from nodo_api.adapters.memory.extractor import KeywordExtractor
from nodo_api.domain.models import Locale, MeetingState, SpokenPhrase
from nodo_api.errors import NotFoundError
from nodo_api.main import AppContainer
from nodo_api.services.ingestion import IngestionService, build_segments, locale_of
from nodo_api.worker import Worker, drain

WORKSPACE, USER = "workspace-a", "user-a"
LOCALES = ("en-GB", "pl-PL", "it-IT", "da-DK")


class CountingSpeech(MemorySpeech):
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio_url, candidate_locales):
        self.calls += 1
        return super().transcribe(audio_url, candidate_locales)


def _setup(app, attestation):
    container: AppContainer = app.state.container
    meeting = container.meeting_service.create(WORKSPACE, USER, "ingest-key-1", attestation)
    container.meeting_service.complete(WORKSPACE, USER, meeting.id)
    speech = CountingSpeech()
    ingestion = IngestionService(
        container.meetings,
        container.transcripts,
        container.blobs,
        container.retriever,
        speech,
        LOCALES,
        KeywordExtractor(),
        container.graph,
    )
    return container, meeting, ingestion, speech


def test_locale_mapping_uses_the_language_and_falls_back() -> None:
    assert locale_of("pl-PL", Locale.EN) is Locale.PL
    assert locale_of("da-DK", Locale.EN) is Locale.DA
    assert locale_of("de-DE", Locale.IT) is Locale.IT
    assert locale_of("", Locale.EN) is Locale.EN


def test_segments_get_anonymous_speaker_labels_and_stable_ids() -> None:
    phrases = (
        SpokenPhrase(2, 0, 10, "Ciao", 0.9, "it-IT"),
        SpokenPhrase(1, 10, 20, "Hej", 0.8, "da-DK"),
    )
    segments = build_segments("m1", phrases, Locale.EN, "NODO")
    assert [(s.id, s.speaker_id, s.locale) for s in segments] == [
        ("seg-00001", "S2", Locale.IT),
        ("seg-00002", "S1", Locale.DA),
    ]
    assert {s.version for s in segments} == {1}


def test_processing_saves_a_transcript_indexes_it_and_reaches_review(app, attestation) -> None:
    container, meeting, ingestion, _ = _setup(app, attestation)
    assert ingestion.process(WORKSPACE, meeting.id) is True
    assert container.meetings.get_owned(WORKSPACE, USER, meeting.id).state is MeetingState.REVIEW
    segments = container.transcripts.latest(WORKSPACE, meeting.id)
    assert [s.speaker_id for s in segments] == ["S1", "S2", "S1"]
    found = container.retriever.search(WORKSPACE, USER, "ingestion queue", None, 10)
    assert not container.retriever.search(WORKSPACE, "someone-else", "ingestion queue", None, 10)
    assert any(s.meeting_id == meeting.id for s in found)
    done = container.meetings.get_owned(WORKSPACE, USER, meeting.id)
    assert done.title and done.summary and done.duration_ms
    digest = container.graph.meeting_digests(WORKSPACE, USER, [meeting.id])[meeting.id]
    assert digest.people or digest.topics or digest.decisions


def test_a_repeated_message_does_nothing(app, attestation) -> None:
    _container, meeting, ingestion, speech = _setup(app, attestation)
    assert ingestion.process(WORKSPACE, meeting.id) is True
    assert ingestion.process(WORKSPACE, meeting.id) is False
    assert speech.calls == 1


def test_a_meeting_deleted_while_queued_is_ignored(app, attestation) -> None:
    container, meeting, ingestion, speech = _setup(app, attestation)
    container.meeting_service.delete(WORKSPACE, USER, meeting.id)
    assert ingestion.process(WORKSPACE, meeting.id) is False
    assert speech.calls == 0
    with pytest.raises(NotFoundError):
        container.transcripts.latest(WORKSPACE, meeting.id)


def test_a_retry_after_a_crash_reuses_the_saved_transcript(app, attestation) -> None:
    container, meeting, ingestion, speech = _setup(app, attestation)
    segments = build_segments(meeting.id, MemorySpeech().transcribe("u", LOCALES), Locale.EN, None)
    container.transcripts.add_version(WORKSPACE, meeting.id, segments)  # crashed after saving
    assert ingestion.process(WORKSPACE, meeting.id) is True
    assert speech.calls == 0
    assert container.meetings.get_owned(WORKSPACE, USER, meeting.id).state is MeetingState.REVIEW


def test_a_silent_recording_is_an_error_not_an_empty_transcript(app, attestation) -> None:
    container, meeting, _, _ = _setup(app, attestation)

    class Silent:
        def transcribe(self, audio_url, candidate_locales):
            return ()

    ingestion = IngestionService(
        container.meetings,
        container.transcripts,
        container.blobs,
        container.retriever,
        Silent(),
        LOCALES,
        KeywordExtractor(),
        container.graph,
    )
    with pytest.raises(ValueError, match="no speech"):
        ingestion.process(WORKSPACE, meeting.id)


def test_extraction_is_retried_without_transcribing_again(app, attestation) -> None:
    container, meeting, _, speech = _setup(app, attestation)

    class Flaky:
        calls = 0

        def extract(self, segments, locale, known_decisions, speakers=None):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise RuntimeError("model unavailable")
            return KeywordExtractor().extract(segments, locale, known_decisions, speakers)

    flaky = IngestionService(
        container.meetings,
        container.transcripts,
        container.blobs,
        container.retriever,
        speech,
        LOCALES,
        Flaky(),
        container.graph,
    )
    with pytest.raises(RuntimeError):
        flaky.process(WORKSPACE, meeting.id)
    stuck = container.meetings.get_owned(WORKSPACE, USER, meeting.id)
    assert stuck.state is MeetingState.EXTRACTING
    assert flaky.process(WORKSPACE, meeting.id) is True
    assert speech.calls == 1
    assert container.meetings.get_owned(WORKSPACE, USER, meeting.id).state is MeetingState.REVIEW


def test_mark_failed_moves_a_transcribing_meeting_to_failed(app, attestation) -> None:
    container, meeting, ingestion, _ = _setup(app, attestation)
    ingestion.mark_failed(WORKSPACE, meeting.id)
    assert container.meetings.get_owned(WORKSPACE, USER, meeting.id).state is MeetingState.FAILED
    ingestion.mark_failed(WORKSPACE, meeting.id)  # already failed: no error
    ingestion.mark_failed(WORKSPACE, "unknown")


# --- the worker ----------------------------------------------------------------------------------


class FakeReceiver:
    def __init__(self, messages=()) -> None:
        self.messages = list(messages)
        self.outcomes: list[tuple[str, str | None]] = []

    def __iter__(self):
        return iter(self.messages)

    def complete_message(self, message) -> None:
        self.outcomes.append(("complete", None))

    def abandon_message(self, message) -> None:
        self.outcomes.append(("abandon", None))

    def dead_letter_message(self, message, reason=None, error_description=None) -> None:
        self.outcomes.append(("dead_letter", reason))


def _message(body: dict | bytes, deliveries: int = 1):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return SimpleNamespace(body=[raw], delivery_count=deliveries)


def _job(meeting_id: str, job_type: str = "transcribe") -> dict:
    return {"job_type": job_type, "workspace_id": WORKSPACE, "meeting_id": meeting_id}


def test_the_worker_completes_a_good_message(app, attestation) -> None:
    container, meeting, ingestion, _ = _setup(app, attestation)
    receiver = FakeReceiver([_message(_job(meeting.id))])
    assert drain(Worker(ingestion), receiver) == 1
    assert receiver.outcomes == [("complete", None)]
    assert container.meetings.get_owned(WORKSPACE, USER, meeting.id).state is MeetingState.REVIEW


def test_the_worker_dead_letters_malformed_and_unknown_messages(app, attestation) -> None:
    _, meeting, ingestion, _ = _setup(app, attestation)
    receiver = FakeReceiver([_message(b"not json"), _message(_job(meeting.id, "mystery"))])
    drain(Worker(ingestion), receiver)
    assert receiver.outcomes == [("dead_letter", "malformed"), ("dead_letter", "unknown_job_type")]


class Exploding:
    def __init__(self) -> None:
        self.failed: list[tuple[str, str]] = []

    def process(self, workspace_id, meeting_id):
        raise RuntimeError("speech down")

    def mark_failed(self, workspace_id, meeting_id) -> None:
        self.failed.append((workspace_id, meeting_id))


def test_a_failure_is_retried_and_the_last_delivery_marks_the_meeting_failed() -> None:
    ingestion = Exploding()
    worker = Worker(ingestion, max_deliveries=3)
    receiver = FakeReceiver()
    assert worker.handle(receiver, _message(_job("m1"), deliveries=1)) == "retry"
    assert worker.handle(receiver, _message(_job("m1"), deliveries=3)) == "dead_lettered"
    assert receiver.outcomes == [("abandon", None), ("dead_letter", "max_deliveries")]
    assert ingestion.failed == [(WORKSPACE, "m1")]
