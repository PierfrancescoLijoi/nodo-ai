"""Collections of sources on SQLite (FR-4, FR-8; schema 0014).

Every check and its write share one BEGIN IMMEDIATE transaction, so two writers can not both pass
the limit, the sibling-name check or the parent-link rule. A tombstoned source loses its
memberships through the `source_collections_forget` trigger."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from nodo_api.adapters.local.db import LocalDatabase, from_text, to_text
from nodo_api.adapters.local.sources import _SOURCE_COLUMNS, _source
from nodo_api.domain.graph import normalize_label
from nodo_api.domain.hierarchy import check_link
from nodo_api.domain.sources import (
    MAX_COLLECTIONS,
    Clear,
    Collection,
    Source,
    collection_name,
    collection_order,
)
from nodo_api.errors import (
    DuplicateName,
    NotFoundError,
    TooManyCollections,
    refusal_error,
)

_LIVE_COUNT = (
    "(SELECT count(*) FROM source_collections sc JOIN sources s"
    " ON s.workspace_id = sc.workspace_id AND s.id = sc.source_id"
    " WHERE sc.workspace_id = c.workspace_id AND sc.collection_id = c.id"
    " AND s.tombstoned_at IS NULL) AS source_count"
)
_COLUMNS = f"c.workspace_id, c.owner_id, c.id, c.parent_id, c.name, c.created_at, {_LIVE_COUNT}"


def _collection(row: sqlite3.Row) -> Collection:
    return Collection(
        id=row["id"],
        workspace_id=row["workspace_id"],
        owner_id=row["owner_id"],
        parent_id=row["parent_id"],
        name=row["name"],
        created_at=from_text(row["created_at"]),
        source_count=row["source_count"],
    )


def _marks(n: int) -> str:
    return ", ".join("?" * n)


def _steps(conn: sqlite3.Connection, workspace_id: str, owner_id: str):
    """parents_of and children_of over the owner's collection tree, for check_link."""

    def parents_of(ids: frozenset[str]) -> frozenset[str]:
        rows = conn.execute(
            "SELECT parent_id FROM collections WHERE workspace_id = ? AND owner_id = ?"
            f" AND parent_id IS NOT NULL AND id IN ({_marks(len(ids))})",
            (workspace_id, owner_id, *ids),
        ).fetchall()
        return frozenset(r[0] for r in rows)

    def children_of(ids: frozenset[str]) -> frozenset[str]:
        rows = conn.execute(
            "SELECT id FROM collections WHERE workspace_id = ? AND owner_id = ?"
            f" AND parent_id IN ({_marks(len(ids))})",
            (workspace_id, owner_id, *ids),
        ).fetchall()
        return frozenset(r[0] for r in rows)

    return parents_of, children_of


class SqliteCollectionRepository:
    def __init__(self, db: LocalDatabase, *, max_collections: int = MAX_COLLECTIONS) -> None:
        self._db = db
        self._max = max_collections

    def _get(
        self, conn: sqlite3.Connection, workspace_id: str, owner_id: str, collection_id: str
    ) -> Collection:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM collections c"
            " WHERE c.workspace_id = ? AND c.owner_id = ? AND c.id = ?",
            (workspace_id, owner_id, collection_id),
        ).fetchone()
        if row is None:
            raise NotFoundError
        return _collection(row)

    def _check_parent(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        owner_id: str,
        child_id: str,
        parent_id: str,
    ) -> None:
        self._get(conn, workspace_id, owner_id, parent_id)
        code = check_link(child_id, parent_id, *_steps(conn, workspace_id, owner_id))
        if code is not None:
            raise refusal_error(code)

    @staticmethod
    def _check_sibling(
        conn: sqlite3.Connection,
        workspace_id: str,
        owner_id: str,
        parent_id: str | None,
        normalized: str,
        exclude: str,
    ) -> None:
        clash = conn.execute(
            "SELECT 1 FROM collections WHERE workspace_id = ? AND owner_id = ?"
            " AND COALESCE(parent_id, '') = ? AND normalized = ? AND id <> ?",
            (workspace_id, owner_id, parent_id or "", normalized, exclude),
        ).fetchone()
        if clash is not None:
            raise DuplicateName

    def create(
        self, workspace_id: str, owner_id: str, *, name: str, parent_id: str | None
    ) -> Collection:
        name = collection_name(name)
        normalized = normalize_label(name)
        new_id = str(uuid.uuid4())
        with self._db.transaction() as conn:
            count = conn.execute(
                "SELECT count(*) FROM collections WHERE workspace_id = ? AND owner_id = ?",
                (workspace_id, owner_id),
            ).fetchone()[0]
            if count >= self._max:
                raise TooManyCollections
            if parent_id is not None:
                self._check_parent(conn, workspace_id, owner_id, new_id, parent_id)
            self._check_sibling(conn, workspace_id, owner_id, parent_id, normalized, new_id)
            conn.execute(
                "INSERT INTO collections (workspace_id, owner_id, id, parent_id, name, normalized,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    owner_id,
                    new_id,
                    parent_id,
                    name,
                    normalized,
                    to_text(datetime.now(UTC)),
                ),
            )
            return self._get(conn, workspace_id, owner_id, new_id)

    def list_owned(self, workspace_id: str, owner_id: str) -> tuple[Collection, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS}, c.normalized FROM collections c"
                " WHERE c.workspace_id = ? AND c.owner_id = ?",
                (workspace_id, owner_id),
            ).fetchall()
        normalized = {row["id"]: row["normalized"] for row in rows}
        return collection_order([_collection(r) for r in rows], lambda c: normalized[c.id])

    def get_owned(self, workspace_id: str, owner_id: str, collection_id: str) -> Collection:
        with self._db.transaction(write=False) as conn:
            return self._get(conn, workspace_id, owner_id, collection_id)

    def update(
        self,
        workspace_id: str,
        owner_id: str,
        collection_id: str,
        *,
        name: str | None = None,
        parent_id: str | Clear | None = None,
    ) -> Collection:
        name = collection_name(name) if name is not None else None
        with self._db.transaction() as conn:
            current = self._get(conn, workspace_id, owner_id, collection_id)
            new_parent = current.parent_id
            if isinstance(parent_id, Clear):
                new_parent = None
            elif parent_id is not None:
                self._check_parent(conn, workspace_id, owner_id, collection_id, parent_id)
                new_parent = parent_id
            new_name = name if name is not None else current.name
            normalized = normalize_label(new_name)
            self._check_sibling(conn, workspace_id, owner_id, new_parent, normalized, collection_id)
            conn.execute(
                "UPDATE collections SET parent_id = ?, name = ?, normalized = ?"
                " WHERE workspace_id = ? AND id = ?",
                (new_parent, new_name, normalized, workspace_id, collection_id),
            )
            return self._get(conn, workspace_id, owner_id, collection_id)

    def delete(self, workspace_id: str, owner_id: str, collection_id: str) -> int:
        with self._db.transaction() as conn:
            self._get(conn, workspace_id, owner_id, collection_id)
            _, children_of = _steps(conn, workspace_id, owner_id)
            subtree, level = {collection_id}, frozenset({collection_id})
            while level:
                level = children_of(level) - subtree
                subtree |= level
            # Memberships go by ON DELETE CASCADE; sources are never touched.
            conn.execute(
                f"DELETE FROM collections WHERE workspace_id = ? AND id IN ({_marks(len(subtree))})",
                (workspace_id, *subtree),
            )
        return len(subtree)

    def add_source(
        self, workspace_id: str, owner_id: str, collection_id: str, source_id: str
    ) -> None:
        with self._db.transaction() as conn:
            # One statement checks the owned collection, the owned live source and inserts.
            added = conn.execute(
                "INSERT OR IGNORE INTO source_collections"
                " (workspace_id, owner_id, collection_id, source_id, added_at)"
                " SELECT c.workspace_id, c.owner_id, c.id, s.id, ? FROM collections c"
                " JOIN sources s ON s.workspace_id = c.workspace_id"
                " WHERE c.workspace_id = ? AND c.owner_id = ? AND c.id = ?"
                " AND s.id = ? AND s.owner_id = ? AND s.tombstoned_at IS NULL",
                (
                    to_text(datetime.now(UTC)),
                    workspace_id,
                    owner_id,
                    collection_id,
                    source_id,
                    owner_id,
                ),
            ).rowcount
            if added:
                return
            present = conn.execute(
                "SELECT 1 FROM source_collections WHERE workspace_id = ? AND owner_id = ?"
                " AND collection_id = ? AND source_id = ?",
                (workspace_id, owner_id, collection_id, source_id),
            ).fetchone()
        if present is None:
            raise NotFoundError

    def remove_source(
        self, workspace_id: str, owner_id: str, collection_id: str, source_id: str
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM source_collections WHERE workspace_id = ? AND owner_id = ?"
                " AND collection_id = ? AND source_id = ?",
                (workspace_id, owner_id, collection_id, source_id),
            )

    def list_sources(
        self,
        workspace_id: str,
        owner_id: str,
        collection_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> tuple[Source, ...]:
        where = (
            "s.workspace_id = ? AND s.owner_id = ? AND s.tombstoned_at IS NULL"
            " AND sc.collection_id = ?"
        )
        params: list[object] = [workspace_id, owner_id, collection_id]
        if cursor is not None:
            where += " AND (s.created_at, s.id) < (?, ?)"
            params.extend((to_text(cursor[0]), cursor[1]))
        params.append(limit)
        columns = ", ".join(f"s.{c.strip()}" for c in _SOURCE_COLUMNS.split(","))
        with self._db.transaction(write=False) as conn:
            self._get(conn, workspace_id, owner_id, collection_id)
            rows = conn.execute(
                f"SELECT {columns} FROM source_collections sc JOIN sources s"
                " ON s.workspace_id = sc.workspace_id AND s.id = sc.source_id"
                f" WHERE {where} ORDER BY s.created_at DESC, s.id DESC LIMIT ?",
                params,
            ).fetchall()
        return tuple(_source(row) for row in rows)

    def collections_of(self, workspace_id: str, owner_id: str, source_id: str) -> tuple[str, ...]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT collection_id FROM source_collections"
                " WHERE workspace_id = ? AND owner_id = ? AND source_id = ? ORDER BY collection_id",
                (workspace_id, owner_id, source_id),
            ).fetchall()
        return tuple(r[0] for r in rows)

    def forget_source(self, workspace_id: str, source_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM source_collections WHERE workspace_id = ? AND source_id = ?",
                (workspace_id, source_id),
            )
