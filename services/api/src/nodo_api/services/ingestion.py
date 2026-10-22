"""Ingestion: turn a finished upload into a transcript, a search index and a knowledge graph.

Idempotent by construction and safe to retry at every step. A message for a meeting past the
extraction stage is a no-op, a retry reuses a transcript that was already saved, and re-running the
extraction replaces the meeting's graph contribution while keeping decisions a person already
reviewed (ARCHITECTURE.md section 5.1, ADR-012)."""

from __future__ import annotations

from dataclasses import replace

from nodo_api.domain.graph import (
    EntityType,
    ExtractedEntity,
    Extraction,
    MeetingRef,
    normalize_label,
    validate_extraction,
)
from nodo_api.domain.models import Locale, Meeting, MeetingState, SpokenPhrase, TranscriptSegment
from nodo_api.domain.rules import InvalidStateTransition, transition_meeting
from nodo_api.errors import NotFoundError
from nodo_api.ports import (
    BlobStore,
    Extractor,
    GraphStore,
    MeetingRepository,
    Retriever,
    SpeechToText,
    TranscriptRepository,
)

FIRST_VERSION = 1
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
    ) -> None:
        self._meetings = meetings
        self._transcripts = transcripts
        self._blobs = blobs
        self._retriever = retriever
        self._speech = speech
        self._locales = candidate_locales
        self._extractor = extractor
        self._graph = graph

    def process(self, workspace_id: str, meeting_id: str) -> bool:
        """True when work was done; False for a stale message or a meeting that is gone."""
        try:
            meeting = self._meetings.get_for_processing(workspace_id, meeting_id)
        except NotFoundError:
            return False  # deleted while queued: nothing to do, and nothing must be recreated
        if meeting.state not in PROCESSABLE:
            return False
        segments = self._existing_or_new_transcript(workspace_id, meeting_id, meeting)
        self._retriever.index(workspace_id, meeting.owner_id, segments)
        if meeting.state is MeetingState.TRANSCRIBING:
            meeting = self._meetings.save(
                workspace_id, transition_meeting(meeting, MeetingState.EXTRACTING)
            )
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
