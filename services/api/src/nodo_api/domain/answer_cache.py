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
    items: list[dict[str, Any]] = []
    for citation in citations:
        item: dict[str, Any] = {
            "meeting_id": citation.meeting_id,
            "segment_id": citation.segment_id,
            "start_ms": citation.start_ms,
            "end_ms": citation.end_ms,
            "quote": citation.quote,
            "speaker_id": citation.speaker_id,
        }
        if citation.source_id is not None:
            item["source_id"] = citation.source_id
        if citation.fragment_id is not None:
            item["fragment_id"] = citation.fragment_id
        if citation.source_version is not None:
            item["source_version"] = citation.source_version
        if citation.locator is not None:
            item["locator"] = citation.locator.to_json()
        items.append(item)
    return items


def citations_from_json(items: list[dict[str, Any]]) -> tuple[Citation, ...]:
    from nodo_api.domain.sources import Locator

    return tuple(
        Citation(
            i.get("meeting_id", ""),
            i.get("segment_id", ""),
            i.get("start_ms", 0),
            i.get("end_ms", 0),
            i["quote"],
            i.get("speaker_id", ""),
            i.get("source_id"),
            i.get("fragment_id"),
            i.get("source_version"),
            Locator.from_json(i["locator"]) if i.get("locator") is not None else None,
        )
        for i in items
    )
