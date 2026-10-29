"""Deterministic stand-in for the model extractor: keyword rules over the transcript. It exists so
the whole pipeline (worker, graph, API) runs and is testable without a model; it is not meant to be
good."""

from __future__ import annotations

import re
from collections.abc import Mapping

from nodo_api.domain.graph import (
    EXISTING_PREFIX,
    Entity,
    EntityType,
    ExtractedEntity,
    ExtractedRelation,
    Extraction,
    RelationKind,
)
from nodo_api.domain.models import Locale, TranscriptSegment

PEOPLE = ("Marco", "Sara", "Giulia")
TOPICS = ("ingestion", "queue", "benchmark", "retrieval")
DECISION_WORDS = re.compile(r"\b(decid\w*|choos\w*|go with|confirmed|agreed)\b", re.IGNORECASE)
ACTION_WORDS = re.compile(r"\b(will|benchmark\w*|prepare\w*)\b", re.IGNORECASE)


class KeywordExtractor:
    def extract(
        self,
        segments: tuple[TranscriptSegment, ...],
        locale: Locale,
        known_decisions: tuple[Entity, ...],
        speakers: Mapping[str, str] | None = None,
    ) -> Extraction:
        entities: dict[str, ExtractedEntity] = {}
        relations: list[ExtractedRelation] = []

        def need(key: str, kind: EntityType, label: str, segment: str) -> None:
            if key in entities:
                prior = entities[key]
                entities[key] = ExtractedEntity(key, kind, prior.label, (*prior.evidence, segment))
            else:
                entities[key] = ExtractedEntity(key, kind, label, (segment,))

        for segment in segments:
            text = segment.text
            people = [p for p in PEOPLE if p.lower() in text.lower()]
            topics = [t for t in TOPICS if t in text.lower()]
            for person in people:
                need(f"p:{person}", EntityType.PERSON, person, segment.id)
            for topic in topics:
                need(f"t:{topic}", EntityType.TOPIC, topic.capitalize(), segment.id)
            if DECISION_WORDS.search(text):
                label = text.rstrip(".")[:120]
                need(f"d:{segment.id}", EntityType.DECISION, label, segment.id)
                relations += [
                    ExtractedRelation(
                        f"d:{segment.id}", f"p:{p}", RelationKind.PROPOSED_BY, 0.8, (segment.id,)
                    )
                    for p in people
                ]
                relations += [
                    ExtractedRelation(
                        f"d:{segment.id}", f"t:{t}", RelationKind.ABOUT, 0.8, (segment.id,)
                    )
                    for t in topics
                ]
                for old in known_decisions:
                    if "async" in text.lower() and "synchronous" in old.label.lower():
                        relations.append(
                            ExtractedRelation(
                                f"d:{segment.id}",
                                f"{EXISTING_PREFIX}{old.id}",
                                RelationKind.SUPERSEDES,
                                0.7,
                                (segment.id,),
                            )
                        )
            elif ACTION_WORDS.search(text) and people:
                need(f"a:{segment.id}", EntityType.ACTION, text.rstrip(".")[:120], segment.id)
                relations.append(
                    ExtractedRelation(
                        f"a:{segment.id}",
                        f"p:{people[0]}",
                        RelationKind.ASSIGNED_TO,
                        0.8,
                        (segment.id,),
                    )
                )
        summary = " ".join(s.text for s in segments[:2])[:300]
        return Extraction(summary, tuple(entities.values()), tuple(relations))
