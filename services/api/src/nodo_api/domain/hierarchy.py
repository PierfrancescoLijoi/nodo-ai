"""Parent links between concepts and between collections (ADR-030).

One pure rule decides whether a parent link may exist: the non-rejected links always form an
acyclic graph in which no path has more than `MAX_DEPTH` links. A link that would break this is
refused, never repaired. The stores apply the rule inside their write transaction.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from nodo_api.domain.graph import (
    Entity,
    EntityType,
    Evidence,
    ExtractedRelation,
    Extraction,
    Relation,
    RelationKind,
    normalize_label,
)
from nodo_api.domain.sources import Fragment, Section

MAX_DEPTH = 6
HEADING_CONFIDENCE = 0.6
MAX_HEADING_EVIDENCE = 3

_HEADING_TYPES = frozenset({EntityType.PROJECT, EntityType.SYSTEM, EntityType.TOPIC})
_LEADING_ENUMERATOR = re.compile(r"^\d+(?:\.\d+)*[.)]?\s+")

LinkRefusal = Literal["self", "cycle", "too_deep"]

Step = Callable[[frozenset[str]], frozenset[str]]


@dataclass(frozen=True, slots=True)
class ParentSuggestion:
    candidate_id: str
    confidence: float
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CoMention:
    entity: Entity
    evidence: tuple[Evidence, ...]


def heading_proposals(
    extraction: Extraction,
    sections: Sequence[Section],
    fragments: Sequence[Fragment],
) -> tuple[ExtractedRelation, ...]:
    """Infer reviewable child-to-parent links from the document outline.

    Titles are used only to match an extracted concept. Returned values contain entity keys and
    fragment ids, never document text. Each cited fragment chooses the nearest matching ancestor;
    grouping those choices yields one deterministic proposal per child/parent pair.
    """
    concepts = tuple(entity for entity in extraction.entities if entity.type in _HEADING_TYPES)
    by_label = {}
    for entity in concepts:
        by_label.setdefault(normalize_label(entity.label), entity)

    by_section = {section.id: section for section in sections}
    heads = {}
    for section in sections:
        title = _LEADING_ENUMERATOR.sub("", section.title.strip())
        head = by_label.get(normalize_label(title))
        if head is not None:
            heads[section.id] = head

    ordered_fragments = sorted(fragments, key=lambda fragment: (fragment.ordinal, fragment.id))
    fragment_by_id = {fragment.id: fragment for fragment in ordered_fragments}
    grouped: dict[tuple[str, str], list[str]] = {}
    for entity in concepts:
        cited = {fragment_id for fragment_id in entity.evidence if fragment_id in fragment_by_id}
        for fragment in ordered_fragments:
            if fragment.id not in cited:
                continue
            section_id = fragment.section_id
            while section_id is not None:
                head = heads.get(section_id)
                if head is not None and head.key != entity.key:
                    evidence = grouped.setdefault((entity.key, head.key), [])
                    if len(evidence) < MAX_HEADING_EVIDENCE:
                        evidence.append(fragment.id)
                    break
                section = by_section.get(section_id)
                section_id = section.parent_id if section is not None else None

    return tuple(
        ExtractedRelation(child, parent, RelationKind.CHILD_OF, HEADING_CONFIDENCE, tuple(evidence))
        for (child, parent), evidence in grouped.items()
    )


def _levels(start: str, step: Step, max_depth: int, stop: str | None) -> int | None:
    """How many non-empty levels lie beyond `start`, or None when `stop` is among them.

    Level by level, without a visited set, so the count is the longest path; a walk still going
    after `max_depth + 1` levels (only corrupt, cyclic data does that) counts as too deep."""
    level = frozenset({start})
    for depth in range(max_depth + 1):
        level = step(level)
        if not level:
            return depth
        if stop is not None and stop in level:
            return None
    return max_depth + 1


def check_link(
    child: str,
    parent: str,
    parents_of: Step,
    children_of: Step,
    *,
    max_depth: int = MAX_DEPTH,
) -> LinkRefusal | None:
    """Why `child` may not be placed under `parent`, or None when it may."""
    if child == parent:
        return "self"
    up = _levels(parent, parents_of, max_depth, stop=child)
    if up is None:
        return "cycle"
    if up + 1 > max_depth:
        return "too_deep"
    down = _levels(child, children_of, max_depth, stop=None) or 0  # no stop: never None
    if up + 1 + down > max_depth:
        return "too_deep"
    return None


Direction = Literal["parents", "children", "both"]

# The parent links touching `ids`: upward (child in ids) when the flag is True, else downward.
Links = Callable[[frozenset[str], bool], Sequence[Relation]]


def walk_links(
    seed: str, links: Links, *, direction: Direction, depth: int, limit: int
) -> tuple[list[str], list[Relation], bool]:
    """Breadth-first walk over parent links: (node ids, seed first; edges; truncated).

    Each direction walks from the seed on its own (parents up, children down, both does both, so
    siblings are not reached). A visited set ends any cycle, so corrupt data still terminates;
    every traversed edge is kept once, also when it leads to a node already seen (a diamond)."""
    if not 1 <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be between 1 and {MAX_DEPTH}")
    order, seen, edges, truncated = [seed], {seed}, {}, False
    for up in {"parents": (True,), "children": (False,), "both": (True, False)}[direction]:
        frontier = frozenset({seed})
        for _ in range(depth):
            if not frontier:
                break
            found: list[str] = []
            for rel in sorted(links(frontier, up), key=lambda r: r.id):
                other = rel.target_id if up else rel.source_id
                if other not in seen:
                    if len(seen) >= limit:
                        truncated = True
                        continue
                    seen.add(other)
                    order.append(other)
                    found.append(other)
                edges.setdefault(rel.id, rel)
            frontier = frozenset(found)
    return order, list(edges.values()), truncated
