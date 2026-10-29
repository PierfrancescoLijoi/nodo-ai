"""PostgreSQL knowledge graph (ADR-012).

Reads are bounded breadth-first walks: one query per level, each expanding the whole frontier with
a LATERAL join that takes at most `fanout` edges per node from the covering index, in both
directions. A walk to depth 2 is therefore at most four round trips whatever the size of the graph,
and a hub node can not blow up a level. Writes replace a meeting's contribution in one transaction
and keep human review decisions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from nodo_api.adapters.azure.postgres import PostgresDatabase
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

_ENTITY_COLUMNS = "id, type, label, status, effective_at, due_at, ref"

# An entity is supported while a meeting mentions it; a meeting node while anything points at it.
_DELETE_UNSUPPORTED_ENTITIES = """
DELETE FROM entities e
WHERE e.workspace_id = %s AND e.owner_id = %s AND e.id = ANY(%s::uuid[])
  AND CASE WHEN e.type = 'meeting' THEN
        NOT EXISTS (SELECT 1 FROM relations r WHERE r.workspace_id = e.workspace_id
                    AND r.owner_id = e.owner_id AND r.target_id = e.id)
      ELSE
        NOT EXISTS (SELECT 1 FROM relations r WHERE r.workspace_id = e.workspace_id
                    AND r.owner_id = e.owner_id AND r.source_id = e.id
                    AND r.kind = 'MENTIONED_IN' AND r.status <> 'rejected')
      END
RETURNING e.id
"""


def _vector_text(vector: Sequence[float]) -> str:
    """pgvector's text form: [0.1,0.2,...]."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _entity(row: dict[str, Any]) -> Entity:
    kind = EntityType(row["type"])
    return Entity(
        id=str(row["id"]),
        type=kind,
        label=row["label"],
        status=row["status"],
        effective_at=row["effective_at"],
        due_at=row["due_at"],
        meeting_id=row["ref"] if kind is EntityType.MEETING else None,
    )


def _relation(row: dict[str, Any], evidence: tuple[Evidence, ...] = ()) -> Relation:
    return Relation(
        id=str(row["id"]),
        source_id=str(row["source_id"]),
        target_id=str(row["target_id"]),
        kind=RelationKind(row["kind"]),
        status=ReviewStatus(row["status"]),
        confidence=float(row["confidence"]),
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        evidence=evidence,
    )


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_LEVEL_SQL = """
SELECT x.id, x.source_id, x.target_id, x.kind, x.status, x.confidence, x.valid_from, x.valid_to,
       f.node
FROM unnest(%(frontier)s::uuid[]) AS f(node)
CROSS JOIN LATERAL (
    (SELECT r.* FROM relations r
      WHERE r.workspace_id = %(ws)s AND r.owner_id = %(owner)s AND r.source_id = f.node
        AND r.status <> 'rejected' AND r.status = ANY(%(statuses)s)
        AND (r.valid_from IS NULL OR r.valid_from <= %(at)s)
        AND (r.valid_to IS NULL OR r.valid_to > %(at)s)
      ORDER BY r.confidence DESC, r.id LIMIT %(lim)s)
    UNION ALL
    (SELECT r.* FROM relations r
      WHERE r.workspace_id = %(ws)s AND r.owner_id = %(owner)s AND r.target_id = f.node
        AND r.status <> 'rejected' AND r.status = ANY(%(statuses)s)
        AND (r.valid_from IS NULL OR r.valid_from <= %(at)s)
        AND (r.valid_to IS NULL OR r.valid_to > %(at)s)
      ORDER BY r.confidence DESC, r.id LIMIT %(lim)s)
) x
"""


class PgGraphStore:
    def __init__(self, db: PostgresDatabase) -> None:
        self._db = db

    # ------------------------------------------------------------------ writes

    def upsert_extraction(
        self, workspace_id: str, owner_id: str, meeting: MeetingRef, extraction: Extraction
    ) -> GraphWriteResult:
        meeting_node = meeting_entity_id(workspace_id, owner_id, meeting.id)
        entities, ids = self._entity_rows(workspace_id, owner_id, meeting, meeting_node, extraction)
        with self._db.workspace(workspace_id) as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 1))",
                (f"graph|{workspace_id}|{owner_id}",),  # one writer per owner graph at a time
            )
            touched = self._drop_meeting_evidence(conn, workspace_id, owner_id, meeting.id)
            self._put_entities(conn, entities)
            relations, evidence = self._relation_rows(
                conn, workspace_id, owner_id, meeting, meeting_node, extraction, ids
            )
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO relations (workspace_id, owner_id, id, source_id, target_id, kind,"
                    " status, confidence) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (workspace_id, owner_id, id)"
                    " DO UPDATE SET confidence = GREATEST(relations.confidence, EXCLUDED.confidence)",
                    relations,
                )
                cursor.executemany(
                    "INSERT INTO relation_evidence (workspace_id, owner_id, relation_id, meeting_id,"
                    " segment_id) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    evidence,
                )
            self._sweep(conn, workspace_id, owner_id, touched)
        return GraphWriteResult(len(entities), len(relations))

    @staticmethod
    def _entity_rows(workspace_id, owner_id, meeting, meeting_node, extraction):
        rows = [
            (
                workspace_id,
                owner_id,
                meeting_node,
                EntityType.MEETING.value,
                meeting.title,
                normalize_label(meeting.id),
                None,
                meeting.created_at,
                None,
                meeting.id,
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
                    effective,
                    item.due_at,
                    None,
                )
            )
        return rows, ids

    @staticmethod
    def _put_entities(conn, rows) -> None:
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO entities (workspace_id, owner_id, id, type, label, normalized, status,"
                " effective_at, due_at, ref) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (workspace_id, owner_id, id) DO UPDATE SET"
                " updated_at = clock_timestamp(),"
                " status = COALESCE(entities.status, EXCLUDED.status),"
                " effective_at = COALESCE(entities.effective_at, EXCLUDED.effective_at),"
                " due_at = COALESCE(EXCLUDED.due_at, entities.due_at)",
                rows,
            )

    def _relation_rows(self, conn, workspace_id, owner_id, meeting, meeting_node, extraction, ids):
        by_key = {e.key: e for e in extraction.entities}
        relations: dict[str, tuple] = {}
        evidence: list[tuple] = []

        def add(source, kind, target, confidence, segments):
            rid = relation_id(workspace_id, owner_id, source, kind, target)
            status = (
                ReviewStatus.APPROVED if kind in AUTO_APPROVED else ReviewStatus.PROPOSED
            ).value
            previous = relations.get(rid)
            best = max(confidence, previous[7]) if previous else confidence
            relations[rid] = (workspace_id, owner_id, rid, source, target, kind.value, status, best)
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
    def _existing_ids(conn, workspace_id: str, owner_id: str, extraction: Extraction) -> set[str]:
        wanted = {
            key[len(EXISTING_PREFIX) :]
            for rel in extraction.relations
            for key in (rel.source, rel.target)
            if key.startswith(EXISTING_PREFIX)
        }
        if not wanted:
            return set()
        rows = conn.execute(
            "SELECT id FROM entities WHERE workspace_id = %s AND owner_id = %s"
            " AND id = ANY(%s::uuid[])",
            (workspace_id, owner_id, list(wanted)),
        ).fetchall()
        return {str(r["id"]) for r in rows}

    @staticmethod
    def _drop_meeting_evidence(
        conn, workspace_id: str, owner_id: str, meeting_id: str
    ) -> list[str]:
        rows = conn.execute(
            "DELETE FROM relation_evidence WHERE workspace_id = %s AND owner_id = %s"
            " AND meeting_id = %s RETURNING relation_id",
            (workspace_id, owner_id, meeting_id),
        ).fetchall()
        return list({str(r["relation_id"]) for r in rows})

    @staticmethod
    def _sweep(conn, workspace_id: str, owner_id: str, relation_ids: list[str]) -> None:
        """Remove what has lost its support, until nothing more falls.

        A relation needs evidence. An entity needs a mention in a meeting (a meeting node needs any
        relation). An entity nobody mentions any more disappears together with the relations that
        pointed at it, so deleting a meeting also deletes the labels it contributed.
        """
        if not relation_ids:
            return
        replaced: set[str] = set()
        dead = conn.execute(
            "DELETE FROM relations r WHERE r.workspace_id = %s AND r.owner_id = %s"
            " AND r.id = ANY(%s::uuid[]) AND NOT EXISTS (SELECT 1 FROM relation_evidence e"
            " WHERE e.workspace_id = r.workspace_id AND e.owner_id = r.owner_id"
            " AND e.relation_id = r.id) RETURNING r.source_id, r.target_id, r.kind",
            (workspace_id, owner_id, relation_ids),
        ).fetchall()
        while dead:
            replaced |= {str(r["target_id"]) for r in dead if r["kind"] == "SUPERSEDES"}
            candidates = list({str(r[end]) for r in dead for end in ("source_id", "target_id")})
            gone = conn.execute(
                _DELETE_UNSUPPORTED_ENTITIES, (workspace_id, owner_id, candidates)
            ).fetchall()
            ids = [str(r["id"]) for r in gone]
            if not ids:
                break
            dead = conn.execute(
                "DELETE FROM relations WHERE workspace_id = %s AND owner_id = %s"
                " AND (source_id = ANY(%s::uuid[]) OR target_id = ANY(%s::uuid[]))"
                " RETURNING source_id, target_id, kind",
                (workspace_id, owner_id, ids, ids),
            ).fetchall()
        if replaced:
            PgGraphStore._restore_supersession(conn, workspace_id, owner_id, list(replaced))

    @staticmethod
    def _restore_supersession(
        conn, workspace_id: str, owner_id: str, decision_ids: list[str]
    ) -> None:
        conn.execute(
            "UPDATE entities e SET status = 'active', updated_at = clock_timestamp()"
            " WHERE e.workspace_id = %s AND e.owner_id = %s AND e.id = ANY(%s::uuid[])"
            " AND e.type = 'decision' AND e.status = 'superseded'"
            " AND NOT EXISTS (SELECT 1 FROM relations r WHERE r.workspace_id = e.workspace_id"
            " AND r.owner_id = e.owner_id AND r.target_id = e.id AND r.kind = 'SUPERSEDES'"
            " AND r.status = 'approved')",
            (workspace_id, owner_id, decision_ids),
        )

    def pending_review(self, workspace_id: str, owner_id: str, limit: int) -> Subgraph:
        with self._db.workspace(workspace_id) as conn:
            rows = conn.execute(
                "SELECT r.id, r.source_id, r.target_id, r.kind, r.status, r.confidence,"
                " r.valid_from, r.valid_to FROM relations r"
                " WHERE r.workspace_id = %s AND r.owner_id = %s AND r.status = 'proposed'"
                " ORDER BY r.created_at DESC, r.id LIMIT %s",
                (workspace_id, owner_id, limit + 1),
            ).fetchall()
            chosen = rows[:limit]
            ids = list(
                dict.fromkeys(str(i) for r in chosen for i in (r["source_id"], r["target_id"]))
            )
            entities = self._entities(conn, workspace_id, owner_id, ids)
            evidence = self._evidence(conn, workspace_id, owner_id, [str(r["id"]) for r in chosen])
        nodes = tuple(entities[i] for i in ids if i in entities)
        relations = tuple(_relation(r, tuple(evidence.get(str(r["id"]), ()))) for r in chosen)
        return Subgraph(nodes, relations, len(rows) > limit)

    def entities_missing_vectors(
        self, workspace_id: str, owner_id: str, limit: int, dimensions: int | None
    ) -> tuple[Entity, ...]:
        with self._db.workspace(workspace_id) as conn:
            rows = conn.execute(
                "SELECT e.id, e.type, e.label, e.status, e.effective_at, e.due_at, e.ref"
                " FROM entities e LEFT JOIN entity_vectors v ON v.workspace_id = e.workspace_id"
                " AND v.owner_id = e.owner_id AND v.entity_id = e.id"
                " WHERE e.workspace_id = %(ws)s AND e.owner_id = %(owner)s AND e.type <> 'meeting'"
                " AND (v.entity_id IS NULL OR (%(dims)s::int IS NOT NULL AND v.dims <> %(dims)s::int))"
                " ORDER BY e.updated_at DESC, e.id LIMIT %(n)s",
                {"ws": workspace_id, "owner": owner_id, "dims": dimensions, "n": limit},
            ).fetchall()
        return tuple(_entity(r) for r in rows)

    def put_entity_vectors(
        self, workspace_id: str, owner_id: str, vectors: Mapping[str, Sequence[float]]
    ) -> None:
        if not vectors:
            return
        with self._db.workspace(workspace_id) as conn:
            for entity_id, vector in vectors.items():
                # INSERT ... SELECT attaches the vector only to an entity of this owner
                conn.execute(
                    "INSERT INTO entity_vectors (workspace_id, owner_id, entity_id, dims, vector)"
                    " SELECT e.workspace_id, e.owner_id, e.id, %(dims)s, %(vec)s::vector"
                    " FROM entities e WHERE e.workspace_id = %(ws)s AND e.owner_id = %(owner)s"
                    " AND e.id = %(id)s::uuid AND e.type <> 'meeting'"
                    " ON CONFLICT (workspace_id, owner_id, entity_id)"
                    " DO UPDATE SET dims = EXCLUDED.dims, vector = EXCLUDED.vector",
                    {
                        "dims": len(vector),
                        "vec": _vector_text(vector),
                        "ws": workspace_id,
                        "owner": owner_id,
                        "id": entity_id,
                    },
                )

    def similar_entities(
        self,
        workspace_id: str,
        owner_id: str,
        vector: Sequence[float],
        limit: int,
        min_similarity: float,
    ) -> tuple[Entity, ...]:
        with self._db.workspace(workspace_id) as conn:
            rows = conn.execute(
                "SELECT e.id, e.type, e.label, e.status, e.effective_at, e.due_at, e.ref"
                " FROM entity_vectors v JOIN entities e ON e.workspace_id = v.workspace_id"
                " AND e.owner_id = v.owner_id AND e.id = v.entity_id"
                " WHERE v.workspace_id = %(ws)s AND v.owner_id = %(owner)s AND v.dims = %(dims)s"
                " AND e.type <> 'meeting' AND 1 - (v.vector <=> %(vec)s::vector) >= %(min)s"
                " ORDER BY v.vector <=> %(vec)s::vector, e.id LIMIT %(n)s",
                {
                    "ws": workspace_id,
                    "owner": owner_id,
                    "dims": len(vector),
                    "vec": _vector_text(vector),
                    "min": min_similarity,
                    "n": limit,
                },
            ).fetchall()
        return tuple(_entity(r) for r in rows)

    def evidence_segment_ids(
        self, workspace_id: str, owner_id: str, meeting_id: str
    ) -> frozenset[str]:
        with self._db.workspace(workspace_id) as conn:
            rows = conn.execute(
                "SELECT DISTINCT segment_id FROM relation_evidence"
                " WHERE workspace_id = %s AND owner_id = %s AND meeting_id = %s",
                (workspace_id, owner_id, meeting_id),
            ).fetchall()
        return frozenset(row["segment_id"] for row in rows)

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        with self._db.workspace(workspace_id) as conn:
            rows = conn.execute(
                "DELETE FROM relation_evidence WHERE workspace_id = %s AND meeting_id = %s"
                " RETURNING owner_id, relation_id",
                (workspace_id, meeting_id),
            ).fetchall()
            by_owner: dict[str, set[str]] = {}
            for row in rows:
                by_owner.setdefault(row["owner_id"], set()).add(str(row["relation_id"]))
            for owner_id, relation_ids in by_owner.items():
                self._sweep(conn, workspace_id, owner_id, list(relation_ids))

    def review_relation(
        self, workspace_id: str, owner_id: str, relation_id_: str, status: ReviewStatus
    ) -> Relation:
        if status is ReviewStatus.PROPOSED:
            raise ValueError("A review is approved or rejected")
        with self._db.workspace(workspace_id) as conn:
            row = conn.execute(
                "UPDATE relations SET status = %s,"
                " valid_from = CASE WHEN kind = 'SUPERSEDES' AND %s = 'approved'"
                " THEN COALESCE(valid_from, clock_timestamp()) ELSE valid_from END"
                " WHERE workspace_id = %s AND owner_id = %s AND id = %s::uuid"
                " RETURNING id, source_id, target_id, kind, status, confidence, valid_from, valid_to",
                (status.value, status.value, workspace_id, owner_id, relation_id_),
            ).fetchone()
            if row is None:
                raise NotFoundError
            if row["kind"] == "SUPERSEDES":
                target = str(row["target_id"])
                if status is ReviewStatus.APPROVED:
                    conn.execute(
                        "UPDATE entities SET status = 'superseded', updated_at = clock_timestamp()"
                        " WHERE workspace_id = %s AND owner_id = %s AND id = %s::uuid"
                        " AND type = 'decision'",
                        (workspace_id, owner_id, target),
                    )
                else:
                    self._restore_supersession(conn, workspace_id, owner_id, [target])
            evidence = self._evidence(conn, workspace_id, owner_id, [relation_id_])
        return _relation(row, tuple(evidence.get(str(row["id"]), ())))

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
        with self._db.workspace(workspace_id) as conn:
            rows = conn.execute(
                f"SELECT {_ENTITY_COLUMNS}, (SELECT count(*) FROM unnest(%(p)s::text[]) q"
                " WHERE e.normalized LIKE q) AS score FROM entities e"
                " WHERE e.workspace_id = %(ws)s AND e.owner_id = %(owner)s AND e.type <> 'meeting'"
                " AND (%(types)s::text[] IS NULL OR e.type = ANY(%(types)s::text[]))"
                " AND e.normalized LIKE ANY(%(p)s::text[])"
                " ORDER BY score DESC, e.updated_at DESC, e.id LIMIT %(n)s",
                {
                    "p": patterns,
                    "ws": workspace_id,
                    "owner": owner_id,
                    "types": [t.value for t in types] if types else None,
                    "n": limit,
                },
            ).fetchall()
        return tuple(_entity(r) for r in rows)

    def overview(self, workspace_id: str, owner_id: str, limit: int) -> tuple[Entity, ...]:
        with self._db.workspace(workspace_id) as conn:
            rows = conn.execute(
                f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE workspace_id = %s AND owner_id = %s"
                " AND type <> 'meeting' ORDER BY updated_at DESC, id LIMIT %s",
                (workspace_id, owner_id, limit),
            ).fetchall()
        return tuple(_entity(r) for r in rows)

    def recent_decisions(self, workspace_id: str, owner_id: str, limit: int) -> tuple[Entity, ...]:
        with self._db.workspace(workspace_id) as conn:
            rows = conn.execute(
                f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE workspace_id = %s AND owner_id = %s"
                " AND type = 'decision' ORDER BY updated_at DESC, id LIMIT %s",
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
        moment = as_of or datetime.now(UTC)
        with self._db.workspace(workspace_id) as conn:
            seeds = self._existing_seeds(conn, workspace_id, owner_id, seed_ids)
            order = seeds[:node_limit]
            truncated = len(order) < len(seeds)
            seen, frontier, edges = set(order), list(order), {}
            for _ in range(depth):
                if not frontier:
                    break
                rows = conn.execute(
                    _LEVEL_SQL,
                    {
                        "frontier": frontier,
                        "ws": workspace_id,
                        "owner": owner_id,
                        "statuses": statuses,
                        "at": moment,
                        "lim": fanout + 1,
                    },
                ).fetchall()
                frontier, level_truncated = self._expand(
                    rows, fanout, seen, order, edges, node_limit
                )
                truncated = truncated or level_truncated
            kept = [
                r
                for r in edges.values()
                if str(r["source_id"]) in seen and str(r["target_id"]) in seen
            ]
            entities = self._entities(conn, workspace_id, owner_id, order)
            evidence = self._evidence(conn, workspace_id, owner_id, [str(r["id"]) for r in kept])
        nodes = tuple(entities[i] for i in order if i in entities)
        relations = tuple(_relation(r, tuple(evidence.get(str(r["id"]), ()))) for r in kept)
        return Subgraph(nodes, relations, truncated)

    @staticmethod
    def _expand(rows, fanout, seen, order, edges, node_limit) -> tuple[list[str], bool]:
        per_node: dict[str, int] = {}
        truncated, nxt = False, []
        for row in rows:
            node = str(row["node"])
            per_node[node] = per_node.get(node, 0) + 1
            if per_node[node] > fanout:
                truncated = True  # the extra row only proves the node has more edges
                continue
            edges[str(row["id"])] = row
            other = (
                str(row["target_id"]) if str(row["source_id"]) == node else str(row["source_id"])
            )
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
        conn, workspace_id: str, owner_id: str, seed_ids: Sequence[str]
    ) -> list[str]:
        wanted = list(dict.fromkeys(seed_ids))
        if not wanted:
            return []
        rows = conn.execute(
            "SELECT id FROM entities WHERE workspace_id = %s AND owner_id = %s"
            " AND id = ANY(%s::uuid[])",
            (workspace_id, owner_id, wanted),
        ).fetchall()
        found = {str(r["id"]) for r in rows}
        return [s for s in wanted if s in found]

    @staticmethod
    def _entities(conn, workspace_id: str, owner_id: str, ids: list[str]) -> dict[str, Entity]:
        if not ids:
            return {}
        rows = conn.execute(
            f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE workspace_id = %s AND owner_id = %s"
            " AND id = ANY(%s::uuid[])",
            (workspace_id, owner_id, ids),
        ).fetchall()
        return {str(r["id"]): _entity(r) for r in rows}

    @staticmethod
    def _evidence(conn, workspace_id: str, owner_id: str, relation_ids: list[str]):
        if not relation_ids:
            return {}
        rows = conn.execute(
            "SELECT relation_id, meeting_id, segment_id FROM relation_evidence"
            " WHERE workspace_id = %s AND owner_id = %s AND relation_id = ANY(%s::uuid[])"
            " ORDER BY meeting_id, segment_id",
            (workspace_id, owner_id, relation_ids),
        ).fetchall()
        grouped: dict[str, list[Evidence]] = {}
        for row in rows:
            grouped.setdefault(str(row["relation_id"]), []).append(
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
        with self._db.workspace(workspace_id) as conn:
            rows = conn.execute(
                "SELECT r.target_id AS meeting_node, r.kind AS link_kind, r.status AS link_status,"
                " e.id, e.type, e.label, e.status, e.effective_at, e.due_at, e.ref FROM relations r JOIN entities e ON e.workspace_id = r.workspace_id"
                " AND e.owner_id = r.owner_id AND e.id = r.source_id"
                " WHERE r.workspace_id = %s AND r.owner_id = %s AND r.target_id = ANY(%s::uuid[])"
                " AND r.kind IN ('MENTIONED_IN', 'DECIDED_IN') AND r.status <> 'rejected'",
                (workspace_id, owner_id, list(by_node)),
            ).fetchall()
            entities = [(by_node[str(r["meeting_node"])], _entity(r)) for r in rows]
            decided = {
                str(r["id"]): r["link_status"] for r in rows if r["link_kind"] == "DECIDED_IN"
            }
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
        if not action_ids:
            return {}, {}
        rows = conn.execute(
            "SELECT r.source_id, r.status, e.label FROM relations r JOIN entities e"
            " ON e.workspace_id = r.workspace_id AND e.owner_id = r.owner_id AND e.id = r.target_id"
            " WHERE r.workspace_id = %s AND r.owner_id = %s AND r.source_id = ANY(%s::uuid[])"
            " AND r.kind = 'ASSIGNED_TO' AND r.status <> 'rejected'",
            (workspace_id, owner_id, action_ids),
        ).fetchall()
        owners = {str(r["source_id"]): r["label"] for r in rows}
        return owners, {str(r["source_id"]): r["status"] for r in rows}

    def recent_changes(self, workspace_id: str, owner_id: str, limit: int) -> tuple[Change, ...]:
        with self._db.workspace(workspace_id) as conn:
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
                " WHERE r.workspace_id = %s AND r.owner_id = %s AND r.kind = 'SUPERSEDES'"
                " AND r.status <> 'rejected' ORDER BY r.created_at DESC, r.id LIMIT %s",
                (workspace_id, owner_id, limit),
            ).fetchall()
        return tuple(
            Change(str(r["id"]), r["new_label"], r["old_label"], r["meeting_id"], r["effective_at"])
            for r in rows
        )
