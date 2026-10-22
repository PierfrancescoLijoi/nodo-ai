"""Extraction over transcripts longer than one model call can read.

A local model has a context of a few thousand words, so a long meeting is cut into windows of whole
segments, each window is extracted on its own, and the results are merged: the same entity found in
two windows becomes one, with the evidence of both. Short transcripts go through untouched, so the
Azure path (a large context) behaves exactly as before."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from nodo_api.domain.graph import (
    EXISTING_PREFIX,
    Entity,
    ExtractedEntity,
    ExtractedRelation,
    Extraction,
    normalize_label,
)
from nodo_api.domain.models import Locale, TranscriptSegment
from nodo_api.ports import Extractor

CHARS_PER_TOKEN = 3.0  # conservative for Polish and Danish, which tokenize into more pieces
MAX_SUMMARY_PARTS = 4
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
    summaries = [p.summary.strip() for p in parts if p.summary.strip()]
    summary = " ".join(summaries[:MAX_SUMMARY_PARTS])[:SUMMARY_LIMIT]
    return Extraction(summary, tuple(canonical.values()), tuple(relations.values()))


class ChunkedExtractor:
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
        parts = windows(segments, self._max_tokens)
        if len(parts) <= 1:
            return self._inner.extract(segments, locale, known_decisions, speakers)
        return merge([self._inner.extract(p, locale, known_decisions, speakers) for p in parts])
