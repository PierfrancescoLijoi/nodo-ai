"""Ingestion: turn a finished upload into a transcript, a search index and a knowledge graph.

Idempotent by construction and safe to retry at every step. A message for a meeting past the
extraction stage is a no-op, a retry reuses a transcript that was already saved, and re-running the
extraction replaces the meeting's graph contribution while keeping decisions a person already
reviewed (ARCHITECTURE.md section 5.1, ADR-012)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from nodo_api.domain.graph import (
    EntityType,
    ExtractedEntity,
    Extraction,
    MeetingRef,
    normalize_label,
    validate_extraction,
)
from nodo_api.domain.models import (
    Locale,
    Meeting,
    MeetingState,
    SpeechStatus,
    SpokenPhrase,
    TranscriptSegment,
)
from nodo_api.domain.rules import InvalidStateTransition, transition_meeting
from nodo_api.errors import NotFoundError
from nodo_api.ports import (
    BlobStore,
    Extractor,
    GraphStore,
    JobQueue,
    MeetingRepository,
    Retriever,
    SpeechJobStore,
    SpeechToText,
    StagedSpeechToText,
    TranscriptRepository,
)
from nodo_api.telemetry import instruments, stage

logger = logging.getLogger("nodo_ingestion")

FIRST_VERSION = 1
TRANSCRIBE = "transcribe"
POLL_GROWTH = 1.5  # each poll waits half as long again as the one before, up to the ceiling
KNOWN_DECISIONS = 20
SPEAKER_EVIDENCE = 3  # segments cited as proof that a named speaker took part
PROCESSABLE = (MeetingState.TRANSCRIBING, MeetingState.EXTRACTING)
REANALYZABLE = (MeetingState.REVIEW, MeetingState.READY)


def locale_of(bcp47: str, fallback: Locale) -> Locale:
    """pl-PL -> Locale.PL; anything outside the four supported languages keeps the fallback."""
    try:
        return Locale(bcp47.split("-")[0].lower())
    except ValueError:
        return fallback


def build_segments(
    meeting_id: str, phrases: tuple[SpokenPhrase, ...], fallback: Locale, project: str | None
) -> tuple[TranscriptSegment, ...]:
    return tuple(
        TranscriptSegment(
            id=f"seg-{index:05d}",
            meeting_id=meeting_id,
            speaker_id=f"S{phrase.speaker}",  # anonymous label only, never a person (ADR-004)
            start_ms=phrase.start_ms,
            end_ms=phrase.end_ms,
            text=phrase.text,
            confidence=phrase.confidence,
            version=FIRST_VERSION,
            locale=locale_of(phrase.locale, fallback),
            project=project,
        )
        for index, phrase in enumerate(phrases, start=1)
    )


def default_title(meeting: Meeting) -> str:
    return meeting.title or f"Meeting {meeting.created_at:%Y-%m-%d %H:%M}"


def with_named_speakers(
    raw: Extraction, segments: tuple[TranscriptSegment, ...], names: dict[str, str]
) -> Extraction:
    """A speaker the owner named is a participant: add the person with a few of their segments as
    proof, unless the model already found someone with that name."""
    known = {normalize_label(e.label) for e in raw.entities if e.type is EntityType.PERSON}
    added = []
    for speaker, name in names.items():
        spoken = [s.id for s in segments if s.speaker_id == speaker][:SPEAKER_EVIDENCE]
        if spoken and normalize_label(name) not in known:
            known.add(normalize_label(name))
            added.append(
                ExtractedEntity(f"speaker:{speaker}", EntityType.PERSON, name, tuple(spoken))
            )
    return replace(raw, entities=(*raw.entities, *added)) if added else raw


class IngestionService:
    def __init__(
        self,
        meetings: MeetingRepository,
        transcripts: TranscriptRepository,
        blobs: BlobStore,
        retriever: Retriever,
        speech: SpeechToText,
        candidate_locales: tuple[str, ...],
        extractor: Extractor,
        graph: GraphStore,
        *,
        staged_speech: StagedSpeechToText | None = None,
        speech_jobs: SpeechJobStore | None = None,
        jobs: JobQueue | None = None,
        poll_seconds: int = 60,
        poll_max_seconds: int = 300,
        speech_timeout_seconds: int = 7200,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._meetings = meetings
        self._transcripts = transcripts
        self._blobs = blobs
        self._retriever = retriever
        self._speech = speech
        self._locales = candidate_locales
        self._extractor = extractor
        self._graph = graph
        # Given these three, the worker submits the speech job and leaves; a later message collects.
        self._staged = staged_speech if speech_jobs is not None and jobs is not None else None
        self._speech_jobs = speech_jobs
        self._jobs = jobs
        self._poll_seconds = poll_seconds
        self._poll_max_seconds = poll_max_seconds
        self._speech_timeout = speech_timeout_seconds
        self._clock = clock

    def process(self, workspace_id: str, meeting_id: str) -> bool:
        """True when work was done; False for a stale message or a meeting that is gone."""
        try:
            meeting = self._meetings.get_for_processing(workspace_id, meeting_id)
        except NotFoundError:
            # Deleted while queued: nothing must be recreated, and a job the provider still
            # holds for the audio is cancelled.
            self._forget_speech_job(workspace_id, meeting_id)
            return False
        if meeting.state not in PROCESSABLE:
            return False
        with stage("transcribe", locale=meeting.attestation.locale.value):
            segments = self._existing_or_new_transcript(workspace_id, meeting_id, meeting)
        if segments is None:
            return True  # submitted or still running: a scheduled message carries on from here
        audio_seconds = max(s.end_ms for s in segments) / 1000
        instruments.audio_seconds.add(audio_seconds)
        with stage("index", segments=len(segments)):
            self._retriever.index(workspace_id, meeting.owner_id, segments)
        if meeting.state is MeetingState.TRANSCRIBING:
            meeting = self._meetings.save(
                workspace_id, transition_meeting(meeting, MeetingState.EXTRACTING)
            )
        with stage("analyze", segments=len(segments)):
            clean = self._analyze(workspace_id, meeting, segments)
        done = replace(
            transition_meeting(meeting, MeetingState.REVIEW),
            title=default_title(meeting),
            summary=clean.summary or None,
            duration_ms=max(s.end_ms for s in segments),
        )
        self._meetings.save(workspace_id, done)
        return True

    def reanalyze(self, workspace_id: str, meeting_id: str) -> bool:
        """Run the extraction again on the latest transcript, e.g. after the owner named a speaker
        or corrected the text. Reviewed decisions survive (replace-by-meeting)."""
        try:
            meeting = self._meetings.get_for_processing(workspace_id, meeting_id)
            segments = self._transcripts.latest(workspace_id, meeting_id)
        except NotFoundError:
            return False
        if meeting.state not in REANALYZABLE:
            return False
        clean = self._analyze(workspace_id, meeting, segments)
        if clean.summary:
            self._meetings.save(workspace_id, replace(meeting, summary=clean.summary))
        return True

    def _analyze(self, workspace_id: str, meeting: Meeting, segments) -> Extraction:
        known = self._graph.recent_decisions(workspace_id, meeting.owner_id, KNOWN_DECISIONS)
        names = dict(meeting.speaker_names)
        raw = self._extractor.extract(segments, meeting.attestation.locale, known, names)
        raw = with_named_speakers(raw, segments, names)
        clean = validate_extraction(
            raw,
            frozenset(s.id for s in segments),
            {d.id: EntityType.DECISION for d in known},
        )
        ref = MeetingRef(meeting.id, default_title(meeting), meeting.created_at)
        self._graph.upsert_extraction(workspace_id, meeting.owner_id, ref, clean)
        return clean

    def _existing_or_new_transcript(self, workspace_id, meeting_id, meeting):
        try:
            saved = self._transcripts.get_version(workspace_id, meeting_id, FIRST_VERSION)
        except NotFoundError:
            pass
        else:
            self._forget_speech_job(workspace_id, meeting_id)  # a crash left the job behind
            return saved
        if self._staged is not None:
            return self._staged_transcript(workspace_id, meeting_id, meeting)
        audio_url = self._blobs.create_read_url(workspace_id, meeting_id)
        phrases = self._speech.transcribe(audio_url, self._locales)
        segments = build_segments(meeting_id, phrases, meeting.attestation.locale, meeting.project)
        if not segments:
            raise ValueError("The recording produced no speech")
        return self._transcripts.add_version(workspace_id, meeting_id, segments)

    # ------------------------------------------------------------------------ speech in stages

    def _staged_transcript(self, workspace_id, meeting_id, meeting):
        """The transcript, or None while the provider is still working. Never waits."""
        assert self._staged is not None and self._speech_jobs is not None
        job = self._speech_jobs.get(workspace_id, meeting_id)
        if job is None:
            audio_url = self._blobs.create_read_url(workspace_id, meeting_id)
            handle = self._staged.submit(audio_url, self._locales)
            self._speech_jobs.start(workspace_id, meeting_id, handle, self._clock())
            self._schedule_poll(workspace_id, meeting_id, polls=0)
            return None
        waited = (self._clock() - job.submitted_at).total_seconds()
        if waited > self._speech_timeout:
            return self._give_up(workspace_id, meeting_id, "timeout")
        result = self._staged.poll(job.handle)
        if result.status is SpeechStatus.RUNNING:
            polls = self._speech_jobs.record_poll(workspace_id, meeting_id)
            self._schedule_poll(workspace_id, meeting_id, polls)
            return None
        if result.status is SpeechStatus.FAILED:
            return self._give_up(workspace_id, meeting_id, "provider_failed")
        segments = build_segments(
            meeting_id, result.phrases, meeting.attestation.locale, meeting.project
        )
        if not segments:
            return self._give_up(workspace_id, meeting_id, "no_speech")
        saved = self._transcripts.add_version(workspace_id, meeting_id, segments)
        self._forget_speech_job(workspace_id, meeting_id)
        return saved

    def _schedule_poll(self, workspace_id: str, meeting_id: str, polls: int) -> None:
        assert self._jobs is not None
        delay = min(int(self._poll_seconds * POLL_GROWTH**polls), self._poll_max_seconds)
        self._jobs.publish_later(
            workspace_id,
            f"{meeting_id}:poll:{polls}",
            TRANSCRIBE,
            {"meeting_id": meeting_id},
            delay,
        )

    def _forget_speech_job(self, workspace_id: str, meeting_id: str) -> None:
        if self._staged is None or self._speech_jobs is None:
            return
        job = self._speech_jobs.get(workspace_id, meeting_id)
        if job is None:
            return
        self._staged.cancel(job.handle)
        self._speech_jobs.clear(workspace_id, meeting_id)

    def _give_up(self, workspace_id: str, meeting_id: str, reason: str) -> None:
        """The provider will not produce a transcript from this audio, and asking again would pay
        again for the same answer: end the meeting as failed and let the message complete."""
        logger.error("speech_gave_up reason=%s", reason)
        self._forget_speech_job(workspace_id, meeting_id)
        self.mark_failed(workspace_id, meeting_id)
        return None

    def mark_failed(self, workspace_id: str, meeting_id: str) -> None:
        try:
            meeting = self._meetings.get_for_processing(workspace_id, meeting_id)
            self._meetings.save(workspace_id, transition_meeting(meeting, MeetingState.FAILED))
        except (NotFoundError, InvalidStateTransition):
            return
