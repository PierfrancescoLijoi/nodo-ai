from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MeetingState(StrEnum):
    UPLOADING = "uploading"
    TRANSCRIBING = "transcribing"
    EXTRACTING = "extracting"
    REVIEW = "review"
    READY = "ready"
    FAILED = "failed"


class Answerability(StrEnum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNANSWERABLE = "unanswerable"


class Locale(StrEnum):
    EN = "en"
    PL = "pl"
    IT = "it"
    DA = "da"


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    id: str
    audio_days: int = 30
    transcript_days: int | None = None
    graph_days: int | None = None


@dataclass(frozen=True, slots=True)
class RecordingAttestation:
    user_id: str
    meeting_id: str
    notice_version: str
    acknowledged_at: datetime
    retention_policy_id: str
    locale: Locale
    audit_event_id: str
    signature: str


@dataclass(frozen=True, slots=True)
class Meeting:
    id: str
    workspace_id: str
    owner_id: str
    created_at: datetime
    state: MeetingState
    attestation: RecordingAttestation
    project: str | None = None
    tombstoned_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Speaker:
    id: str
    meeting_id: str
    anon_label: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"S[1-9][0-9]*", self.anon_label):
            raise ValueError("Speaker labels must be anonymous labels such as S1 or S2")


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    id: str
    meeting_id: str
    speaker_id: str
    start_ms: int
    end_ms: int
    text: str
    confidence: float
    version: int
    locale: Locale
    project: str | None = None


@dataclass(frozen=True, slots=True)
class Citation:
    meeting_id: str
    segment_id: str
    start_ms: int
    end_ms: int
    quote: str


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str
    label: str
    meeting_id: str
    segment_id: str


@dataclass(frozen=True, slots=True)
class SpokenPhrase:
    """One recognised phrase from speech to text, before it becomes a transcript segment."""

    speaker: int
    start_ms: int
    end_ms: int
    text: str
    confidence: float
    locale: str
