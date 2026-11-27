"""Deterministic stand-in for the model fragment extractor: the meeting keyword rules over the
fragments' text. It exists so the import pipeline runs and is testable without a model."""

from __future__ import annotations

from nodo_api.adapters.memory.extractor import KeywordExtractor
from nodo_api.domain.graph import Entity, Extraction
from nodo_api.domain.models import Locale, TranscriptSegment
from nodo_api.domain.sources import Fragment


class KeywordFragmentExtractor:
    def extract(
        self,
        fragments: tuple[Fragment, ...],
        section_path: tuple[str, ...],
        locale: Locale,
        known_decisions: tuple[Entity, ...] = (),
    ) -> Extraction:
        segments = tuple(
            TranscriptSegment(f.id, f.source_id, "S1", 0, 0, f.text, 1.0, f.version, locale)
            for f in fragments
        )
        return KeywordExtractor().extract(segments, locale, known_decisions)
