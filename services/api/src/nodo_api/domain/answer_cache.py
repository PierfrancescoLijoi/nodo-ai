"""What the answer cache stores and how an entry is addressed (ADR-026)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from nodo_api.domain.models import Answerability, Citation

_SPACES = re.compile(r"\s+")


def question_hash(question: str) -> str:
    """The same question written with other spacing or case is the same question."""
    return hashlib.sha256(_SPACES.sub(" ", question.strip().lower()).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AnswerKey:
    """An entry answers a question only if everything that shapes the answer is the same.
    `version` names the models and prompts that wrote it; `filters` is the canonical form of the
    search filters; `question` is `question_hash` of the question that was answered."""

    version: str
    locale: str
    filters: str
    question: str


@dataclass(frozen=True, slots=True)
class CachedAnswer:
    answer: str
    answerability: Answerability
    citations: tuple[Citation, ...]
    related_entities: tuple[str, ...]
    similarity: float = 1.0  # how close the new question is to the one that was answered


def citations_to_json(citations: tuple[Citation, ...]) -> list[dict[str, Any]]:
    return [
        {
            "meeting_id": c.meeting_id,
            "segment_id": c.segment_id,
            "start_ms": c.start_ms,
            "end_ms": c.end_ms,
            "quote": c.quote,
            "speaker_id": c.speaker_id,
        }
        for c in citations
    ]


def citations_from_json(items: list[dict[str, Any]]) -> tuple[Citation, ...]:
    return tuple(
        Citation(
            i["meeting_id"],
            i["segment_id"],
            i["start_ms"],
            i["end_ms"],
            i["quote"],
            i["speaker_id"],
        )
        for i in items
    )
