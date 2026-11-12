"""Source model: what the knowledge base is built from (ADR-029).

A meeting is one kind of source; its id is the source id and each transcript segment is a
fragment with the same id. Other kinds (documents, decks, web pages, notes, audio) share the
same shape and differ only in how a fragment is located inside its source.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from nodo_api.domain.models import Locale, Meeting, TranscriptSegment

MAX_FRAGMENT_CHARS = 20_000


class SourceKind(StrEnum):
    MEETING = "meeting"
    DOCUMENT = "document"
    DECK = "deck"
    WEB = "web"
    NOTE = "note"
    AUDIO = "audio"


def _check_range(name: str, value: tuple[int, int]) -> None:
    start, end = value
    if start < 0 or end < start:
        raise ValueError(f"{name} must be a non-negative range with end >= start")


@dataclass(frozen=True, slots=True)
class Locator:
    """Where a fragment sits inside its source; at least one of time, page, slide or chars."""

    time_ms: tuple[int, int] | None = None
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    slide: int | None = None
    chars: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if self.time_ms is None and self.page is None and self.slide is None and self.chars is None:
            raise ValueError("A locator needs time_ms, page, slide or chars")
        if self.time_ms is not None:
            _check_range("time_ms", self.time_ms)
        if self.chars is not None:
            _check_range("chars", self.chars)
        if self.page is not None and self.page < 1:
            raise ValueError("page starts at 1")
        if self.slide is not None and self.slide < 1:
            raise ValueError("slide starts at 1")
        if self.bbox is not None and self.page is None:
            raise ValueError("bbox needs a page")

    def to_json(self) -> str:
        fields = {
            "bbox": self.bbox,
            "chars": self.chars,
            "page": self.page,
            "slide": self.slide,
            "time_ms": self.time_ms,
        }
        return json.dumps(
            {k: v for k, v in fields.items() if v is not None},
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, text: str) -> Locator:
        data = json.loads(text)
        return cls(**{k: tuple(v) if isinstance(v, list) else v for k, v in data.items()})


@dataclass(frozen=True, slots=True)
class Fragment:
    id: str
    source_id: str
    section_id: str | None
    ordinal: int
    text: str
    locator: Locator
    version: int
    speaker_id: str | None = None
    confidence: float | None = None
    locale: Locale | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("A fragment needs text")
        if len(self.text) > MAX_FRAGMENT_CHARS:
            raise ValueError(f"A fragment holds at most {MAX_FRAGMENT_CHARS} characters")
        if self.version < 1:
            raise ValueError("version starts at 1")
        if self.ordinal < 0:
            raise ValueError("ordinal cannot be negative")

    @classmethod
    def from_segment(cls, segment: TranscriptSegment, ordinal: int) -> Fragment:
        return cls(
            id=segment.id,
            source_id=segment.meeting_id,
            section_id=None,
            ordinal=ordinal,
            text=segment.text,
            locator=Locator(time_ms=(segment.start_ms, segment.end_ms)),
            version=segment.version,
            speaker_id=segment.speaker_id,
            confidence=segment.confidence,
            locale=segment.locale,
        )

    def to_segment(self, project: str | None) -> TranscriptSegment:
        if (
            self.locator.time_ms is None
            or self.speaker_id is None
            or self.locale is None
            or self.confidence is None
        ):
            raise ValueError("Only timed, attributed fragments convert to transcript segments")
        return TranscriptSegment(
            id=self.id,
            meeting_id=self.source_id,
            speaker_id=self.speaker_id,
            start_ms=self.locator.time_ms[0],
            end_ms=self.locator.time_ms[1],
            text=self.text,
            confidence=self.confidence,
            version=self.version,
            locale=self.locale,
            project=project,
        )


@dataclass(frozen=True, slots=True)
class Section:
    id: str
    source_id: str
    parent_id: str | None
    ordinal: int
    level: int
    title: str

    def __post_init__(self) -> None:
        if self.level < 1:
            raise ValueError("level starts at 1")
        if not self.title.strip():
            raise ValueError("A section needs a title")


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    workspace_id: str
    owner_id: str
    kind: SourceKind
    title: str | None
    state: str
    created_at: datetime
    origin: str | None = None
    language: Locale | None = None
    content_hash: str | None = None
    version: int = 1
    tombstoned_at: datetime | None = None

    @classmethod
    def from_meeting(cls, meeting: Meeting) -> Source:
        return cls(
            id=meeting.id,
            workspace_id=meeting.workspace_id,
            owner_id=meeting.owner_id,
            kind=SourceKind.MEETING,
            title=meeting.title,
            state=meeting.state.value,
            created_at=meeting.created_at,
            tombstoned_at=meeting.tombstoned_at,
        )
