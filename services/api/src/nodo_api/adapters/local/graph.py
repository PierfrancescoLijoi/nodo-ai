"""SQLite knowledge graph (ADR-012, ADR-014): the same behaviour as the PostgreSQL store.

The behaviour is pinned by the shared contract suite (tests/graph_contract.py), which runs against
the memory, PostgreSQL and SQLite stores. SQLite has no LATERAL join, so a level of the walk runs
one indexed, limited query per frontier node and direction; with a local, single-user graph that is
still a handful of microsecond lookups.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from nodo_api.adapters.local.db import LocalDatabase, from_text, now_text, to_text
from nodo_api.domain.graph import (
    AUTO_APPROVED,
    EXISTING_PREFIX,
    Change,
    Entity,
    EntityType,
    Evidence,
    ExportedEntity,
    ExportedRef,
    ExportedRelation,
    Extraction,
    GraphExport,
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

_ENTITY_COLUMNS = "id, type, label, status, effective_at, due_at, ref"
_RELATION_COLUMNS = "id, source_id, target_id, kind, status, confidence, valid_from, valid_to"
_MAX_PARAMS = 500  # well under SQLite's limit; longer lists are processed in slices


def _marks(count: int) -> str:
    return ",".join("?" * count)


def _slices(items: Sequence[str]) -> list[Sequence[str]]:
    return [items[i : i + _MAX_PARAMS] for i in range(0, len(items), _MAX_PARAMS)]


def _entity(row: sqlite3.Row) -> Entity:
    kind = EntityType(row["type"])
    return Entity(
        id=row["id"],
        type=kind,
        label=row["label"],
        status=row["status"],
        effective_at=from_text(row["effective_at"]),
        due_at=from_text(row["due_at"]),
        meeting_id=row["ref"] if kind is EntityType.MEETING else None,
    )


def _relation(row: sqlite3.Row, evidence: tuple[Evidence, ...] = ()) -> Relation:
    return Relation(
        id=row["id"],
        source_id=row["source_id"],
        target_id=row["target_id"],
        kind=RelationKind(row["kind"]),
        status=ReviewStatus(row["status"]),
        confidence=float(row["confidence"]),
        valid_from=from_text(row["valid_from"]),
        valid_to=from_text(row["valid_to"]),
        evidence=evidence,
    )


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_LEVEL_SQL = """
SELECT {columns} FROM relations
WHERE workspace_id = ? AND owner_id = ? AND {end} = ?
  AND status <> 'rejected' AND status IN ({statuses})
  AND (valid_from IS NULL OR valid_from <= ?)
  AND (valid_to IS NULL OR valid_to > ?)
ORDER BY confidence DESC, id LIMIT ?
"""


class SqliteGraphStore:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    # ------------------------------------------------------------------ writes

    def upsert_extraction(
        self, workspace_id: str, owner_id: str, meeting: MeetingRef, extraction: Extraction
    ) -> GraphWriteResult:
        meeting_node = meeting_entity_id(workspace_id, owner_id, meeting.id)
        entities, ids = self._entity_rows(workspace_id, owner_id, meeting, meeting_node, extraction)
        with self._db.transaction() as conn:  # the write lock is the per-owner serialisation
            touched = self._drop_meeting_evidence(conn, workspace_id, owner_id, meeting.id)
            self._put_entities(conn, entities)
            relations, evidence = self._relation_rows(
                conn, workspace_id, owner_id, meeting, meeting_node, extraction, ids
            )
            conn.executemany(
                "INSERT INTO relations (workspace_id, owner_id, id, source_id, target_id, kind,"
                " status, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (workspace_id, owner_id, id)"
                " DO UPDATE SET confidence = max(relations.confidence, excluded.confidence)",
                relations,
            )
            conn.executemany(
                "INSERT OR IGNORE INTO relation_evidence (workspace_id, owner_id, relation_id,"
                " meeting_id, segment_id) VALUES (?, ?, ?, ?, ?)",
                evidence,
            )
            self._sweep(conn, workspace_id, owner_id, touched)
        return GraphWriteResult(len(entities), len(relations))

    @staticmethod
    def _entity_rows(workspace_id, owner_id, meeting, meeting_node, extraction):
        stamp = now_text()
        rows = [
            (
                workspace_id,
                owner_id,
                meeting_node,
                EntityType.MEETING.value,
                meeting.title,
                normalize_label(meeting.id),
                None,
                to_text(meeting.created_at),
                None,
                meeting.id,
                stamp,
            )
        ]
        ids: dict[str, str] = {}
        for item in extraction.entities:
            normalized = normalize_label(item.label)
            eid = entity_id(workspace_id, owner_id, item.type, normalized)
            ids[item.key] = eid
            is_decision = item.type is EntityType.DECISION
            status = item.status or ("active" if is_decision else None)
            effective = item.effective_at or (meeting.created_at if is_decision else None)
            rows.append(
                (
                    workspace_id,
                    owner_id,
                    eid,
                    item.type.value,
                    item.label,
                    normalized,
                    status,
                    to_text(effective),
                    to_text(item.due_at),
                    None,
                    stamp,
                )
            )
        return rows, ids

    @staticmethod
    def _put_entities(conn: sqlite3.Connection, rows) -> None:
        conn.executemany(
            "INSERT INTO entities (workspace_id, owner_id, id, type, label, normalized, status,"
            " effective_at, due_at, ref, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (workspace_id, owner_id, id) DO UPDATE SET"
            " updated_at = excluded.updated_at,"
            " status = COALESCE(entities.status, excluded.status),"
            " effective_at = COALESCE(entities.effective_at, excluded.effective_at),"
            " due_at = COALESCE(excluded.due_at, entities.due_at)",
            rows,
        )

    def _relation_rows(self, conn, workspace_id, owner_id, meeting, meeting_node, extraction, ids):
        by_key = {e.key: e for e in extraction.entities}
        relations: dict[str, tuple] = {}
        evidence: list[tuple] = []
        stamp = now_text()

        def add(source, kind, target, confidence, segments):
            rid = relation_id(workspace_id, owner_id, source, kind, target)
            status = (
                ReviewStatus.APPROVED if kind in AUTO_APPROVED else ReviewStatus.PROPOSED
            ).value
            previous = relations.get(rid)
            best = max(confidence, previous[7]) if previous else confidence
            relations[rid] = (
                workspace_id,
                owner_id,
                rid,
                source,
                target,
                kind.value,
                status,
                best,
                stamp,
            )
            evidence.extend((workspace_id, owner_id, rid, meeting.id, s) for s in segments)

        for key, eid in ids.items():
            item = by_key[key]
            add(eid, RelationKind.MENTIONED_IN, meeting_node, 1.0, item.evidence)
            if item.type is EntityType.DECISION:
                add(eid, RelationKind.DECIDED_IN, meeting_node, 1.0, item.evidence)
        existing = self._existing_ids(conn, workspace_id, owner_id, extraction)
        for rel in extraction.relations:
            source = self._resolve(ids, existing, rel.source)
            target = self._resolve(ids, existing, rel.target)
            if source and target:
                add(source, rel.kind, target, rel.confidence, rel.evidence)
        return list(relations.values()), evidence

    @staticmethod
    def _resolve(ids: dict[str, str], existing: set[str], key: str) -> str | None:
        if key.startswith(EXISTING_PREFIX):
            eid = key[len(EXISTING_PREFIX) :]
            return eid if eid in existing else None
        return ids.get(key)

    @staticmethod
    def _existing_ids(
        conn: sqlite3.Connection, workspace_id: str, owner_id: str, extraction: Extraction
    ) -> set[str]:
        wanted = sorted(
            {
                key[len(EXISTING_PREFIX) :]
                for rel in extraction.relations
                for key in (rel.source, rel.target)
                if key.startswith(EXISTING_PREFIX)
            }
        )
        found: set[str] = set()
        for part in _slices(wanted):
            rows = conn.execute(
                f"SELECT id FROM entities WHERE workspace_id = ? AND owner_id = ? AND id IN ({_marks(len(part))})",
                (workspace_id, owner_id, *part),
            ).fetchall()
            found |= {r["id"] for r in rows}
        return found

    @staticmethod
    def _drop_meeting_evidence(
        conn: sqlite3.Connection, workspace_id: str, owner_id: str, meeting_id: str
    ) -> list[str]:
        rows = conn.execute(
            "DELETE FROM relation_evidence WHERE workspace_id = ? AND owner_id = ?"
            " AND meeting_id = ? RETURNING relation_id",
            (workspace_id, owner_id, meeting_id),
        ).fetchall()
        return list({r["relation_id"] for r in rows})

    def _sweep(
        self, conn: sqlite3.Connection, workspace_id: str, owner_id: str, relation_ids: list[str]
    ) -> None:
        """Remove what has lost its support, until nothing more falls (see PgGraphStore._sweep)."""
        if not relation_ids:
            return
        replaced: set[str] = set()
        dead: list[sqlite3.Row] = []
        for part in _slices(relation_ids):
            dead += conn.execute(
                f"DELETE FROM relations WHERE workspace_id = ? AND owner_id = ?"
                f" AND id IN ({_marks(len(part))}) AND NOT EXISTS (SELECT 1 FROM relation_evidence e"
                " WHERE e.workspace_id = relations.workspace_id AND e.owner_id = relations.owner_id"
                " AND e.relation_id = relations.id) RETURNING source_id, target_id, kind",
                (workspace_id, owner_id, *part),
            ).fetchall()
        while dead:
            replaced |= {r["target_id"] for r in dead if r["kind"] == "SUPERSEDES"}
            candidates = sorted({r[end] for r in dead for end in ("source_id", "target_id")})
            gone: list[str] = []
            for part in _slices(candidates):
                gone += [
                    r["id"] for r in self._delete_unsupported(conn, workspace_id, owner_id, part)
                ]
            if not gone:
                break
            dead = []
            for part in _slices(gone):
                dead += conn.execute(
                    f"DELETE FROM relations WHERE workspace_id = ? AND owner_id = ?"
                    f" AND (source_id IN ({_marks(len(part))}) OR target_id IN ({_marks(len(part))}))"
                    " RETURNING source_id, target_id, kind",
                    (workspace_id, owner_id, *part, *part),
                ).fetchall()
        if replaced:
            self._restore_supersession(conn, workspace_id, owner_id, sorted(replaced))

    @staticmethod
    def _delete_unsupported(conn, workspace_id: str, owner_id: str, ids: Sequence[str]):
        return conn.execute(
            f"DELETE FROM entities WHERE workspace_id = ? AND owner_id = ?"
            f" AND id IN ({_marks(len(ids))}) AND CASE WHEN type = 'meeting' THEN"
            " NOT EXISTS (SELECT 1 FROM relations r WHERE r.workspace_id = entities.workspace_id"
            " AND r.owner_id = entities.owner_id AND r.target_id = entities.id)"
            " ELSE NOT EXISTS (SELECT 1 FROM relations r WHERE r.workspace_id = entities.workspace_id"
            " AND r.owner_id = entities.owner_id AND r.source_id = entities.id"
            " AND r.kind = 'MENTIONED_IN' AND r.status <> 'rejected') END RETURNING id",
            (workspace_id, owner_id, *ids),
        ).fetchall()

    @staticmethod
    def _restore_supersession(
        conn: sqlite3.Connection, workspace_id: str, owner_id: str, decision_ids: Sequence[str]
    ) -> None:
        for part in _slices(list(decision_ids)):
            conn.execute(
                f"UPDATE entities SET status = 'active', updated_at = ?"
                f" WHERE workspace_id = ? AND owner_id = ? AND id IN ({_marks(len(part))})"
                " AND type = 'decision' AND status = 'superseded'"
                " AND NOT EXISTS (SELECT 1 FROM relations r WHERE r.workspace_id = entities.workspace_id"
                " AND r.owner_id = entities.owner_id AND r.target_id = entities.id"
                " AND r.kind = 'SUPERSEDES' AND r.status = 'approved')",
                (now_text(), workspace_id, owner_id, *part),
            )

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        with self._db.transaction() as conn:
            rows = conn.execute(
                "DELETE FROM relation_evidence WHERE workspace_id = ? AND meeting_id = ?"
                " RETURNING owner_id, relation_id",
                (workspace_id, meeting_id),
            ).fetchall()
            by_owner: dict[str, set[str]] = {}
            for row in rows:
                by_owner.setdefault(row["owner_id"], set()).add(row["relation_id"])
            for owner_id, relation_ids in by_owner.items():
                self._sweep(conn, workspace_id, owner_id, sorted(relation_ids))

    def review_relation(
        self, workspace_id: str, owner_id: str, relation_id_: str, status: ReviewStatus
    ) -> Relation:
        if status is ReviewStatus.PROPOSED:
            raise ValueError("A review is approved or rejected")
        with self._db.transaction() as conn:
            row = conn.execute(
                "UPDATE relations SET status = ?,"
                " valid_from = CASE WHEN kind = 'SUPERSEDES' AND ? = 'approved'"
                " THEN COALESCE(valid_from, ?) ELSE valid_from END"
                f" WHERE workspace_id = ? AND owner_id = ? AND id = ? RETURNING {_RELATION_COLUMNS}",
                (status.value, status.value, now_text(), workspace_id, owner_id, relation_id_),
            ).fetchone()
            if row is None:
                raise NotFoundError
            if row["kind"] == "SUPERSEDES":
                target = row["target_id"]
                if status is ReviewStatus.APPROVED:
                    conn.execute(
                        "UPDATE entities SET status = 'superseded', updated_at = ?"
                        " WHERE workspace_id = ? AND owner_id = ? AND id = ? AND type = 'decision'",
                        (now_text(), workspace_id, owner_id, target),
                    )
                else:
                    self._restore_supersession(conn, workspace_id, owner_id, [target])
            evidence = self._evidence(conn, workspace_id, owner_id, [relation_id_])
        return _relation(row, tuple(evidence.get(row["id"], ())))

    # ------------------------------------------------------------------- reads

    def find_entities(
        self,
        workspace_id: str,
        owner_id: str,
        terms: Sequence[str],
        limit: int,
        types: frozenset[EntityType] | None = None,
    ) -> tuple[Entity, ...]:
        patterns = [_like(t) for t in (normalize_label(x) for x in terms) if len(t) >= 3]
        if not patterns:
            return ()
        like = "normalized LIKE ? ESCAPE '\\'"
        score = " + ".join(f"CASE WHEN {like} THEN 1 ELSE 0 END" for _ in patterns)
        match = " OR ".join(like for _ in patterns)
        kinds = [t.value for t in types] if types else None
        type_filter = f" AND type IN ({_marks(len(kinds))})" if kinds else ""
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                f"SELECT {_ENTITY_COLUMNS}, ({score}) AS score FROM entities"
                f" WHERE workspace_id = ? AND owner_id = ? AND type <> 'meeting'{type_filter}"
                f" AND ({match}) ORDER BY score DESC, updated_at DESC, id LIMIT ?",
                (*patterns, workspace_id, owner_id, *(kinds or ()), *patterns, limit),
            ).fetchall()
        return tuple(_entity(r) for r in rows)

    def overview(self, workspace_id: str, owner_id: str, limit: int) -> tuple[Entity, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE workspace_id = ? AND owner_id = ?"
                " AND type <> 'meeting' ORDER BY updated_at DESC, id LIMIT ?",
                (workspace_id, owner_id, limit),
            ).fetchall()
        return tuple(_entity(r) for r in rows)

    def recent_decisions(self, workspace_id: str, owner_id: str, limit: int) -> tuple[Entity, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE workspace_id = ? AND owner_id = ?"
                " AND type = 'decision' ORDER BY updated_at DESC, id LIMIT ?",
                (workspace_id, owner_id, limit),
            ).fetchall()
        return tuple(_entity(r) for r in rows)

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
        statuses = ["approved", *(["proposed"] if include_proposed else [])]
        moment = to_text(as_of or datetime.now(UTC))
        with self._db.transaction(write=False) as conn:
            seeds = self._existing_seeds(conn, workspace_id, owner_id, seed_ids)
            order = seeds[:node_limit]
            truncated = len(order) < len(seeds)
            seen, frontier, edges = set(order), list(order), {}
            for _ in range(depth):
                if not frontier:
                    break
                rows = self._level(
                    conn, workspace_id, owner_id, frontier, statuses, moment, fanout + 1
                )
                frontier, level_truncated = self._expand(
                    rows, fanout, seen, order, edges, node_limit
                )
                truncated = truncated or level_truncated
            kept = [r for r in edges.values() if r["source_id"] in seen and r["target_id"] in seen]
            entities = self._entities(conn, workspace_id, owner_id, order)
            evidence = self._evidence(conn, workspace_id, owner_id, [r["id"] for r in kept])
        nodes = tuple(entities[i] for i in order if i in entities)
        relations = tuple(_relation(r, tuple(evidence.get(r["id"], ()))) for r in kept)
        return Subgraph(nodes, relations, truncated)

    @staticmethod
    def _level(conn, workspace_id, owner_id, frontier, statuses, moment, limit):
        rows = []
        for node in frontier:
            for end in ("source_id", "target_id"):
                sql = _LEVEL_SQL.format(
                    columns=_RELATION_COLUMNS, end=end, statuses=_marks(len(statuses))
                )
                found = conn.execute(
                    sql, (workspace_id, owner_id, node, *statuses, moment, moment, limit)
                ).fetchall()
                rows += [{**dict(r), "node": node} for r in found]
        return rows

    @staticmethod
    def _expand(rows, fanout, seen, order, edges, node_limit) -> tuple[list[str], bool]:
        per_node: dict[str, int] = {}
        truncated, nxt = False, []
        for row in rows:
            node = row["node"]
            per_node[node] = per_node.get(node, 0) + 1
            if per_node[node] > fanout:
                truncated = True
                continue
            edges[row["id"]] = row
            other = row["target_id"] if row["source_id"] == node else row["source_id"]
            if other in seen:
                continue
            if len(seen) >= node_limit:
                truncated = True
                continue
            seen.add(other)
            order.append(other)
            nxt.append(other)
        return nxt, truncated

    @staticmethod
    def _existing_seeds(
        conn: sqlite3.Connection, workspace_id: str, owner_id: str, seed_ids: Sequence[str]
    ) -> list[str]:
        wanted = list(dict.fromkeys(seed_ids))
        found: set[str] = set()
        for part in _slices(wanted):
            rows = conn.execute(
                f"SELECT id FROM entities WHERE workspace_id = ? AND owner_id = ? AND id IN ({_marks(len(part))})",
                (workspace_id, owner_id, *part),
            ).fetchall()
            found |= {r["id"] for r in rows}
        return [s for s in wanted if s in found]

    @staticmethod
    def _entities(
        conn: sqlite3.Connection, workspace_id: str, owner_id: str, ids: list[str]
    ) -> dict[str, Entity]:
        found: dict[str, Entity] = {}
        for part in _slices(ids):
            rows = conn.execute(
                f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE workspace_id = ? AND owner_id = ?"
                f" AND id IN ({_marks(len(part))})",
                (workspace_id, owner_id, *part),
            ).fetchall()
            found |= {r["id"]: _entity(r) for r in rows}
        return found

    @staticmethod
    def _evidence(
        conn: sqlite3.Connection, workspace_id: str, owner_id: str, relation_ids: list[str]
    ) -> dict[str, list[Evidence]]:
        grouped: dict[str, list[Evidence]] = {}
        for part in _slices(relation_ids):
            rows = conn.execute(
                "SELECT relation_id, meeting_id, segment_id FROM relation_evidence"
                f" WHERE workspace_id = ? AND owner_id = ? AND relation_id IN ({_marks(len(part))})"
                " ORDER BY meeting_id, segment_id",
                (workspace_id, owner_id, *part),
            ).fetchall()
            for row in rows:
                grouped.setdefault(row["relation_id"], []).append(
                    Evidence(row["meeting_id"], row["segment_id"])
                )
        return grouped

    def meeting_digests(
        self, workspace_id: str, owner_id: str, meeting_ids: Sequence[str]
    ) -> dict[str, MeetingDigest]:
        if not meeting_ids:
            return {}
        by_node = {meeting_entity_id(workspace_id, owner_id, m): m for m in meeting_ids}
        groups: dict[str, dict[EntityType, dict[str, Entity]]] = {m: {} for m in meeting_ids}
        rows: list[sqlite3.Row] = []
        with self._db.transaction(write=False) as conn:
            for part in _slices(list(by_node)):
                rows += conn.execute(
                    "SELECT r.target_id AS meeting_node, r.kind AS link_kind, r.status AS link_status,"
                    " e.id, e.type, e.label, e.status, e.effective_at, e.due_at, e.ref"
                    " FROM relations r JOIN entities e ON e.workspace_id = r.workspace_id"
                    " AND e.owner_id = r.owner_id AND e.id = r.source_id"
                    " WHERE r.workspace_id = ? AND r.owner_id = ?"
                    f" AND r.target_id IN ({_marks(len(part))})"
                    " AND r.kind IN ('MENTIONED_IN', 'DECIDED_IN') AND r.status <> 'rejected'",
                    (workspace_id, owner_id, *part),
                ).fetchall()
            entities = [(by_node[r["meeting_node"]], _entity(r)) for r in rows]
            decided = {r["id"]: r["link_status"] for r in rows if r["link_kind"] == "DECIDED_IN"}
            action_ids = [e.id for _, e in entities if e.type is EntityType.ACTION]
            owners, assigned = self._action_owners(conn, workspace_id, owner_id, action_ids)
        for meeting, entity in entities:
            groups[meeting].setdefault(entity.type, {})[entity.id] = entity
        return {m: self._digest(g, owners, {**decided, **assigned}) for m, g in groups.items()}

    @staticmethod
    def _digest(
        groups: dict[EntityType, dict[str, Entity]], owners: dict[str, str], review: dict[str, str]
    ) -> MeetingDigest:
        def ordered(kind: EntityType) -> tuple[Entity, ...]:
            return tuple(sorted(groups.get(kind, {}).values(), key=lambda e: (e.label, e.id)))

        actions = tuple(sorted(groups.get(EntityType.ACTION, {}).values(), key=lambda e: e.id))
        return MeetingDigest(
            people=ordered(EntityType.PERSON),
            topics=ordered(EntityType.TOPIC),
            decisions=ordered(EntityType.DECISION),
            actions=actions,
            action_owners={a.id: owners[a.id] for a in actions if a.id in owners},
            review={
                **{d.id: review.get(d.id, "proposed") for d in ordered(EntityType.DECISION)},
                **{a.id: review.get(a.id, "proposed") for a in actions},
            },
        )

    @staticmethod
    def _action_owners(conn, workspace_id: str, owner_id: str, action_ids: list[str]):
        owners: dict[str, str] = {}
        status: dict[str, str] = {}
        for part in _slices(action_ids):
            rows = conn.execute(
                "SELECT r.source_id, r.status, e.label FROM relations r JOIN entities e"
                " ON e.workspace_id = r.workspace_id AND e.owner_id = r.owner_id AND e.id = r.target_id"
                " WHERE r.workspace_id = ? AND r.owner_id = ?"
                f" AND r.source_id IN ({_marks(len(part))})"
                " AND r.kind = 'ASSIGNED_TO' AND r.status <> 'rejected'",
                (workspace_id, owner_id, *part),
            ).fetchall()
            owners |= {r["source_id"]: r["label"] for r in rows}
            status |= {r["source_id"]: r["status"] for r in rows}
        return owners, status

    def recent_changes(self, workspace_id: str, owner_id: str, limit: int) -> tuple[Change, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT r.id, n.label AS new_label, o.label AS old_label, n.effective_at,"
                " (SELECT ev.meeting_id FROM relation_evidence ev WHERE ev.workspace_id = r.workspace_id"
                "  AND ev.owner_id = r.owner_id AND ev.relation_id = r.id"
                "  ORDER BY ev.meeting_id LIMIT 1) AS meeting_id"
                " FROM relations r"
                " JOIN entities n ON n.workspace_id = r.workspace_id AND n.owner_id = r.owner_id"
                "  AND n.id = r.source_id"
                " JOIN entities o ON o.workspace_id = r.workspace_id AND o.owner_id = r.owner_id"
                "  AND o.id = r.target_id"
                " WHERE r.workspace_id = ? AND r.owner_id = ? AND r.kind = 'SUPERSEDES'"
                " AND r.status <> 'rejected' ORDER BY r.created_at DESC, r.id LIMIT ?",
                (workspace_id, owner_id, limit),
            ).fetchall()
        return tuple(
            Change(
                r["id"],
                r["new_label"],
                r["old_label"],
                r["meeting_id"],
                from_text(r["effective_at"]),
            )
            for r in rows
        )

    # ------------------------------------------------------------------ export

    def export_meeting(self, workspace_id: str, owner_id: str, meeting_id: str) -> GraphExport:
        """The entities a meeting mentions and the relations it evidences, by label rather than id,
        with their review status. Relations to other meetings' decisions become references."""
        with self._db.transaction(write=False) as conn:
            evidence_rows = conn.execute(
                "SELECT relation_id, segment_id FROM relation_evidence"
                " WHERE workspace_id = ? AND owner_id = ? AND meeting_id = ? ORDER BY segment_id",
                (workspace_id, owner_id, meeting_id),
            ).fetchall()
            by_relation: dict[str, list[str]] = {}
            for row in evidence_rows:
                by_relation.setdefault(row["relation_id"], []).append(row["segment_id"])
            relations = self._relations_with_ends(conn, workspace_id, owner_id, list(by_relation))
        entities: dict[tuple[EntityType, str], ExportedEntity] = {}
        exported: list[ExportedRelation] = []
        for row in relations:
            evidence = tuple(by_relation[row["id"]])
            source_kind, target_kind = (
                EntityType(row["source_type"]),
                EntityType(row["target_type"]),
            )
            if row["kind"] == RelationKind.MENTIONED_IN.value:
                entities[(source_kind, row["source_label"])] = ExportedEntity(
                    source_kind,
                    row["source_label"],
                    evidence,
                    from_text(row["source_due"]),
                    from_text(row["source_effective"]),
                )
                continue
            exported.append(
                ExportedRelation(
                    _ref(source_kind, row["source_label"]),
                    RelationKind(row["kind"]),
                    _ref(target_kind, row["target_label"]),
                    float(row["confidence"]),
                    ReviewStatus(row["status"]),
                    evidence,
                )
            )
        return GraphExport(tuple(entities.values()), tuple(exported))

    @staticmethod
    def _relations_with_ends(conn, workspace_id: str, owner_id: str, relation_ids: list[str]):
        rows: list[sqlite3.Row] = []
        for part in _slices(relation_ids):
            rows += conn.execute(
                "SELECT r.id, r.kind, r.status, r.confidence,"
                " s.type AS source_type, s.label AS source_label, s.due_at AS source_due,"
                " s.effective_at AS source_effective, t.type AS target_type, t.label AS target_label"
                " FROM relations r"
                " JOIN entities s ON s.workspace_id = r.workspace_id AND s.owner_id = r.owner_id"
                " AND s.id = r.source_id"
                " JOIN entities t ON t.workspace_id = r.workspace_id AND t.owner_id = r.owner_id"
                " AND t.id = r.target_id"
                f" WHERE r.workspace_id = ? AND r.owner_id = ? AND r.id IN ({_marks(len(part))})"
                " ORDER BY r.created_at, r.id",
                (workspace_id, owner_id, *part),
            ).fetchall()
        return rows


def _ref(kind: EntityType, label: str) -> ExportedRef:
    return ExportedRef() if kind is EntityType.MEETING else ExportedRef(kind, label)
