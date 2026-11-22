"""SQLite knowledge graph (ADR-012, ADR-014): the same behaviour as the PostgreSQL store.

The behaviour is pinned by the shared contract suite (tests/graph_contract.py), which runs against
the memory, PostgreSQL and SQLite stores. SQLite has no LATERAL join, so a level of the walk runs
one indexed, limited query per frontier node and direction; with a local, single-user graph that is
still a handful of microsecond lookups.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import numpy as np

from nodo_api.adapters.local.db import LocalDatabase, from_text, now_text, to_text
from nodo_api.domain.graph import (
    AUTO_APPROVED,
    EXISTING_PREFIX,
    ORIGIN_TYPES,
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

_ENTITY_COLUMNS = "id, type, label, status, effective_at, due_at, ref"
_RELATION_COLUMNS = "id, source_id, target_id, kind, status, confidence, valid_from, valid_to"
_MAX_PARAMS = 500  # well under SQLite's limit; longer lists are processed in slices


@dataclass(frozen=True, slots=True)
class _Origin:
    """The meeting or source node a write hangs its evidence on."""

    node: str
    type: EntityType
    id: str
    label: str
    created_at: datetime


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
        source_id=row["ref"] if kind is EntityType.SOURCE else None,
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
  AND (valid_to IS NULL OR valid_to > ?){meetings}
ORDER BY confidence DESC, id LIMIT ?
"""

# The meetings-only view (/v1, frozen clients): what it shows is decided inside each query, so
# document data never spends a limit, a fanout or a seed slot. A relation counts when a meeting
# evidences it (never CHILD_OF), an entity when a meeting mentions it. The meeting ids are the refs
# of the owner's meeting nodes; the subquery takes (workspace, owner) and runs once per statement.
# ponytail: entities has no index on (type, ref), so that subquery scans the owner's entities once
# per statement; add the index if graphs grow to tens of thousands of entities.
_MEETING_IDS = (
    "(SELECT m.ref FROM entities m"
    " WHERE m.workspace_id = ? AND m.owner_id = ? AND m.type = 'meeting')"
)


def _meeting_backed(alias: str) -> str:
    return (
        f" AND {alias}.kind <> 'CHILD_OF' AND EXISTS (SELECT 1 FROM relation_evidence ev"
        f" WHERE ev.workspace_id = {alias}.workspace_id AND ev.owner_id = {alias}.owner_id"
        f" AND ev.relation_id = {alias}.id AND ev.source_id IN {_MEETING_IDS})"
    )


def _meeting_mentioned(alias: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM relations mr JOIN entities mt ON mt.workspace_id = mr.workspace_id"
        " AND mt.owner_id = mr.owner_id AND mt.id = mr.target_id"
        f" WHERE mr.workspace_id = {alias}.workspace_id AND mr.owner_id = {alias}.owner_id"
        f" AND mr.source_id = {alias}.id AND mr.kind = 'MENTIONED_IN' AND mr.status <> 'rejected'"
        " AND mt.type = 'meeting')"
    )


class SqliteGraphStore:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    # ------------------------------------------------------------------ writes

    def upsert_extraction(
        self, workspace_id: str, owner_id: str, meeting: MeetingRef, extraction: Extraction
    ) -> GraphWriteResult:
        node = meeting_entity_id(workspace_id, owner_id, meeting.id)
        origin = _Origin(node, EntityType.MEETING, meeting.id, meeting.title, meeting.created_at)
        with self._db.transaction() as conn:  # the write lock is the per-owner serialisation
            touched = self._drop_evidence(conn, workspace_id, owner_id, meeting.id, None)
            return self._write(conn, workspace_id, owner_id, origin, extraction, touched)

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
        node = source_entity_id(workspace_id, user_id, source.id)
        origin = _Origin(node, EntityType.SOURCE, source.id, source.label, source.created_at)
        with self._db.transaction() as conn:  # one transaction: a failure keeps the old graph
            touched = self._drop_evidence(
                conn, workspace_id, user_id, source.id, dropped_fragment_ids
            )
            self._write(conn, workspace_id, user_id, origin, extraction, touched)
            # The node follows the current title; concepts keep their first-writer label.
            conn.execute(
                "UPDATE entities SET label = ? WHERE workspace_id = ? AND owner_id = ? AND id = ?",
                (source.label, workspace_id, user_id, node),
            )
            # Nothing may point at the node (an empty extraction): it must not linger.
            self._delete_unsupported(conn, workspace_id, user_id, [node])

    def _write(self, conn, workspace_id, owner_id, origin, extraction, touched) -> GraphWriteResult:
        entities, ids = self._entity_rows(workspace_id, owner_id, origin, extraction)
        self._put_entities(conn, entities, refresh=origin.type is EntityType.MEETING)
        relations, evidence, hierarchy = self._relation_rows(
            conn, workspace_id, owner_id, origin, extraction, ids
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
            " source_id, fragment_id) VALUES (?, ?, ?, ?, ?)",
            evidence,
        )
        written = self._write_hierarchy(conn, workspace_id, owner_id, origin, hierarchy)
        self._sweep(conn, workspace_id, owner_id, touched)
        return GraphWriteResult(len(entities), len(relations) + written)

    def _write_hierarchy(self, conn, workspace_id, owner_id, origin: _Origin, hierarchy) -> int:
        """Parent links one at a time, in extraction order, each checked against those before it
        (ADR-030); runs inside the write transaction. Returns how many distinct links it wrote."""
        refused: Counter[str] = Counter()
        written: set[str] = set()
        steps = _hierarchy_steps(conn, workspace_id, owner_id)
        stamp = now_text()  # one stamp per write, as the other relations of the write

        def kept(item) -> bool:
            rid = relation_id(workspace_id, owner_id, item[0], RelationKind.CHILD_OF, item[1])
            found = conn.execute(
                "SELECT 1 FROM relations WHERE workspace_id = ? AND owner_id = ? AND id = ?"
                " AND status <> 'rejected'",
                (workspace_id, owner_id, rid),
            ).fetchone()
            return found is not None

        # Links already on record go first, so a reviewed link never loses to a new proposal.
        hierarchy = sorted(hierarchy, key=lambda item: not kept(item))
        for source, target, confidence, segments in hierarchy:
            rid = relation_id(workspace_id, owner_id, source, RelationKind.CHILD_OF, target)
            row = conn.execute(
                "SELECT status, EXISTS (SELECT 1 FROM relation_evidence ev"
                " WHERE ev.workspace_id = r.workspace_id AND ev.owner_id = r.owner_id"
                " AND ev.relation_id = r.id) AS backed FROM relations r"
                " WHERE r.workspace_id = ? AND r.owner_id = ? AND r.id = ?",
                (workspace_id, owner_id, rid),
            ).fetchone()
            # A row a reimport just stripped of evidence is about to be swept: it counts as new.
            if row is None or (row["status"] != "rejected" and not row["backed"]):
                code = check_link(source, target, *steps)
                if code is not None:
                    refused[code] += 1
                    continue
            conn.execute(
                "INSERT INTO relations (workspace_id, owner_id, id, source_id, target_id, kind,"
                " status, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (workspace_id, owner_id, id)"
                " DO UPDATE SET confidence = max(relations.confidence, excluded.confidence)",
                (
                    workspace_id,
                    owner_id,
                    rid,
                    source,
                    target,
                    RelationKind.CHILD_OF.value,
                    ReviewStatus.PROPOSED.value,
                    confidence,
                    stamp,
                ),
            )
            # Evidence names the source written now, never anything the model said (ADR-029).
            conn.executemany(
                "INSERT OR IGNORE INTO relation_evidence (workspace_id, owner_id, relation_id,"
                " source_id, fragment_id) VALUES (?, ?, ?, ?, ?)",
                [(workspace_id, owner_id, rid, origin.id, s) for s in segments],
            )
            written.add(rid)
        for code, count in sorted(refused.items()):
            logger.info("hierarchy_link_refused code=%s count=%d", code, count)
        return len(written)

    @staticmethod
    def _entity_rows(workspace_id, owner_id, origin: _Origin, extraction):
        stamp = now_text()
        rows = [
            (
                workspace_id,
                owner_id,
                origin.node,
                origin.type.value,
                origin.label,
                normalize_label(origin.id),
                None,
                to_text(origin.created_at),
                None,
                origin.id,
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
            effective = item.effective_at or (origin.created_at if is_decision else None)
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
    def _put_entities(conn: sqlite3.Connection, rows, *, refresh: bool) -> None:
        """Insert or merge entities. Only a meeting write moves an existing entity's recency, so
        documents never reorder what the meetings-only overview and search show."""
        recency = "excluded" if refresh else "entities"
        conn.executemany(
            "INSERT INTO entities (workspace_id, owner_id, id, type, label, normalized, status,"
            " effective_at, due_at, ref, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (workspace_id, owner_id, id) DO UPDATE SET"
            f" updated_at = {recency}.updated_at,"
            " status = COALESCE(entities.status, excluded.status),"
            " effective_at = COALESCE(entities.effective_at, excluded.effective_at),"
            " due_at = COALESCE(excluded.due_at, entities.due_at)",
            rows,
        )

    def _relation_rows(self, conn, workspace_id, owner_id, origin: _Origin, extraction, ids):
        by_key = {e.key: e for e in extraction.entities}
        relations: dict[str, tuple] = {}
        evidence: list[tuple] = []
        hierarchy: list[tuple] = []
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
            # Evidence names the source written now, never anything the model said (ADR-029).
            evidence.extend((workspace_id, owner_id, rid, origin.id, s) for s in segments)

        for key, eid in ids.items():
            item = by_key[key]
            add(eid, RelationKind.MENTIONED_IN, origin.node, 1.0, item.evidence)
            if item.type is EntityType.DECISION:
                add(eid, RelationKind.DECIDED_IN, origin.node, 1.0, item.evidence)
        existing = self._existing_ids(conn, workspace_id, owner_id, extraction)
        for rel in extraction.relations:
            source = self._resolve(ids, existing, rel.source)
            target = self._resolve(ids, existing, rel.target)
            if source and target and rel.kind is RelationKind.CHILD_OF:
                hierarchy.append((source, target, rel.confidence, rel.evidence))
            elif source and target:
                add(source, rel.kind, target, rel.confidence, rel.evidence)
        return list(relations.values()), evidence, hierarchy

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
    def _drop_evidence(
        conn: sqlite3.Connection,
        workspace_id: str,
        owner_id: str,
        source_id: str,
        fragment_ids: frozenset[str] | None,
    ) -> list[str]:
        """Delete the source's evidence (only the listed fragments when given); return the
        relations that lost some, for the sweep."""
        sql = (
            "DELETE FROM relation_evidence WHERE workspace_id = ? AND owner_id = ?"
            " AND source_id = ?{fragments} RETURNING relation_id"
        )
        if fragment_ids is None:
            rows = conn.execute(sql.format(fragments=""), (workspace_id, owner_id, source_id))
            return list({r["relation_id"] for r in rows.fetchall()})
        touched: set[str] = set()
        for part in _slices(sorted(fragment_ids)):
            rows = conn.execute(
                sql.format(fragments=f" AND fragment_id IN ({_marks(len(part))})"),
                (workspace_id, owner_id, source_id, *part),
            ).fetchall()
            touched |= {r["relation_id"] for r in rows}
        return list(touched)

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
            f" AND id IN ({_marks(len(ids))}) AND CASE WHEN type IN ('meeting', 'source') THEN"
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

    def pending_review(
        self, workspace_id: str, owner_id: str, limit: int, *, meetings_only: bool = False
    ) -> Subgraph:
        scope = (workspace_id, owner_id)
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT r.id, r.source_id, r.target_id, r.kind, r.status, r.confidence,"
                " r.valid_from, r.valid_to FROM relations r"
                " WHERE r.workspace_id = ? AND r.owner_id = ? AND r.status = 'proposed'"
                f"{_meeting_backed('r') if meetings_only else ''}"
                " ORDER BY r.created_at DESC, r.id LIMIT ?",
                (*scope, *(scope if meetings_only else ()), limit + 1),
            ).fetchall()
            chosen = rows[:limit]
            ids = list(dict.fromkeys(i for r in chosen for i in (r["source_id"], r["target_id"])))
            entities = self._entities(conn, workspace_id, owner_id, ids)
            evidence = self._evidence(
                conn, workspace_id, owner_id, [r["id"] for r in chosen], meetings_only
            )
        nodes = tuple(entities[i] for i in ids if i in entities)
        relations = tuple(_relation(r, tuple(evidence.get(r["id"], ()))) for r in chosen)
        return Subgraph(nodes, relations, len(rows) > limit)

    def entities_missing_vectors(
        self, workspace_id: str, owner_id: str, limit: int, dimensions: int | None
    ) -> tuple[Entity, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT e.id, e.type, e.label, e.status, e.effective_at, e.due_at, e.ref"
                " FROM entities e LEFT JOIN entity_vectors v ON v.workspace_id = e.workspace_id"
                " AND v.owner_id = e.owner_id AND v.entity_id = e.id"
                " WHERE e.workspace_id = ? AND e.owner_id = ? AND e.type NOT IN ('meeting', 'source')"
                " AND (v.entity_id IS NULL OR (? IS NOT NULL AND v.dims <> ?))"
                " ORDER BY e.updated_at DESC, e.id LIMIT ?",
                (workspace_id, owner_id, dimensions, dimensions, limit),
            ).fetchall()
        return tuple(_entity(r) for r in rows)

    def put_entity_vectors(
        self, workspace_id: str, owner_id: str, vectors: Mapping[str, Sequence[float]]
    ) -> None:
        if not vectors:
            return
        with self._db.transaction() as conn:
            for entity_id, vector in vectors.items():
                conn.execute(
                    "INSERT INTO entity_vectors (workspace_id, owner_id, entity_id, dims, vector)"
                    " SELECT e.workspace_id, e.owner_id, e.id, ?, ? FROM entities e"
                    " WHERE e.workspace_id = ? AND e.owner_id = ? AND e.id = ? AND e.type NOT IN ('meeting', 'source')"
                    " ON CONFLICT (workspace_id, owner_id, entity_id)"
                    " DO UPDATE SET dims = excluded.dims, vector = excluded.vector",
                    (
                        len(vector),
                        np.asarray(vector, dtype=np.float32).tobytes(),
                        workspace_id,
                        owner_id,
                        entity_id,
                    ),
                )

    def similar_entities(
        self,
        workspace_id: str,
        owner_id: str,
        vector: Sequence[float],
        limit: int,
        min_similarity: float,
    ) -> tuple[Entity, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT e.id, e.type, e.label, e.status, e.effective_at, e.due_at, e.ref, v.vector"
                " FROM entity_vectors v JOIN entities e ON e.workspace_id = v.workspace_id"
                " AND e.owner_id = v.owner_id AND e.id = v.entity_id"
                " WHERE v.workspace_id = ? AND v.owner_id = ? AND v.dims = ? AND e.type NOT IN ('meeting', 'source')",
                (workspace_id, owner_id, len(vector)),
            ).fetchall()
        if not rows:
            return ()
        query = np.asarray(vector, dtype=np.float32)
        query = query / max(float(np.linalg.norm(query)), 1e-9)
        matrix = np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
        matrix = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)
        scores = matrix @ query
        order = sorted(
            (i for i in range(len(rows)) if scores[i] >= min_similarity),
            key=lambda i: (-float(scores[i]), rows[i]["id"]),
        )
        return tuple(_entity(rows[i]) for i in order[:limit])

    def evidence_segment_ids(
        self, workspace_id: str, owner_id: str, meeting_id: str
    ) -> frozenset[str]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT DISTINCT fragment_id FROM relation_evidence"
                " WHERE workspace_id = ? AND owner_id = ? AND source_id = ?",
                (workspace_id, owner_id, meeting_id),
            ).fetchall()
        return frozenset(row["fragment_id"] for row in rows)

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        with self._db.transaction() as conn:
            rows = conn.execute(
                "DELETE FROM relation_evidence WHERE workspace_id = ? AND source_id = ?"
                " RETURNING owner_id, relation_id",
                (workspace_id, meeting_id),
            ).fetchall()
            by_owner: dict[str, set[str]] = {}
            for row in rows:
                by_owner.setdefault(row["owner_id"], set()).add(row["relation_id"])
            for owner_id, relation_ids in by_owner.items():
                self._sweep(conn, workspace_id, owner_id, sorted(relation_ids))

    def review_relation(
        self,
        workspace_id: str,
        owner_id: str,
        relation_id_: str,
        status: ReviewStatus,
        *,
        meetings_only: bool = False,
    ) -> Relation:
        if status is ReviewStatus.PROPOSED:
            raise ValueError("A review is approved or rejected")
        with self._db.transaction() as conn:
            if (
                meetings_only
                and not conn.execute(
                    "SELECT 1 FROM relations r WHERE r.workspace_id = ? AND r.owner_id = ?"
                    f" AND r.id = ?{_meeting_backed('r')}",
                    (workspace_id, owner_id, relation_id_, workspace_id, owner_id),
                ).fetchone()
            ):
                raise NotFoundError  # hidden from that view: nothing is written
            if status is ReviewStatus.APPROVED:
                self._check_reapproval(conn, workspace_id, owner_id, relation_id_)
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
            evidence = self._evidence(conn, workspace_id, owner_id, [relation_id_], meetings_only)
        return _relation(row, tuple(evidence.get(row["id"], ())))

    @staticmethod
    def _check_reapproval(conn, workspace_id: str, owner_id: str, relation_id_: str) -> None:
        """A rejected parent link comes back only if the hierarchy still allows it; a proposed one
        already satisfies the rule."""
        rel = conn.execute(
            "SELECT source_id, target_id FROM relations WHERE workspace_id = ? AND owner_id = ?"
            " AND id = ? AND kind = 'CHILD_OF' AND status = 'rejected'",
            (workspace_id, owner_id, relation_id_),
        ).fetchone()
        if rel is None:
            return
        steps = _hierarchy_steps(conn, workspace_id, owner_id, exclude=relation_id_)
        code = check_link(rel["source_id"], rel["target_id"], *steps)
        if code is not None:
            raise refusal_error(code)

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
    ) -> tuple[Entity, ...]:
        patterns = [_like(t) for t in (normalize_label(x) for x in terms) if len(t) >= 3]
        if not patterns:
            return ()
        like = "normalized LIKE ? ESCAPE '\\'"
        score = " + ".join(f"CASE WHEN {like} THEN 1 ELSE 0 END" for _ in patterns)
        match = " OR ".join(like for _ in patterns)
        kinds = [t.value for t in types] if types else None
        type_filter = f" AND type IN ({_marks(len(kinds))})" if kinds else ""
        if meetings_only:
            type_filter += f" AND {_meeting_mentioned('e')}"
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                f"SELECT {_ENTITY_COLUMNS}, ({score}) AS score FROM entities e"
                f" WHERE workspace_id = ? AND owner_id = ? AND type NOT IN ('meeting', 'source'){type_filter}"
                f" AND ({match}) ORDER BY score DESC, updated_at DESC, id LIMIT ?",
                (*patterns, workspace_id, owner_id, *(kinds or ()), *patterns, limit),
            ).fetchall()
        return tuple(_entity(r) for r in rows)

    def overview(
        self, workspace_id: str, owner_id: str, limit: int, *, meetings_only: bool = False
    ) -> tuple[Entity, ...]:
        mentioned = f" AND {_meeting_mentioned('e')}" if meetings_only else ""
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                f"SELECT {_ENTITY_COLUMNS} FROM entities e WHERE workspace_id = ? AND owner_id = ?"
                f" AND type NOT IN ('meeting', 'source'){mentioned}"
                " ORDER BY updated_at DESC, id LIMIT ?",
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
        meetings_only: bool = False,
    ) -> Subgraph:
        statuses = ["approved", *(["proposed"] if include_proposed else [])]
        moment = to_text(as_of or datetime.now(UTC))
        with self._db.transaction(write=False) as conn:
            seeds = self._existing_seeds(conn, workspace_id, owner_id, seed_ids, meetings_only)
            order = seeds[:node_limit]
            truncated = len(order) < len(seeds)
            seen, frontier, edges = set(order), list(order), {}
            for _ in range(depth):
                if not frontier:
                    break
                rows = self._level(
                    conn,
                    workspace_id,
                    owner_id,
                    frontier,
                    statuses,
                    moment,
                    fanout + 1,
                    meetings_only,
                )
                frontier, level_truncated = self._expand(
                    rows, fanout, seen, order, edges, node_limit
                )
                truncated = truncated or level_truncated
            kept = [r for r in edges.values() if r["source_id"] in seen and r["target_id"] in seen]
            entities = self._entities(conn, workspace_id, owner_id, order)
            evidence = self._evidence(
                conn, workspace_id, owner_id, [r["id"] for r in kept], meetings_only
            )
        nodes = tuple(entities[i] for i in order if i in entities)
        relations = tuple(_relation(r, tuple(evidence.get(r["id"], ()))) for r in kept)
        return Subgraph(nodes, relations, truncated)

    @staticmethod
    def _level(conn, workspace_id, owner_id, frontier, statuses, moment, limit, meetings_only):
        scope = (workspace_id, owner_id) if meetings_only else ()
        rows = []
        for node in frontier:
            for end in ("source_id", "target_id"):
                sql = _LEVEL_SQL.format(
                    columns=_RELATION_COLUMNS,
                    end=end,
                    statuses=_marks(len(statuses)),
                    meetings=_meeting_backed("relations") if meetings_only else "",
                )
                found = conn.execute(
                    sql, (workspace_id, owner_id, node, *statuses, moment, moment, *scope, limit)
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
        conn: sqlite3.Connection,
        workspace_id: str,
        owner_id: str,
        seed_ids: Sequence[str],
        meetings_only: bool,
    ) -> list[str]:
        wanted = list(dict.fromkeys(seed_ids))
        shown = f" AND (type = 'meeting' OR {_meeting_mentioned('e')})" if meetings_only else ""
        found: set[str] = set()
        for part in _slices(wanted):
            rows = conn.execute(
                f"SELECT id FROM entities e WHERE workspace_id = ? AND owner_id = ?"
                f" AND id IN ({_marks(len(part))}){shown}",
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
        conn: sqlite3.Connection,
        workspace_id: str,
        owner_id: str,
        relation_ids: list[str],
        meetings_only: bool = False,
    ) -> dict[str, list[Evidence]]:
        scope = (workspace_id, owner_id) if meetings_only else ()
        meetings = f" AND source_id IN {_MEETING_IDS}" if meetings_only else ""
        grouped: dict[str, list[Evidence]] = {}
        for part in _slices(relation_ids):
            rows = conn.execute(
                "SELECT relation_id, source_id, fragment_id FROM relation_evidence"
                f" WHERE workspace_id = ? AND owner_id = ? AND relation_id IN ({_marks(len(part))})"
                f"{meetings} ORDER BY source_id, fragment_id",
                (workspace_id, owner_id, *part, *scope),
            ).fetchall()
            for row in rows:
                grouped.setdefault(row["relation_id"], []).append(
                    Evidence(row["source_id"], row["fragment_id"])
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

    def recent_changes(
        self, workspace_id: str, owner_id: str, limit: int, *, meetings_only: bool = False
    ) -> tuple[Change, ...]:
        scope = (workspace_id, owner_id) if meetings_only else ()
        meetings = f" AND ev.source_id IN {_MEETING_IDS}" if meetings_only else ""
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT r.id, n.label AS new_label, o.label AS old_label, n.effective_at,"
                " (SELECT ev.source_id FROM relation_evidence ev WHERE ev.workspace_id = r.workspace_id"
                f"  AND ev.owner_id = r.owner_id AND ev.relation_id = r.id{meetings}"
                "  ORDER BY ev.source_id LIMIT 1) AS meeting_id"
                " FROM relations r"
                " JOIN entities n ON n.workspace_id = r.workspace_id AND n.owner_id = r.owner_id"
                "  AND n.id = r.source_id"
                " JOIN entities o ON o.workspace_id = r.workspace_id AND o.owner_id = r.owner_id"
                "  AND o.id = r.target_id"
                " WHERE r.workspace_id = ? AND r.owner_id = ? AND r.kind = 'SUPERSEDES'"
                f" AND r.status <> 'rejected'{_meeting_backed('r') if meetings_only else ''}"
                " ORDER BY r.created_at DESC, r.id LIMIT ?",
                (*scope, workspace_id, owner_id, *scope, limit),
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
        statuses = _LIVE if include_proposed else ("approved",)
        with self._db.transaction(write=False) as conn:
            seed = self._entities(conn, workspace_id, owner_id, [entity_id]).get(entity_id)
            if seed is None or seed.type in ORIGIN_TYPES:
                raise NotFoundError

            def links(ids: frozenset[str], up: bool) -> list[Relation]:
                end = "source_id" if up else "target_id"
                rows = _child_of_rows(conn, workspace_id, owner_id, end, ids, statuses)
                return [_relation(r) for r in rows]

            order, edges, truncated = walk_links(
                entity_id, links, direction=direction, depth=depth, limit=limit
            )
            entities = self._entities(conn, workspace_id, owner_id, order)
            evidence = self._evidence(conn, workspace_id, owner_id, [e.id for e in edges])
        nodes = tuple(entities[i] for i in order if i in entities)
        relations = tuple(replace(e, evidence=tuple(evidence.get(e.id, ()))) for e in edges)
        return Subgraph(nodes, relations, truncated)

    def pending_hierarchy(self, workspace_id: str, owner_id: str, limit: int) -> Subgraph:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                f"SELECT {_RELATION_COLUMNS} FROM relations WHERE workspace_id = ? AND owner_id = ?"
                " AND kind = 'CHILD_OF' AND status = 'proposed' AND EXISTS (SELECT 1"
                " FROM relation_evidence ev WHERE ev.workspace_id = relations.workspace_id"
                " AND ev.owner_id = relations.owner_id AND ev.relation_id = relations.id)"
                " ORDER BY created_at DESC, id LIMIT ?",
                (workspace_id, owner_id, limit + 1),
            ).fetchall()
            chosen = rows[:limit]
            ids = list(dict.fromkeys(i for r in chosen for i in (r["source_id"], r["target_id"])))
            entities = self._entities(conn, workspace_id, owner_id, ids)
            evidence = self._evidence(conn, workspace_id, owner_id, [r["id"] for r in chosen])
        nodes = tuple(entities[i] for i in ids if i in entities)
        relations = tuple(_relation(r, tuple(evidence.get(r["id"], ()))) for r in chosen)
        return Subgraph(nodes, relations, len(rows) > limit)

    def child_of(self, workspace_id: str, owner_id: str, relation_id_: str) -> Relation:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                f"SELECT {_RELATION_COLUMNS} FROM relations WHERE workspace_id = ? AND owner_id = ?"
                " AND id = ? AND kind = 'CHILD_OF'",
                (workspace_id, owner_id, relation_id_),
            ).fetchone()
            if row is None:
                raise NotFoundError
            evidence = self._evidence(conn, workspace_id, owner_id, [relation_id_])
        return _relation(row, tuple(evidence.get(relation_id_, ())))

    def mentions(
        self, workspace_id: str, owner_id: str, entity_id: str, limit: int
    ) -> tuple[Entity, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT e.id, e.type, e.label, e.status, e.effective_at, e.due_at, e.ref"
                " FROM relations r JOIN entities e ON e.workspace_id = r.workspace_id"
                " AND e.owner_id = r.owner_id AND e.id = r.target_id"
                " WHERE r.workspace_id = ? AND r.owner_id = ? AND r.source_id = ?"
                " AND r.kind = 'MENTIONED_IN' AND r.status <> 'rejected'"
                " AND e.type IN ('meeting', 'source')"
                " ORDER BY e.effective_at DESC, e.id LIMIT ?",
                (workspace_id, owner_id, entity_id, limit),
            ).fetchall()
        return tuple(_entity(r) for r in rows)

    def co_mentions(
        self, workspace_id: str, owner_id: str, entity_id: str, limit: int
    ) -> tuple[CoMention, ...]:
        descendants = {
            entity.id
            for entity in self.hierarchy(
                workspace_id,
                owner_id,
                entity_id,
                direction="children",
                depth=6,
                include_proposed=True,
                limit=10000,
            ).nodes
        }
        with self._db.transaction(write=False) as conn:
            linked_rows = conn.execute(
                "SELECT source_id, target_id FROM relations WHERE workspace_id = ? AND owner_id = ?"
                " AND kind = 'CHILD_OF' AND status <> 'rejected'"
                " AND (source_id = ? OR target_id = ?)",
                (workspace_id, owner_id, entity_id, entity_id),
            ).fetchall()
            linked = {
                row["target_id"] if row["source_id"] == entity_id else row["source_id"]
                for row in linked_rows
            }
            rows = conn.execute(
                "SELECT r.source_id AS entity_id, e.id, e.type, e.label, e.status, e.effective_at,"
                " e.due_at, e.ref, ev.source_id, ev.fragment_id"
                " FROM relations r JOIN relation_evidence ev ON ev.workspace_id = r.workspace_id"
                " AND ev.owner_id = r.owner_id AND ev.relation_id = r.id"
                " JOIN entities e ON e.workspace_id = r.workspace_id AND e.owner_id = r.owner_id"
                " AND e.id = r.source_id WHERE r.workspace_id = ? AND r.owner_id = ?"
                " AND r.kind = 'MENTIONED_IN' AND r.status <> 'rejected'"
                " AND e.type IN ('project', 'system', 'topic')",
                (workspace_id, owner_id),
            ).fetchall()
        evidence: dict[str, set[Evidence]] = {}
        entities: dict[str, Entity] = {}
        for row in rows:
            eid = row["entity_id"]
            evidence.setdefault(eid, set()).add(Evidence(row["source_id"], row["fragment_id"]))
            entities.setdefault(eid, _entity(row))
        seed_evidence = evidence.get(entity_id, set())
        result = []
        for candidate_id, candidate_evidence in evidence.items():
            shared = tuple(
                sorted(
                    seed_evidence & candidate_evidence, key=lambda e: (e.source_id, e.fragment_id)
                )
            )
            if (
                candidate_id != entity_id
                and candidate_id not in linked
                and candidate_id not in descendants
                and shared
            ):
                result.append(CoMention(entities[candidate_id], shared))
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
        rid = relation_id(workspace_id, owner_id, child_id, RelationKind.CHILD_OF, parent_id)
        with self._db.transaction() as conn:
            entities = self._entities(conn, workspace_id, owner_id, [child_id, parent_id])
            things = {EntityType.PROJECT, EntityType.SYSTEM, EntityType.TOPIC}
            if (
                set(entities) != {child_id, parent_id}
                or entities[child_id].type not in things
                or entities[parent_id].type not in things
            ):
                return None
            mention_rows = conn.execute(
                "SELECT r.source_id AS entity_id, ev.source_id, ev.fragment_id FROM relations r"
                " JOIN relation_evidence ev ON ev.workspace_id = r.workspace_id"
                " AND ev.owner_id = r.owner_id AND ev.relation_id = r.id"
                " WHERE r.workspace_id = ? AND r.owner_id = ? AND r.kind = 'MENTIONED_IN'"
                " AND r.status <> 'rejected' AND r.source_id IN (?, ?)",
                (workspace_id, owner_id, child_id, parent_id),
            ).fetchall()
            mentions: dict[str, set[Evidence]] = {child_id: set(), parent_id: set()}
            for row in mention_rows:
                mentions[row["entity_id"]].add(Evidence(row["source_id"], row["fragment_id"]))
            shared = mentions[child_id] & mentions[parent_id]
            kept = tuple(item for item in evidence if item in shared)
            if not kept:
                return None
            existing = conn.execute(
                f"SELECT {_RELATION_COLUMNS} FROM relations WHERE workspace_id = ? AND owner_id = ?"
                " AND id = ?",
                (workspace_id, owner_id, rid),
            ).fetchone()
            if existing is None and check_link(
                child_id, parent_id, *_hierarchy_steps(conn, workspace_id, owner_id)
            ):
                return None
            conn.execute(
                "INSERT INTO relations (workspace_id, owner_id, id, source_id, target_id, kind,"
                " status, confidence, created_at) VALUES (?, ?, ?, ?, ?, 'CHILD_OF', 'proposed', ?, ?)"
                " ON CONFLICT (workspace_id, owner_id, id) DO UPDATE SET"
                " confidence = max(relations.confidence, excluded.confidence)",
                (workspace_id, owner_id, rid, child_id, parent_id, confidence, now_text()),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO relation_evidence (workspace_id, owner_id, relation_id,"
                " source_id, fragment_id) VALUES (?, ?, ?, ?, ?)",
                [(workspace_id, owner_id, rid, item.source_id, item.fragment_id) for item in kept],
            )
            row = conn.execute(
                f"SELECT {_RELATION_COLUMNS} FROM relations WHERE workspace_id = ? AND owner_id = ?"
                " AND id = ?",
                (workspace_id, owner_id, rid),
            ).fetchone()
            stored_evidence = self._evidence(conn, workspace_id, owner_id, [rid])
        return _relation(row, tuple(stored_evidence.get(rid, ())))

    # ------------------------------------------------------------------ export

    def export_meeting(self, workspace_id: str, owner_id: str, meeting_id: str) -> GraphExport:
        """The entities a meeting mentions and the relations it evidences, by label rather than id,
        with their review status. Relations to other meetings' decisions become references."""
        with self._db.transaction(write=False) as conn:
            evidence_rows = conn.execute(
                "SELECT relation_id, fragment_id FROM relation_evidence"
                " WHERE workspace_id = ? AND owner_id = ? AND source_id = ? ORDER BY fragment_id",
                (workspace_id, owner_id, meeting_id),
            ).fetchall()
            by_relation: dict[str, list[str]] = {}
            for row in evidence_rows:
                by_relation.setdefault(row["relation_id"], []).append(row["fragment_id"])
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


_LIVE = ("approved", "proposed")


def _child_of_rows(
    conn,
    workspace_id: str,
    owner_id: str,
    end: str,
    ids: frozenset[str],
    statuses: Sequence[str],
    exclude: str | None = None,
) -> list[sqlite3.Row]:
    """The parent links whose `end` (source_id or target_id) is in `ids`, with one of `statuses`
    and still some evidence."""
    rows: list[sqlite3.Row] = []
    for part in _slices(sorted(ids)):
        rows += conn.execute(
            f"SELECT {_RELATION_COLUMNS} FROM relations r WHERE r.workspace_id = ?"
            f" AND r.owner_id = ? AND r.kind = 'CHILD_OF' AND r.status IN ({_marks(len(statuses))})"
            f" AND r.id <> ? AND r.{end} IN ({_marks(len(part))})"
            " AND EXISTS (SELECT 1 FROM relation_evidence ev"
            " WHERE ev.workspace_id = r.workspace_id AND ev.owner_id = r.owner_id"
            " AND ev.relation_id = r.id)",
            (workspace_id, owner_id, *statuses, exclude or "", *part),
        ).fetchall()
    return rows


def _hierarchy_steps(conn, workspace_id: str, owner_id: str, exclude: str | None = None):
    """parents_of and children_of over the live parent links: not rejected, with evidence."""

    def step(end: str, out: str):
        def ends(ids: frozenset[str]) -> frozenset[str]:
            rows = _child_of_rows(conn, workspace_id, owner_id, end, ids, _LIVE, exclude)
            return frozenset(r[out] for r in rows)

        return ends

    return step("source_id", "target_id"), step("target_id", "source_id")


def _ref(kind: EntityType, label: str) -> ExportedRef:
    return ExportedRef() if kind is EntityType.MEETING else ExportedRef(kind, label)
