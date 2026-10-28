"""Extraction over transcripts longer than one model call can read.

A local model has a context of a few thousand words, so a long meeting is cut into windows of whole
segments, each window is extracted on its own, and the results are merged: the same entity found in
two windows becomes one, with the evidence of both. Short transcripts go through untouched, so the
Azure path (a large context) behaves exactly as before."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace

from nodo_api.domain.graph import (
    EXISTING_PREFIX,
    Entity,
    ExtractedEntity,
    ExtractedRelation,
    Extraction,
    ExtractionError,
    normalize_label,
)
from nodo_api.domain.models import Locale, TranscriptSegment
from nodo_api.ports import Extractor

logger = logging.getLogger("nodo_extraction")
MAX_SPLITS = 3  # a window that still fails after being halved this many times is skipped
CHARS_PER_TOKEN = 3.0  # conservative for Polish and Danish, which tokenize into more pieces
MAX_SUMMARY_SENTENCES = 3
SUMMARY_LIMIT = 700


def windows(
    segments: Sequence[TranscriptSegment], max_tokens: int
) -> list[tuple[TranscriptSegment, ...]]:
    """Consecutive segments grouped so each group stays under the token budget."""
    budget = int(max_tokens * CHARS_PER_TOKEN)
    groups: list[list[TranscriptSegment]] = [[]]
    used = 0
    for segment in segments:
        size = len(segment.text) + 12  # speaker label and numbering the prompt adds
        if groups[-1] and used + size > budget:
            groups.append([])
            used = 0
        groups[-1].append(segment)
        used += size
    return [tuple(g) for g in groups if g]


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _summary(parts: Sequence[str]) -> str:
    """The first distinct sentences of the windows' summaries, whole sentences only. Windows of one
    meeting repeat each other, and cutting at a character count ends in the middle of a word."""
    chosen: list[str] = []
    seen: set[str] = set()
    for text in parts:
        for sentence in _SENTENCE_END.split(text.strip()):
            key = normalize_label(sentence)
            if key and key not in seen and len(" ".join([*chosen, sentence])) <= SUMMARY_LIMIT:
                seen.add(key)
                chosen.append(sentence)
            if len(chosen) == MAX_SUMMARY_SENTENCES:
                return " ".join(chosen)
    return " ".join(chosen)


def merge(parts: Sequence[Extraction]) -> Extraction:
    """One extraction from the extractions of consecutive windows."""
    canonical: dict[tuple[str, str], ExtractedEntity] = {}
    remap: list[dict[str, str]] = []
    for index, part in enumerate(parts):
        local: dict[str, str] = {}
        for entity in part.entities:
            identity = (entity.type.value, normalize_label(entity.label))
            known = canonical.get(identity)
            if known is None:
                key = f"w{index}:{entity.key}"
                canonical[identity] = replace(entity, key=key)
                local[entity.key] = key
            else:
                evidence = tuple(dict.fromkeys((*known.evidence, *entity.evidence)))
                canonical[identity] = replace(known, evidence=evidence)
                local[entity.key] = known.key
        remap.append(local)

    relations: dict[tuple[str, str, str], ExtractedRelation] = {}
    for part, local in zip(parts, remap, strict=True):
        for rel in part.relations:
            source = rel.source if rel.source.startswith(EXISTING_PREFIX) else local.get(rel.source)
            target = rel.target if rel.target.startswith(EXISTING_PREFIX) else local.get(rel.target)
            if source is None or target is None or source == target:
                continue
            identity = (source, rel.kind.value, target)
            known = relations.get(identity)
            if known is None:
                relations[identity] = replace(rel, source=source, target=target)
            else:
                relations[identity] = replace(
                    known,
                    confidence=max(known.confidence, rel.confidence),
                    evidence=tuple(dict.fromkeys((*known.evidence, *rel.evidence))),
                )
    summary = _summary([p.summary for p in parts])
    return Extraction(summary, tuple(canonical.values()), tuple(relations.values()))


class ChunkedExtractor:
    """Cuts a long transcript into windows, and a window the model can not answer into halves.

    A model that fails on one dense stretch must not cost the whole meeting: the stretch is split
    and retried, and only if halves keep failing is it left out (with a warning). If nothing at all
    could be extracted the error is raised, so the job is retried instead of finishing empty."""

    def __init__(self, inner: Extractor, max_tokens: int) -> None:
        self._inner = inner
        self._max_tokens = max_tokens

    def extract(
        self,
        segments: tuple[TranscriptSegment, ...],
        locale: Locale,
        known_decisions: tuple[Entity, ...],
        speakers: Mapping[str, str] | None = None,
    ) -> Extraction:
        parts = [
            self._window(p, locale, known_decisions, speakers, 0)
            for p in windows(segments, self._max_tokens)
        ]
        done = [p for p in parts if p is not None]
        if not done:
            raise ExtractionError("No part of the transcript could be extracted")
        return done[0] if len(parts) == 1 else merge(done)

    def _window(self, window, locale, known, speakers, depth) -> Extraction | None:
        try:
            return self._inner.extract(window, locale, known, speakers)
        except ExtractionError:
            if len(window) < 2 or depth >= MAX_SPLITS:
                logger.warning("extraction_window_skipped segments=%s depth=%s", len(window), depth)
                return None
        middle = len(window) // 2
        halves = [
            self._window(half, locale, known, speakers, depth + 1)
            for half in (window[:middle], window[middle:])
        ]
        done = [h for h in halves if h is not None]
        return merge(done) if done else None
