"""In-memory knowledge graph. It is the reference for the semantics every GraphStore must have: the
same contract tests run against this class and against PostgreSQL (tests/graph_contract.py)."""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from nodo_api.domain.graph import (
    AUTO_APPROVED,
    EXISTING_PREFIX,
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
    Subgraph,
    entity_id,
    meeting_entity_id,
    normalize_label,
    relation_id,
)
from nodo_api.errors import NotFoundError

Key = tuple[str, str]  # (workspace_id, owner_id)


@dataclass(slots=True)
class _Row:
    relation: Relation
    seq: int


@dataclass(slots=True)
class _Partition:
    entities: dict[str, Entity] = field(default_factory=dict)
    normalized: dict[str, str] = field(default_factory=dict)
    touched: dict[str, int] = field(default_factory=dict)
    relations: dict[str, _Row] = field(default_factory=dict)


class MemoryGraphStore:
    def __init__(self) -> None:
        self._parts: dict[Key, _Partition] = {}
        self._clock = itertools.count(1)

    def _part(self, workspace_id: str, owner_id: str) -> _Partition:
        return self._parts.setdefault((workspace_id, owner_id), _Partition())

    # ------------------------------------------------------------------ writes

    def upsert_extraction(
        self, workspace_id: str, owner_id: str, meeting: MeetingRef, extraction: Extraction
    ) -> GraphWriteResult:
        part = self._part(workspace_id, owner_id)
        self._drop_meeting_evidence(part, meeting.id)

        meeting_node = meeting_entity_id(workspace_id, owner_id, meeting.id)
        self._put_entity(
            part,
            Entity(
                meeting_node,
                EntityType.MEETING,
                meeting.title,
                None,
                meeting.created_at,
                None,
                meeting.id,
            ),
            meeting.id,
        )
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
                meeting.created_at if item.type is EntityType.DECISION else None
            )
            self._put_entity(
                part, Entity(eid, item.type, item.label, status, effective, item.due_at), normalized
            )
        for key, eid in list(ids.items()):
            self._add_relation(
                part,
                workspace_id,
                owner_id,
                eid,
                RelationKind.MENTIONED_IN,
                meeting_node,
                1.0,
                self._evidence(meeting.id, _by_key(extraction, key).evidence),
            )
            if part.entities[eid].type is EntityType.DECISION:
                self._add_relation(
                    part,
                    workspace_id,
                    owner_id,
                    eid,
                    RelationKind.DECIDED_IN,
                    meeting_node,
                    1.0,
                    self._evidence(meeting.id, _by_key(extraction, key).evidence),
                )
        for rel in extraction.relations:
            source, target = (
                self._resolve(part, ids, rel.source),
                self._resolve(part, ids, rel.target),
            )
            if source is None or target is None:
                continue
            self._add_relation(
                part,
                workspace_id,
                owner_id,
                source,
                rel.kind,
                target,
                rel.confidence,
                self._evidence(meeting.id, rel.evidence),
            )
        self._sweep(part)
        self._restore_supersession(part)
        for row in part.relations.values():
            if (
                row.relation.kind is RelationKind.SUPERSEDES
                and row.relation.status is ReviewStatus.APPROVED
            ):
                self._set_superseded(part, row.relation.target_id, True)
        mine = sum(
            1
            for r in part.relations.values()
            if any(e.meeting_id == meeting.id for e in r.relation.evidence)
        )
        return GraphWriteResult(len(extraction.entities) + 1, mine)

    @staticmethod
    def _evidence(meeting_id: str, segment_ids: Sequence[str]) -> tuple[Evidence, ...]:
        return tuple(Evidence(meeting_id, s) for s in segment_ids)

    @staticmethod
    def _resolve(part: _Partition, ids: dict[str, str], key: str) -> str | None:
        if key.startswith(EXISTING_PREFIX):
            eid = key[len(EXISTING_PREFIX) :]
            return eid if eid in part.entities else None
        return ids.get(key)

    def _put_entity(self, part: _Partition, entity: Entity, normalized: str) -> None:
        part.entities[entity.id] = entity
        part.normalized[entity.id] = normalized
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

    def _drop_meeting_evidence(self, part: _Partition, meeting_id: str) -> None:
        for row in part.relations.values():
            kept = tuple(e for e in row.relation.evidence if e.meeting_id != meeting_id)
            if len(kept) != len(row.relation.evidence):
                row.relation = replace(row.relation, evidence=kept)

    def _sweep(self, part: _Partition) -> None:
        """Remove what has lost its support, until nothing more falls.

        A relation needs evidence. An entity needs a mention in a meeting (a meeting node needs any
        relation). An entity nobody mentions any more disappears together with the relations that
        pointed at it, so deleting a meeting also deletes the labels it contributed.
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
                alive = eid in touched if entity.type is EntityType.MEETING else eid in mentioned
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

    def evidence_segment_ids(
        self, workspace_id: str, owner_id: str, meeting_id: str
    ) -> frozenset[str]:
        part = self._parts.get((workspace_id, owner_id))
        if part is None:
            return frozenset()
        return frozenset(
            e.segment_id
            for row in part.relations.values()
            for e in row.relation.evidence
            if e.meeting_id == meeting_id
        )

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        for (ws, _), part in self._parts.items():
            if ws != workspace_id:
                continue
            self._drop_meeting_evidence(part, meeting_id)
            self._sweep(part)
            self._restore_supersession(part)

    def review_relation(
        self, workspace_id: str, owner_id: str, relation_id_: str, status: ReviewStatus
    ) -> Relation:
        part = self._part(workspace_id, owner_id)
        row = part.relations.get(relation_id_)
        if row is None or status is ReviewStatus.PROPOSED:
            raise NotFoundError if row is None else ValueError("A review is approved or rejected")
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
    ) -> tuple[Entity, ...]:
        part = self._part(workspace_id, owner_id)
        wanted = [t for t in (normalize_label(x) for x in terms) if len(t) >= 3]
        scored = []
        for eid, entity in part.entities.items():
            if entity.type is EntityType.MEETING or (types and entity.type not in types):
                continue
            score = sum(1 for t in wanted if t in part.normalized[eid])
            if score:
                scored.append((-score, -part.touched[eid], eid, entity))
        return tuple(e for *_, e in sorted(scored, key=lambda x: x[:3]))[:limit]

    def overview(self, workspace_id: str, owner_id: str, limit: int) -> tuple[Entity, ...]:
        part = self._part(workspace_id, owner_id)
        ordered = sorted(
            (e for e in part.entities.values() if e.type is not EntityType.MEETING),
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
    ) -> Subgraph:
        part = self._part(workspace_id, owner_id)
        moment = as_of or datetime.now(UTC)
        allowed = {ReviewStatus.APPROVED} | ({ReviewStatus.PROPOSED} if include_proposed else set())
        order = [s for s in dict.fromkeys(seed_ids) if s in part.entities][:node_limit]
        seen = set(order)
        frontier, collected, truncated = (
            list(order),
            {},
            len(order) < len(set(seed_ids) & set(part.entities)),
        )
        for _ in range(depth):
            nxt: list[str] = []
            for node in frontier:
                incident = self._incident(part, node, allowed, moment)
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
    def _incident(part: _Partition, node: str, allowed, moment: datetime) -> list[Relation]:
        found = []
        for row in part.relations.values():
            rel = row.relation
            if node not in (rel.source_id, rel.target_id) or rel.status not in allowed:
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

    def recent_changes(self, workspace_id: str, owner_id: str, limit: int) -> tuple[Change, ...]:
        part = self._part(workspace_id, owner_id)
        rows = [
            r
            for r in part.relations.values()
            if r.relation.kind is RelationKind.SUPERSEDES
            and r.relation.status is not ReviewStatus.REJECTED
        ]
        rows.sort(key=lambda r: -r.seq)
        changes = []
        for row in rows[:limit]:
            rel = row.relation
            new, old = part.entities.get(rel.source_id), part.entities.get(rel.target_id)
            if new and old:
                meeting = rel.evidence[0].meeting_id if rel.evidence else None
                changes.append(Change(rel.id, new.label, old.label, meeting, new.effective_at))
        return tuple(changes)


def _sorted(group: dict[str, Entity] | None) -> tuple[Entity, ...]:
    return tuple(sorted((group or {}).values(), key=lambda e: (e.label, e.id)))


def _by_key(extraction: Extraction, key: str):
    return next(e for e in extraction.entities if e.key == key)
