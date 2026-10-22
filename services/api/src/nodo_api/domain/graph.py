"""The temporal knowledge graph: entities, typed relations and the evidence behind each relation.

Pure rules, no I/O. Every relation must be backed by at least one transcript segment, an entity
identity is a function of its type and normalised name (so re-running an extraction never creates a
duplicate), and what the model proposes is cleaned by `validate_extraction` before it can be stored
(ADR-004, ADR-012).
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

NAMESPACE = uuid.UUID("5d3b7a8e-2f1c-4c6a-9a55-3f0e6a1b7c20")
MAX_LABEL = 120
MAX_ENTITIES = 60
MAX_RELATIONS = 120
EXISTING_PREFIX = "existing:"


class EntityType(StrEnum):
    PERSON = "person"
    PROJECT = "project"
    SYSTEM = "system"
    TOPIC = "topic"
    DECISION = "decision"
    ACTION = "action"
    MEETING = "meeting"


class RelationKind(StrEnum):
    MENTIONED_IN = "MENTIONED_IN"
    ABOUT = "ABOUT"
    DECIDED_IN = "DECIDED_IN"
    PROPOSED_BY = "PROPOSED_BY"
    ASSIGNED_TO = "ASSIGNED_TO"
    DEPENDS_ON = "DEPENDS_ON"
    SUPERSEDES = "SUPERSEDES"
    CONTRADICTS = "CONTRADICTS"


class ReviewStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


_ANY = frozenset(EntityType) - {EntityType.MEETING}
_THINGS = frozenset({EntityType.PROJECT, EntityType.SYSTEM, EntityType.TOPIC})
_DECISION = frozenset({EntityType.DECISION})
_PERSON = frozenset({EntityType.PERSON})
_MEETING = frozenset({EntityType.MEETING})

# (allowed source types, allowed target types) per relation kind.
ENDPOINTS: dict[RelationKind, tuple[frozenset[EntityType], frozenset[EntityType]]] = {
    RelationKind.MENTIONED_IN: (_ANY, _MEETING),
    RelationKind.ABOUT: (frozenset({EntityType.DECISION, EntityType.ACTION}), _THINGS),
    RelationKind.DECIDED_IN: (_DECISION, _MEETING),
    RelationKind.PROPOSED_BY: (_DECISION, _PERSON),
    RelationKind.ASSIGNED_TO: (frozenset({EntityType.ACTION}), _PERSON),
    RelationKind.DEPENDS_ON: (_THINGS, _THINGS),
    RelationKind.SUPERSEDES: (_DECISION, _DECISION),
    RelationKind.CONTRADICTS: (_DECISION, _DECISION),
}

# Facts of record that the system derives itself are approved at once; what the model infers waits
# for a person.
AUTO_APPROVED = frozenset({RelationKind.MENTIONED_IN})

_LETTERS = {"ł": "l", "ø": "o", "æ": "ae", "đ": "d", "ß": "ss"}


def normalize_label(text: str) -> str:
    """Case, accent and spacing insensitive form used to recognise the same thing again."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    decomposed = unicodedata.normalize("NFD", folded)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    mapped = "".join(_LETTERS.get(c, c) for c in stripped)
    return " ".join(mapped.split())


def entity_id(workspace_id: str, owner_id: str, kind: EntityType, normalized: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"e|{workspace_id}|{owner_id}|{kind.value}|{normalized}"))


def meeting_entity_id(workspace_id: str, owner_id: str, meeting_id: str) -> str:
    return entity_id(workspace_id, owner_id, EntityType.MEETING, meeting_id)


def relation_id(
    workspace_id: str, owner_id: str, source_id: str, kind: RelationKind, target_id: str
) -> str:
    return str(
        uuid.uuid5(NAMESPACE, f"r|{workspace_id}|{owner_id}|{source_id}|{kind.value}|{target_id}")
    )


@dataclass(frozen=True, slots=True)
class Evidence:
    meeting_id: str
    segment_id: str


@dataclass(frozen=True, slots=True)
class Entity:
    id: str
    type: EntityType
    label: str
    status: str | None = None
    effective_at: datetime | None = None
    due_at: datetime | None = None
    meeting_id: str | None = None  # set on meeting nodes only


@dataclass(frozen=True, slots=True)
class Relation:
    id: str
    source_id: str
    target_id: str
    kind: RelationKind
    status: ReviewStatus
    confidence: float
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True)
class Subgraph:
    nodes: tuple[Entity, ...] = ()
    edges: tuple[Relation, ...] = ()
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class ExportedRef:
    """An endpoint by what it is, not by id: ids differ between workspaces, labels do not.
    type and label both None means the meeting itself."""

    type: EntityType | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class ExportedEntity:
    type: EntityType
    label: str
    evidence: tuple[str, ...]
    due_at: datetime | None = None
    effective_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExportedRelation:
    source: ExportedRef
    kind: RelationKind
    target: ExportedRef
    confidence: float
    status: ReviewStatus
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GraphExport:
    """What one meeting contributed to the graph, including the review decisions on it (ADR-015)."""

    entities: tuple[ExportedEntity, ...] = ()
    relations: tuple[ExportedRelation, ...] = ()


@dataclass(frozen=True, slots=True)
class MeetingRef:
    id: str
    title: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExtractedEntity:
    key: str
    type: EntityType
    label: str
    evidence: tuple[str, ...]
    status: str | None = None
    effective_at: datetime | None = None
    due_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExtractedRelation:
    source: str
    target: str
    kind: RelationKind
    confidence: float
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Extraction:
    summary: str = ""
    entities: tuple[ExtractedEntity, ...] = ()
    relations: tuple[ExtractedRelation, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphWriteResult:
    entities: int = 0
    relations: int = 0


@dataclass(frozen=True, slots=True)
class MeetingDigest:
    people: tuple[Entity, ...] = ()
    topics: tuple[Entity, ...] = ()
    decisions: tuple[Entity, ...] = ()
    actions: tuple[Entity, ...] = ()
    action_owners: dict[str, str] = field(default_factory=dict)
    review: dict[str, str] = field(default_factory=dict)  # entity id -> proposed | approved


@dataclass(frozen=True, slots=True)
class Change:
    id: str
    new_label: str
    old_label: str
    meeting_id: str | None
    at: datetime | None


ANONYMOUS_SPEAKER = re.compile(r"s[0-9]+")


def validate_extraction(
    raw: Extraction,
    valid_segment_ids: frozenset[str],
    known_entities: dict[str, EntityType] | None = None,
) -> Extraction:
    """Keep only what is well formed and backed by evidence. Never raises: bad items are dropped.

    `known_entities` maps ids of entities that already exist (offered to the model as
    `existing:<id>`) to their type, so a new decision can supersede an older one.
    """
    known = known_entities or {}
    entities = _clean_entities(raw.entities, valid_segment_ids)
    types = {e.key: e.type for e in entities}
    types.update({f"{EXISTING_PREFIX}{eid}": t for eid, t in known.items()})
    relations = _clean_relations(raw.relations, types, valid_segment_ids)
    return replace(raw, summary=raw.summary.strip()[:2000], entities=entities, relations=relations)


def _clean_entities(
    entities: tuple[ExtractedEntity, ...], valid: frozenset[str]
) -> tuple[ExtractedEntity, ...]:
    seen: set[tuple[EntityType, str]] = set()
    kept: list[ExtractedEntity] = []
    for item in entities:
        label = " ".join(item.label.split())[:MAX_LABEL]
        normalized = normalize_label(label)
        evidence = tuple(dict.fromkeys(s for s in item.evidence if s in valid))
        if item.type is EntityType.MEETING or not normalized or not evidence:
            continue
        if item.type is EntityType.PERSON and ANONYMOUS_SPEAKER.fullmatch(normalized):
            continue  # "S1" is a diarization label, not someone's name (ADR-004, ADR-013)
        identity = (item.type, normalized)
        if identity in seen or any(e.key == item.key for e in kept):
            continue
        seen.add(identity)
        kept.append(replace(item, label=label, evidence=evidence))
        if len(kept) == MAX_ENTITIES:
            break
    return tuple(kept)


def _clean_relations(
    relations: tuple[ExtractedRelation, ...],
    types: dict[str, EntityType],
    valid: frozenset[str],
) -> tuple[ExtractedRelation, ...]:
    kept: dict[tuple[str, RelationKind, str], ExtractedRelation] = {}
    for item in relations:
        evidence = tuple(dict.fromkeys(s for s in item.evidence if s in valid))
        source_type, target_type = types.get(item.source), types.get(item.target)
        if not evidence or item.source == item.target or source_type is None or target_type is None:
            continue
        allowed_source, allowed_target = ENDPOINTS[item.kind]
        if item.kind in AUTO_APPROVED or source_type not in allowed_source:
            continue
        if target_type not in allowed_target:
            continue
        # A decision can not both replace and be replaced by the same decision.
        if (
            item.kind is RelationKind.SUPERSEDES
            and (item.target, RelationKind.SUPERSEDES, item.source) in kept
        ):
            continue
        confidence = min(1.0, max(0.0, item.confidence))
        key = (item.source, item.kind, item.target)
        kept.setdefault(key, replace(item, confidence=confidence, evidence=evidence))
        if len(kept) == MAX_RELATIONS:
            break
    return tuple(kept.values())
