"""Ingestion: turn a finished upload into a versioned transcript and a search index.

Idempotent by construction: a repeated message for a meeting that already moved past
`transcribing` is a no-op, and a retry after a crash reuses the transcript that was already
saved (ARCHITECTURE.md section 5.1). Claim extraction and the human review step follow later."""

from __future__ import annotations

from nodo_api.domain.models import Locale, MeetingState, SpokenPhrase, TranscriptSegment
from nodo_api.domain.rules import InvalidStateTransition, transition_meeting
from nodo_api.errors import NotFoundError
from nodo_api.ports import (
    BlobStore,
    MeetingRepository,
    Retriever,
    SpeechToText,
    TranscriptRepository,
)

FIRST_VERSION = 1


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


class IngestionService:
    def __init__(
        self,
        meetings: MeetingRepository,
        transcripts: TranscriptRepository,
        blobs: BlobStore,
        retriever: Retriever,
        speech: SpeechToText,
        candidate_locales: tuple[str, ...],
    ) -> None:
        self._meetings = meetings
        self._transcripts = transcripts
        self._blobs = blobs
        self._retriever = retriever
        self._speech = speech
        self._locales = candidate_locales

    def process(self, workspace_id: str, meeting_id: str) -> bool:
        """True when work was done; False for a stale message or a meeting that is gone."""
        try:
            meeting = self._meetings.get_for_processing(workspace_id, meeting_id)
        except NotFoundError:
            return False  # deleted while queued: nothing to do, and nothing must be recreated
        if meeting.state is not MeetingState.TRANSCRIBING:
            return False
        segments = self._existing_or_new_transcript(workspace_id, meeting_id, meeting)
        for segment in segments:
            self._retriever.index(workspace_id, segment)
        extracting = transition_meeting(meeting, MeetingState.EXTRACTING)
        self._meetings.save(workspace_id, extracting)
        self._meetings.save(workspace_id, transition_meeting(extracting, MeetingState.REVIEW))
        return True

    def _existing_or_new_transcript(self, workspace_id, meeting_id, meeting):
        try:
            return self._transcripts.get_version(workspace_id, meeting_id, FIRST_VERSION)
        except NotFoundError:
            pass
        audio_url = self._blobs.create_read_url(workspace_id, meeting_id)
        phrases = self._speech.transcribe(audio_url, self._locales)
        segments = build_segments(meeting_id, phrases, meeting.attestation.locale, meeting.project)
        if not segments:
            raise ValueError("The recording produced no speech")
        return self._transcripts.add_version(workspace_id, meeting_id, segments)

    def mark_failed(self, workspace_id: str, meeting_id: str) -> None:
        try:
            meeting = self._meetings.get_for_processing(workspace_id, meeting_id)
            self._meetings.save(workspace_id, transition_meeting(meeting, MeetingState.FAILED))
        except (NotFoundError, InvalidStateTransition):
            return
