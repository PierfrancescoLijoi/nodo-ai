"""In-memory knowledge graph. It is the reference for the semantics every GraphStore must have: the
same contract tests run against this class and against PostgreSQL (tests/graph_contract.py)."""

from __future__ import annotations

import copy
import itertools
import logging
import math
import threading
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from nodo_api.domain.graph import (
    AUTO_APPROVED,
    EXISTING_PREFIX,
    ORIGIN_TYPES,
    Change,
    Entity,
    EntityType,
    Evidence,
    Extraction,
    GraphWriteResult,
    MeetingDigest,
    MeetingRef,
    Relation,
    RelationKind,
    ReviewStatus,
    SourceRef,
    Subgraph,
    entity_id,
    meeting_entity_id,
    normalize_label,
    relation_id,
    source_entity_id,
)
from nodo_api.domain.hierarchy import CoMention, Direction, check_link, walk_links
from nodo_api.domain.sources import SourceKind
from nodo_api.errors import NotFoundError, refusal_error

logger = logging.getLogger("nodo_graph")

Key = tuple[str, str]  # (workspace_id, owner_id)


@dataclass(slots=True)
class _Row:
    relation: Relation
    seq: int
    batch: int = 0  # the write that created it, as created_at in SQLite (one stamp per write)


@dataclass(slots=True)
class _Partition:
    entities: dict[str, Entity] = field(default_factory=dict)
    normalized: dict[str, str] = field(default_factory=dict)
    touched: dict[str, int] = field(default_factory=dict)
    relations: dict[str, _Row] = field(default_factory=dict)
    vectors: dict[str, list[float]] = field(default_factory=dict)


class MemoryGraphStore:
    def __init__(self) -> None:
        self._parts: dict[Key, _Partition] = {}
        self._clock = itertools.count(1)
        self._lock = threading.RLock()  # writes are serialised, as SQLite's write lock does

    def _part(self, workspace_id: str, owner_id: str) -> _Partition:
        return self._parts.setdefault((workspace_id, owner_id), _Partition())

    # ------------------------------------------------------------------ writes

    def upsert_extraction(
        self, workspace_id: str, owner_id: str, meeting: MeetingRef, extraction: Extraction
    ) -> GraphWriteResult:
        node = Entity(
            meeting_entity_id(workspace_id, owner_id, meeting.id),
            EntityType.MEETING,
            meeting.title,
            None,
            meeting.created_at,
            None,
            meeting.id,
        )
        with self._lock:
            part = self._part(workspace_id, owner_id)
            self._drop_meeting_evidence(part, meeting.id)
            self._write(part, workspace_id, owner_id, node, meeting.id, extraction)
            mine = sum(
                1
                for r in part.relations.values()
                if any(e.source_id == meeting.id for e in r.relation.evidence)
            )
        return GraphWriteResult(len(extraction.entities) + 1, mine)

    def replace_source_evidence(
        self,
        workspace_id: str,
        user_id: str,
        source: SourceRef,
        extraction: Extraction,
        *,
        dropped_fragment_ids: frozenset[str] | None = None,
    ) -> None:
        if source.kind is SourceKind.MEETING:
            raise ValueError("A meeting is written with upsert_extraction")
        node = Entity(
            source_entity_id(workspace_id, user_id, source.id),
            EntityType.SOURCE,
            source.label,
            None,
            source.created_at,
            source_id=source.id,
        )
        with self._lock:
            # Work on a copy and swap it in at the end: a failure half way keeps the old graph.
            part = copy.deepcopy(self._part(workspace_id, user_id))
            self._drop_meeting_evidence(part, source.id, dropped_fragment_ids)
            self._write(part, workspace_id, user_id, node, source.id, extraction)
            # The node follows the current title; concepts keep their first-writer label.
            if node.id in part.entities:
                part.entities[node.id] = replace(part.entities[node.id], label=node.label)
            self._parts[(workspace_id, user_id)] = part

    def _write(
        self,
        part: _Partition,
        workspace_id: str,
        owner_id: str,
        node: Entity,
        origin_id: str,
        extraction: Extraction,
    ) -> None:
        refresh = node.type is EntityType.MEETING
        before, batch = set(part.relations), next(self._clock)
        self._put_entity(part, node, origin_id, refresh)
        created_at = node.effective_at
        ids: dict[str, str] = {}
        for item in extraction.entities:
            normalized = normalize_label(item.label)
            eid = entity_id(workspace_id, owner_id, item.type, normalized)
            ids[item.key] = eid
            existing = part.entities.get(eid)
            status = existing.status if existing and existing.status else item.status
            if item.type is EntityType.DECISION and status is None:
                status = "active"
            effective = item.effective_at or (
                created_at if item.type is EntityType.DECISION else None
            )
            self._put_entity(
                part,
                Entity(eid, item.type, item.label, status, effective, item.due_at),
                normalized,
                refresh,
            )
        for key, eid in list(ids.items()):
            self._add_relation(
                part,
                workspace_id,
                owner_id,
                eid,
                RelationKind.MENTIONED_IN,
                node.id,
                1.0,
                self._evidence(origin_id, _by_key(extraction, key).evidence),
            )
            if part.entities[eid].type is EntityType.DECISION:
                self._add_relation(
                    part,
                    workspace_id,
                    owner_id,
                    eid,
                    RelationKind.DECIDED_IN,
                    node.id,
                    1.0,
                    self._evidence(origin_id, _by_key(extraction, key).evidence),
                )
        hierarchy = []
        for rel in extraction.relations:
            source, target = (
                self._resolve(part, ids, rel.source),
                self._resolve(part, ids, rel.target),
            )
            if source is None or target is None:
                continue
            if rel.kind is RelationKind.CHILD_OF:
                hierarchy.append((source, target, rel))
                continue
            self._add_relation(
                part,
                workspace_id,
                owner_id,
                source,
                rel.kind,
                target,
                rel.confidence,
                self._evidence(origin_id, rel.evidence),
            )
        # Parent links one at a time, in extraction order, each checked against those before it.
        refused: Counter[str] = Counter()

        def kept(item) -> bool:
            row = part.relations.get(
                relation_id(workspace_id, owner_id, item[0], RelationKind.CHILD_OF, item[1])
            )
            return row is not None and row.relation.status is not ReviewStatus.REJECTED

        # Links already on record go first, so a reviewed link never loses to a new proposal.
        hierarchy.sort(key=lambda item: not kept(item))
        for source, target, rel in hierarchy:
            row = part.relations.get(
                relation_id(workspace_id, owner_id, source, RelationKind.CHILD_OF, target)
            )
            # A row a reimport just stripped of evidence is about to be swept: it counts as new.
            if row is None or (
                row.relation.status is not ReviewStatus.REJECTED and not row.relation.evidence
            ):
                code = check_link(source, target, *_hierarchy_steps(part))
                if code is not None:
                    refused[code] += 1
                    continue
            self._add_relation(
                part,
                workspace_id,
                owner_id,
                source,
                RelationKind.CHILD_OF,
                target,
                rel.confidence,
                self._evidence(origin_id, rel.evidence),
            )
        for code, count in sorted(refused.items()):
            logger.info("hierarchy_link_refused code=%s count=%d", code, count)
        for rid in part.relations.keys() - before:
            part.relations[rid].batch = batch
        self._sweep(part)
        self._restore_supersession(part)
        for row in part.relations.values():
            if (
                row.relation.kind is RelationKind.SUPERSEDES
                and row.relation.status is ReviewStatus.APPROVED
            ):
                self._set_superseded(part, row.relation.target_id, True)

    @staticmethod
    def _evidence(meeting_id: str, segment_ids: Sequence[str]) -> tuple[Evidence, ...]:
        return tuple(Evidence(meeting_id, s) for s in segment_ids)

    @staticmethod
    def _resolve(part: _Partition, ids: dict[str, str], key: str) -> str | None:
        if key.startswith(EXISTING_PREFIX):
            eid = key[len(EXISTING_PREFIX) :]
            return eid if eid in part.entities else None
        return ids.get(key)

    def _put_entity(
        self, part: _Partition, entity: Entity, normalized: str, refresh: bool = True
    ) -> None:
        """Insert or merge, as SQLite does: an existing entity keeps its label, status and
        effective date, and only a meeting write (`refresh`) moves its recency."""
        existing = part.entities.get(entity.id)
        if existing is not None:
            entity = replace(
                existing,
                status=existing.status or entity.status,
                effective_at=existing.effective_at or entity.effective_at,
                due_at=entity.due_at or existing.due_at,
            )
        part.entities[entity.id] = entity
        part.normalized.setdefault(entity.id, normalized)
        if existing is None or refresh:
            part.touched[entity.id] = next(self._clock)

    def _add_relation(
        self, part, workspace_id, owner_id, source, kind, target, confidence, evidence
    ) -> None:
        rid = relation_id(workspace_id, owner_id, source, kind, target)
        row = part.relations.get(rid)
        if row is None:
            status = ReviewStatus.APPROVED if kind in AUTO_APPROVED else ReviewStatus.PROPOSED
            relation = Relation(rid, source, target, kind, status, confidence, None, None, evidence)
            part.relations[rid] = _Row(relation, next(self._clock))
            return
        merged = tuple(dict.fromkeys((*row.relation.evidence, *evidence)))
        row.relation = replace(
            row.relation, confidence=max(row.relation.confidence, confidence), evidence=merged
        )

    def _drop_meeting_evidence(
        self, part: _Partition, meeting_id: str, fragment_ids: frozenset[str] | None = None
    ) -> None:
        """Drop the source's evidence (only the listed fragments when given)."""
        for row in part.relations.values():
            kept = tuple(
                e
                for e in row.relation.evidence
                if e.source_id != meeting_id
                or (fragment_ids is not None and e.fragment_id not in fragment_ids)
            )
            if len(kept) != len(row.relation.evidence):
                row.relation = replace(row.relation, evidence=kept)

    def _sweep(self, part: _Partition) -> None:
        """Remove what has lost its support, until nothing more falls.

        A relation needs evidence. An entity needs a mention in a meeting or source (a meeting or
        source node needs any relation). An entity nobody mentions any more disappears together
        with the relations that pointed at it, so deleting a meeting also deletes the labels it
        contributed.
        """
        changed = True
        while changed:
            changed = False
            for rid in [r for r, row in part.relations.items() if not row.relation.evidence]:
                del part.relations[rid]
                changed = True
            mentioned = {
                r.relation.source_id
                for r in part.relations.values()
                if r.relation.kind is RelationKind.MENTIONED_IN
                and r.relation.status is not ReviewStatus.REJECTED
            }
            touched = {r.relation.source_id for r in part.relations.values()}
            touched |= {r.relation.target_id for r in part.relations.values()}
            for eid, entity in list(part.entities.items()):
                alive = eid in touched if entity.type in ORIGIN_TYPES else eid in mentioned
                if not alive:
                    del part.entities[eid]
                    part.normalized.pop(eid, None)
                    part.touched.pop(eid, None)
                    changed = True
            for rid, row in list(part.relations.items()):
                rel = row.relation
                if rel.source_id not in part.entities or rel.target_id not in part.entities:
                    del part.relations[rid]
                    changed = True

    def _set_superseded(self, part: _Partition, entity_id_: str, superseded: bool) -> None:
        entity = part.entities.get(entity_id_)
        if entity is not None and entity.type is EntityType.DECISION:
            part.entities[entity_id_] = replace(
                entity, status="superseded" if superseded else "active"
            )

    def _restore_supersession(self, part: _Partition) -> None:
        """A decision is superseded only while an approved relation says so."""
        replaced = {
            r.relation.target_id
            for r in part.relations.values()
            if r.relation.kind is RelationKind.SUPERSEDES
            and r.relation.status is ReviewStatus.APPROVED
        }
        for eid, entity in list(part.entities.items()):
            if (
                entity.type is EntityType.DECISION
                and entity.status == "superseded"
                and eid not in replaced
            ):
                part.entities[eid] = replace(entity, status="active")

    def pending_review(
        self, workspace_id: str, owner_id: str, limit: int, *, meetings_only: bool = False
    ) -> Subgraph:
        part = self._parts.get((workspace_id, owner_id))
        if part is None:
            return Subgraph()
        view = _View(part, True, None) if meetings_only else None
        waiting = [
            (r.seq, shown)
            for r in part.relations.values()
            if r.relation.status is ReviewStatus.PROPOSED
            and (shown := _show(view, r.relation)) is not None
        ]
        waiting.sort(key=lambda pair: (-pair[0], pair[1].id))
        chosen = [rel for _, rel in waiting[:limit]]
        ids = list(dict.fromkeys(i for r in chosen for i in (r.source_id, r.target_id)))
        nodes = tuple(part.entities[i] for i in ids if i in part.entities)
        return Subgraph(nodes, tuple(chosen), len(waiting) > limit)

    def entities_missing_vectors(
        self, workspace_id: str, owner_id: str, limit: int, dimensions: int | None
    ) -> tuple[Entity, ...]:
        part = self._parts.get((workspace_id, owner_id))
        if part is None:
            return ()
        missing = [
            e
            for e in part.entities.values()
            if e.type not in ORIGIN_TYPES
            and (
                e.id not in part.vectors
                or (dimensions is not None and len(part.vectors[e.id]) != dimensions)
            )
        ]
        return tuple(sorted(missing, key=lambda e: e.id)[:limit])

    def put_entity_vectors(
        self, workspace_id: str, owner_id: str, vectors: Mapping[str, Sequence[float]]
    ) -> None:
        with self._lock:
            part = self._parts.get((workspace_id, owner_id))
            if part is None:
                return
            for key, vector in vectors.items():
                if key in part.entities:
                    part.vectors[key] = [float(x) for x in vector]

    def similar_entities(
        self,
        workspace_id: str,
        owner_id: str,
        vector: Sequence[float],
        limit: int,
        min_similarity: float,
    ) -> tuple[Entity, ...]:
        part = self._parts.get((workspace_id, owner_id))
        if part is None:
            return ()
        query = [float(x) for x in vector]
        norm = math.sqrt(sum(x * x for x in query)) or 1.0
        scored = []
        for key, stored in part.vectors.items():
            entity = part.entities.get(key)
            if entity is None or entity.type in ORIGIN_TYPES or len(stored) != len(query):
                continue
            other = math.sqrt(sum(x * x for x in stored)) or 1.0
            cosine = sum(a * b for a, b in zip(query, stored, strict=True)) / (norm * other)
            if cosine >= min_similarity:
                scored.append((cosine, entity))
        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        return tuple(entity for _, entity in scored[:limit])

    def evidence_segment_ids(
        self, workspace_id: str, owner_id: str, meeting_id: str
    ) -> frozenset[str]:
        part = self._parts.get((workspace_id, owner_id))
        if part is None:
            return frozenset()
        return frozenset(
            e.fragment_id
            for row in part.relations.values()
            for e in row.relation.evidence
            if e.source_id == meeting_id
        )

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        with self._lock:
            for (ws, _), part in self._parts.items():
                if ws != workspace_id:
                    continue
                self._drop_meeting_evidence(part, meeting_id)
                self._sweep(part)
                self._restore_supersession(part)

    def review_relation(
        self,
        workspace_id: str,
        owner_id: str,
        relation_id_: str,
        status: ReviewStatus,
        *,
        meetings_only: bool = False,
    ) -> Relation:
        with self._lock:
            reviewed = self._review(workspace_id, owner_id, relation_id_, status, meetings_only)
            view = _View(self._part(workspace_id, owner_id), True, None) if meetings_only else None
            return _show(view, reviewed) or reviewed

    def _review(
        self,
        workspace_id: str,
        owner_id: str,
        relation_id_: str,
        status: ReviewStatus,
        meetings_only: bool = False,
    ) -> Relation:
        part = self._part(workspace_id, owner_id)
        row = part.relations.get(relation_id_)
        if row is None or status is ReviewStatus.PROPOSED:
            raise NotFoundError if row is None else ValueError("A review is approved or rejected")
        if meetings_only and _show(_View(part, True, None), row.relation) is None:
            raise NotFoundError  # hidden from that view: nothing is written
        rel = row.relation
        if (
            rel.kind is RelationKind.CHILD_OF
            and status is ReviewStatus.APPROVED
            and rel.status is ReviewStatus.REJECTED
        ):
            code = check_link(rel.source_id, rel.target_id, *_hierarchy_steps(part, exclude=rel.id))
            if code is not None:
                raise refusal_error(code)
        row.relation = replace(row.relation, status=status)
        if row.relation.kind is RelationKind.SUPERSEDES:
            now = datetime.now(UTC)
            if status is ReviewStatus.APPROVED:
                row.relation = replace(row.relation, valid_from=now)
                self._set_superseded(part, row.relation.target_id, True)
            else:
                self._restore_supersession(part)
        return row.relation

    # ------------------------------------------------------------------- reads

    def find_entities(
        self,
        workspace_id: str,
        owner_id: str,
        terms: Sequence[str],
        limit: int,
        types: frozenset[EntityType] | None = None,
        *,
        meetings_only: bool = False,
        source_ids: frozenset[str] | None = None,
    ) -> tuple[Entity, ...]:
        part = self._part(workspace_id, owner_id)
        view = (
            _View(part, meetings_only, source_ids)
            if meetings_only or source_ids is not None
            else None
        )
        wanted = [t for t in (normalize_label(x) for x in terms) if len(t) >= 3]
        scored = []
        for eid, entity in part.entities.items():
            if entity.type in ORIGIN_TYPES or (types and entity.type not in types):
                continue
            if view is not None and eid not in view.entities:
                continue
            score = sum(1 for t in wanted if t in part.normalized[eid])
            if score:
                scored.append((-score, -part.touched[eid], eid, entity))
        return tuple(e for *_, e in sorted(scored, key=lambda x: x[:3]))[:limit]

    def overview(
        self, workspace_id: str, owner_id: str, limit: int, *, meetings_only: bool = False
    ) -> tuple[Entity, ...]:
        part = self._part(workspace_id, owner_id)
        view = _View(part, True, None) if meetings_only else None
        ordered = sorted(
            (
                e
                for e in part.entities.values()
                if e.type not in ORIGIN_TYPES and (view is None or e.id in view.entities)
            ),
            key=lambda e: (-part.touched[e.id], e.id),
        )
        return tuple(ordered[:limit])

    def recent_decisions(self, workspace_id: str, owner_id: str, limit: int) -> tuple[Entity, ...]:
        part = self._part(workspace_id, owner_id)
        decisions = [e for e in part.entities.values() if e.type is EntityType.DECISION]
        decisions.sort(key=lambda e: (-part.touched[e.id], e.id))
        return tuple(decisions[:limit])

    def neighborhood(
        self,
        workspace_id: str,
        owner_id: str,
        seed_ids: Sequence[str],
        *,
        depth: int,
        node_limit: int,
        fanout: int,
        include_proposed: bool,
        as_of: datetime | None,
        meetings_only: bool = False,
        source_ids: frozenset[str] | None = None,
    ) -> Subgraph:
        part = self._part(workspace_id, owner_id)
        view = (
            _View(part, meetings_only, source_ids)
            if meetings_only or source_ids is not None
            else None
        )
        shown = part.entities.keys() if view is None else view.entities
        moment = as_of or datetime.now(UTC)
        allowed = {ReviewStatus.APPROVED} | ({ReviewStatus.PROPOSED} if include_proposed else set())
        order = [s for s in dict.fromkeys(seed_ids) if s in shown][:node_limit]
        seen = set(order)
        frontier, collected, truncated = (
            list(order),
            {},
            len(order) < len(set(seed_ids) & set(shown)),
        )
        for _ in range(depth):
            nxt: list[str] = []
            for node in frontier:
                incident = self._incident(part, node, allowed, moment, view)
                if len(incident) > fanout:
                    truncated = True
                for rel in incident[:fanout]:
                    collected[rel.id] = rel
                    other = rel.target_id if rel.source_id == node else rel.source_id
                    if other in seen:
                        continue
                    if len(seen) >= node_limit:
                        truncated = True
                        continue
                    seen.add(other)
                    order.append(other)
                    nxt.append(other)
            frontier = nxt
        edges = tuple(r for r in collected.values() if r.source_id in seen and r.target_id in seen)
        return Subgraph(tuple(part.entities[i] for i in order), edges, truncated)

    @staticmethod
    def _incident(
        part: _Partition, node: str, allowed, moment: datetime, view: _View | None
    ) -> list[Relation]:
        found = []
        for row in part.relations.values():
            rel = _show(view, row.relation)
            if rel is None or node not in (rel.source_id, rel.target_id):
                continue
            if rel.status not in allowed:
                continue
            if (rel.valid_from and rel.valid_from > moment) or (
                rel.valid_to and rel.valid_to <= moment
            ):
                continue
            found.append(rel)
        found.sort(key=lambda r: (-r.confidence, r.id))
        return found

    def meeting_digests(
        self, workspace_id: str, owner_id: str, meeting_ids: Sequence[str]
    ) -> dict[str, MeetingDigest]:
        part = self._part(workspace_id, owner_id)
        by_node = {meeting_entity_id(workspace_id, owner_id, m): m for m in meeting_ids}
        buckets: dict[str, dict[EntityType, dict[str, Entity]]] = {m: {} for m in meeting_ids}
        decided: dict[str, str] = {}
        for row in part.relations.values():
            rel = row.relation
            meeting = by_node.get(rel.target_id)
            if (
                meeting is None
                or rel.status is ReviewStatus.REJECTED
                or rel.kind not in (RelationKind.MENTIONED_IN, RelationKind.DECIDED_IN)
            ):
                continue
            entity = part.entities.get(rel.source_id)
            if entity is not None:
                buckets[meeting].setdefault(entity.type, {})[entity.id] = entity
                if rel.kind is RelationKind.DECIDED_IN:
                    decided[entity.id] = rel.status.value
        owners, assigned = self._action_owners(part)
        result = {}
        for meeting, groups in buckets.items():
            actions = tuple(sorted(groups.get(EntityType.ACTION, {}).values(), key=lambda e: e.id))
            result[meeting] = MeetingDigest(
                people=_sorted(groups.get(EntityType.PERSON)),
                topics=_sorted(groups.get(EntityType.TOPIC)),
                decisions=_sorted(groups.get(EntityType.DECISION)),
                actions=actions,
                action_owners={a.id: owners[a.id] for a in actions if a.id in owners},
                review={
                    **decided,
                    **{a.id: assigned.get(a.id, "proposed") for a in actions},
                },
            )
        return result

    @staticmethod
    def _action_owners(part: _Partition) -> tuple[dict[str, str], dict[str, str]]:
        owners: dict[str, str] = {}
        statuses: dict[str, str] = {}
        for r in part.relations.values():
            rel = r.relation
            if (
                rel.kind is RelationKind.ASSIGNED_TO
                and rel.status is not ReviewStatus.REJECTED
                and rel.target_id in part.entities
            ):
                owners[rel.source_id] = part.entities[rel.target_id].label
                statuses[rel.source_id] = rel.status.value
        return owners, statuses

    def recent_changes(
        self, workspace_id: str, owner_id: str, limit: int, *, meetings_only: bool = False
    ) -> tuple[Change, ...]:
        part = self._part(workspace_id, owner_id)
        view = _View(part, True, None) if meetings_only else None
        rows = [
            (r.seq, rel)
            for r in part.relations.values()
            if r.relation.kind is RelationKind.SUPERSEDES
            and r.relation.status is not ReviewStatus.REJECTED
            and (rel := _show(view, r.relation)) is not None
        ]
        rows.sort(key=lambda pair: -pair[0])
        changes = []
        for _, rel in rows[:limit]:
            new, old = part.entities.get(rel.source_id), part.entities.get(rel.target_id)
            if new and old:
                meeting = rel.evidence[0].source_id if rel.evidence else None
                changes.append(Change(rel.id, new.label, old.label, meeting, new.effective_at))
        return tuple(changes)

    # --------------------------------------------------------------- hierarchy

    def hierarchy(
        self,
        workspace_id: str,
        owner_id: str,
        entity_id: str,
        *,
        direction: Direction,
        depth: int,
        include_proposed: bool,
        limit: int,
    ) -> Subgraph:
        part = self._parts.get((workspace_id, owner_id)) or _Partition()
        seed = part.entities.get(entity_id)
        if seed is None or seed.type in ORIGIN_TYPES:
            raise NotFoundError
        statuses = _LIVE if include_proposed else frozenset({ReviewStatus.APPROVED})

        def links(ids: frozenset[str], up: bool) -> list[Relation]:
            return [
                r for r in _child_of(part, statuses) if (r.source_id if up else r.target_id) in ids
            ]

        order, edges, truncated = walk_links(
            entity_id, links, direction=direction, depth=depth, limit=limit
        )
        nodes = tuple(part.entities[i] for i in order if i in part.entities)
        return Subgraph(nodes, tuple(edges), truncated)

    def pending_hierarchy(self, workspace_id: str, owner_id: str, limit: int) -> Subgraph:
        part = self._parts.get((workspace_id, owner_id))
        if part is None:
            return Subgraph()
        waiting = sorted(
            (
                r
                for r in part.relations.values()
                if r.relation.kind is RelationKind.CHILD_OF
                and r.relation.status is ReviewStatus.PROPOSED
                and r.relation.evidence
            ),
            key=lambda r: (-r.batch, r.relation.id),
        )
        chosen = [r.relation for r in waiting[:limit]]
        ids = list(dict.fromkeys(i for r in chosen for i in (r.source_id, r.target_id)))
        nodes = tuple(part.entities[i] for i in ids if i in part.entities)
        return Subgraph(nodes, tuple(chosen), len(waiting) > limit)

    def child_of(self, workspace_id: str, owner_id: str, relation_id_: str) -> Relation:
        part = self._parts.get((workspace_id, owner_id))
        row = part.relations.get(relation_id_) if part else None
        if row is None or row.relation.kind is not RelationKind.CHILD_OF:
            raise NotFoundError
        return row.relation

    def mentions(
        self, workspace_id: str, owner_id: str, entity_id: str, limit: int
    ) -> tuple[Entity, ...]:
        part = self._parts.get((workspace_id, owner_id))
        if part is None:
            return ()
        nodes = {
            r.relation.target_id
            for r in part.relations.values()
            if r.relation.kind is RelationKind.MENTIONED_IN
            and r.relation.status is not ReviewStatus.REJECTED
            and r.relation.source_id == entity_id
        }
        found = [part.entities[i] for i in nodes if i in part.entities]
        found.sort(key=lambda e: (-e.effective_at.timestamp(), e.id))
        return tuple(found[:limit])

    def co_mentions(
        self, workspace_id: str, owner_id: str, entity_id: str, limit: int
    ) -> tuple[CoMention, ...]:
        part = self._parts.get((workspace_id, owner_id))
        seed = part.entities.get(entity_id) if part else None
        if seed is None:
            raise NotFoundError
        mentions = {
            row.relation.source_id: frozenset(row.relation.evidence)
            for row in part.relations.values()
            if row.relation.kind is RelationKind.MENTIONED_IN
            and row.relation.status is not ReviewStatus.REJECTED
        }
        shared_with = mentions.get(entity_id, frozenset())
        linked = {
            other
            for row in part.relations.values()
            if row.relation.kind is RelationKind.CHILD_OF
            and row.relation.status is not ReviewStatus.REJECTED
            for other in (
                [row.relation.target_id]
                if row.relation.source_id == entity_id
                else [row.relation.source_id]
                if row.relation.target_id == entity_id
                else []
            )
        }
        descendants = set(
            self.hierarchy(
                workspace_id,
                owner_id,
                entity_id,
                direction="children",
                depth=6,
                include_proposed=True,
                limit=max(len(part.entities), 1),
            ).nodes
        )
        descendant_ids = {entity.id for entity in descendants}
        things = {EntityType.PROJECT, EntityType.SYSTEM, EntityType.TOPIC}
        result = []
        for candidate_id, candidate_evidence in mentions.items():
            candidate = part.entities.get(candidate_id)
            shared = tuple(
                sorted(shared_with & candidate_evidence, key=lambda e: (e.source_id, e.fragment_id))
            )
            if (
                candidate is not None
                and candidate.type in things
                and candidate_id != entity_id
                and candidate_id not in linked
                and candidate_id not in descendant_ids
                and shared
            ):
                result.append(CoMention(candidate, shared))
        result.sort(key=lambda item: (-len(item.evidence), item.entity.label, item.entity.id))
        return tuple(result[:limit])

    def propose_child_of(
        self,
        workspace_id: str,
        owner_id: str,
        child_id: str,
        parent_id: str,
        confidence: float,
        evidence: tuple[Evidence, ...],
    ) -> Relation | None:
        with self._lock:
            part = self._parts.get((workspace_id, owner_id))
            things = {EntityType.PROJECT, EntityType.SYSTEM, EntityType.TOPIC}
            if (
                part is None
                or part.entities.get(child_id) is None
                or part.entities.get(parent_id) is None
                or part.entities[child_id].type not in things
                or part.entities[parent_id].type not in things
            ):
                return None
            mentions = {
                row.relation.source_id: frozenset(row.relation.evidence)
                for row in part.relations.values()
                if row.relation.kind is RelationKind.MENTIONED_IN
                and row.relation.status is not ReviewStatus.REJECTED
            }
            shared = mentions.get(child_id, frozenset()) & mentions.get(parent_id, frozenset())
            kept = tuple(item for item in evidence if item in shared)
            if not kept:
                return None
            rid = relation_id(workspace_id, owner_id, child_id, RelationKind.CHILD_OF, parent_id)
            existing = part.relations.get(rid)
            if existing is None and check_link(child_id, parent_id, *_hierarchy_steps(part)):
                return None
            self._add_relation(
                part,
                workspace_id,
                owner_id,
                child_id,
                RelationKind.CHILD_OF,
                parent_id,
                confidence,
                kept,
            )
            return part.relations[rid].relation


class _View:
    """A source-evidence view, applied before graph traversal spends any bounds."""

    def __init__(
        self, part: _Partition, meetings_only: bool, source_ids: frozenset[str] | None
    ) -> None:
        self.sources = (
            {e.meeting_id for e in part.entities.values() if e.type is EntityType.MEETING}
            if meetings_only
            else set(source_ids or ())
        )
        nodes = {
            i
            for i, e in part.entities.items()
            if (e.type is EntityType.MEETING and e.meeting_id in self.sources)
            or (e.type is EntityType.SOURCE and e.source_id in self.sources)
        }
        self.entities = nodes | {
            r.relation.source_id
            for r in part.relations.values()
            if r.relation.kind is RelationKind.MENTIONED_IN
            and r.relation.status is not ReviewStatus.REJECTED
            and r.relation.target_id in nodes
        }


def _show(view: _View | None, rel: Relation) -> Relation | None:
    """The relation as the view shows it: only meeting evidence, or None when no meeting backs it
    or it is a hierarchy edge."""
    if view is None:
        return rel
    evidence = tuple(e for e in rel.evidence if e.source_id in view.sources)
    if rel.kind is RelationKind.CHILD_OF or not evidence:
        return None
    return replace(rel, evidence=evidence)


_LIVE = frozenset({ReviewStatus.APPROVED, ReviewStatus.PROPOSED})


def _child_of(
    part: _Partition, statuses: frozenset[ReviewStatus], exclude: str | None = None
) -> list[Relation]:
    """The parent links with one of `statuses` that still have evidence."""
    # ponytail: scans the whole partition per call (links x relations); memory is dev/test only.
    return [
        r.relation
        for rid, r in part.relations.items()
        if r.relation.kind is RelationKind.CHILD_OF
        and r.relation.status in statuses
        and r.relation.evidence
        and rid != exclude
    ]


def _hierarchy_steps(part: _Partition, exclude: str | None = None):
    """parents_of and children_of over the live parent links: not rejected, with evidence."""

    def parents_of(ids: frozenset[str]) -> frozenset[str]:
        return frozenset(r.target_id for r in _child_of(part, _LIVE, exclude) if r.source_id in ids)

    def children_of(ids: frozenset[str]) -> frozenset[str]:
        return frozenset(r.source_id for r in _child_of(part, _LIVE, exclude) if r.target_id in ids)

    return parents_of, children_of


def _sorted(group: dict[str, Entity] | None) -> tuple[Entity, ...]:
    return tuple(sorted((group or {}).values(), key=lambda e: (e.label, e.id)))


def _by_key(extraction: Extraction, key: str):
    return next(e for e in extraction.entities if e.key == key)
