"""Hand a finished live transcript to the existing pipeline.

A live meeting ends as a normal one does after the speech step: the phrases become transcript
version 1, the meeting moves to TRANSCRIBING and the same job is published; the worker then finds
the transcript already there and goes on with indexing and extraction (IngestionService.process).
Every step is safe to repeat, so a retry after a failure can not duplicate anything.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from nodo_api.domain.models import MeetingState, SpokenPhrase
from nodo_api.domain.rules import transition_meeting
from nodo_api.errors import NotFoundError
from nodo_api.services.ingestion import build_segments
from nodo_api.voice.store import SessionRecord

logger = logging.getLogger("nodo_api.voice")


class MeetingTranscriptFinalizer:
    def __init__(self, transcripts: Any, meetings: Any, jobs: Any) -> None:
        self._transcripts = transcripts
        self._meetings = meetings
        self._jobs = jobs

    async def finalize(self, record: SessionRecord, finals: list[dict[str, Any]]) -> bool:
        return await asyncio.to_thread(self._finalize, record, finals)

    def _finalize(self, record: SessionRecord, finals: list[dict[str, Any]]) -> bool:
        if record.meeting_id is None:
            return False
        workspace, meeting_id = record.workspace_id, record.meeting_id
        try:
            meeting = self._meetings.get_for_processing(workspace, meeting_id)
        except NotFoundError:
            # Terminal success: the meeting was deleted, so the retry queue must erase the
            # remaining transcript rather than retaining personal data until its TTL expires.
            return True
        if meeting.state is MeetingState.UPLOADING:
            phrases = tuple(
                SpokenPhrase(
                    speaker=int(item["speaker"]),
                    start_ms=int(item["start_ms"]),
                    end_ms=int(item["end_ms"]),
                    text=str(item["text"]),
                    confidence=float(item["confidence"]),
                    locale=str(item["locale"]),
                )
                for item in finals
            )
            segments = build_segments(
                meeting_id, phrases, meeting.attestation.locale, meeting.project
            )
            with contextlib.suppress(ValueError):  # an earlier attempt stored it
                self._transcripts.add_version(workspace, meeting_id, segments)
            self._meetings.save(workspace, transition_meeting(meeting, MeetingState.TRANSCRIBING))
        self._jobs.publish_once(workspace, meeting_id, "transcribe", {"meeting_id": meeting_id})
        return True
