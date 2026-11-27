"""Collections of sources in memory, for tests and the demo backend (FR-4, FR-8).

One lock covers every method: each check and its write happen together, and the live-and-owned
check of a source runs under it too. There is no tombstone trigger here, so the source and
meeting services call `forget_source` when they delete."""

from __future__ import annotations

import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime

from nodo_api.adapters.memory.sources import MemorySourceRepository
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
from nodo_api.errors import DuplicateName, NotFoundError, TooManyCollections, refusal_error


class MemoryCollectionRepository:
    def __init__(
        self, sources: MemorySourceRepository, *, max_collections: int = MAX_COLLECTIONS
    ) -> None:
        self._sources = sources
        self._max = max_collections
        self._rows: dict[tuple[str, str], Collection] = {}  # (workspace, id); count unset
        self._members: dict[tuple[str, str], dict[str, datetime]] = {}  # (ws, collection)
        self._lock = threading.RLock()

    def _owned(self, workspace_id: str, owner_id: str) -> list[Collection]:
        return [
            c for (ws, _), c in self._rows.items() if ws == workspace_id and c.owner_id == owner_id
        ]

    def _live(self, workspace_id: str, owner_id: str, source_id: str) -> Source | None:
        try:
            return self._sources.get_owned(workspace_id, owner_id, source_id)
        except NotFoundError:
            return None

    def _get(self, workspace_id: str, owner_id: str, collection_id: str) -> Collection:
        found = self._rows.get((workspace_id, collection_id))
        if found is None or found.owner_id != owner_id:
            raise NotFoundError
        count = sum(
            self._live(workspace_id, owner_id, sid) is not None
            for sid in self._members.get((workspace_id, collection_id), {})
        )
        return replace(found, source_count=count)

    def _check_parent(
        self, workspace_id: str, owner_id: str, child_id: str, parent_id: str
    ) -> None:
        self._get(workspace_id, owner_id, parent_id)
        owned = self._owned(workspace_id, owner_id)

        def parents_of(ids: frozenset[str]) -> frozenset[str]:
            return frozenset(c.parent_id for c in owned if c.id in ids and c.parent_id)

        def children_of(ids: frozenset[str]) -> frozenset[str]:
            return frozenset(c.id for c in owned if c.parent_id in ids)

        code = check_link(child_id, parent_id, parents_of, children_of)
        if code is not None:
            raise refusal_error(code)

    def _check_sibling(
        self, workspace_id: str, owner_id: str, child_id: str, parent_id: str | None, name: str
    ) -> None:
        normalized = normalize_label(name)
        if any(
            c.parent_id == parent_id and c.id != child_id and normalize_label(c.name) == normalized
            for c in self._owned(workspace_id, owner_id)
        ):
            raise DuplicateName

    def create(
        self, workspace_id: str, owner_id: str, *, name: str, parent_id: str | None
    ) -> Collection:
        name = collection_name(name)
        new_id = str(uuid.uuid4())
        with self._lock:
            if len(self._owned(workspace_id, owner_id)) >= self._max:
                raise TooManyCollections
            if parent_id is not None:
                self._check_parent(workspace_id, owner_id, new_id, parent_id)
            self._check_sibling(workspace_id, owner_id, new_id, parent_id, name)
            self._rows[(workspace_id, new_id)] = Collection(
                new_id, workspace_id, owner_id, parent_id, name, datetime.now(UTC)
            )
            return self._get(workspace_id, owner_id, new_id)

    def list_owned(self, workspace_id: str, owner_id: str) -> tuple[Collection, ...]:
        with self._lock:
            listed = [
                self._get(workspace_id, owner_id, c.id) for c in self._owned(workspace_id, owner_id)
            ]
        return collection_order(listed, lambda c: normalize_label(c.name))

    def get_owned(self, workspace_id: str, owner_id: str, collection_id: str) -> Collection:
        with self._lock:
            return self._get(workspace_id, owner_id, collection_id)

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
        with self._lock:
            current = self._get(workspace_id, owner_id, collection_id)
            new_parent = current.parent_id
            if isinstance(parent_id, Clear):
                new_parent = None
            elif parent_id is not None:
                self._check_parent(workspace_id, owner_id, collection_id, parent_id)
                new_parent = parent_id
            new_name = name if name is not None else current.name
            self._check_sibling(workspace_id, owner_id, collection_id, new_parent, new_name)
            self._rows[(workspace_id, collection_id)] = replace(
                current, parent_id=new_parent, name=new_name, source_count=0
            )
            return self._get(workspace_id, owner_id, collection_id)

    def delete(self, workspace_id: str, owner_id: str, collection_id: str) -> int:
        with self._lock:
            self._get(workspace_id, owner_id, collection_id)
            owned = self._owned(workspace_id, owner_id)
            subtree, level = {collection_id}, {collection_id}
            while level:
                level = {c.id for c in owned if c.parent_id in level} - subtree
                subtree |= level
            for gone in subtree:  # memberships go with them; sources are never touched
                del self._rows[(workspace_id, gone)]
                self._members.pop((workspace_id, gone), None)
        return len(subtree)

    def add_source(
        self, workspace_id: str, owner_id: str, collection_id: str, source_id: str
    ) -> None:
        with self._lock:
            self._get(workspace_id, owner_id, collection_id)
            if self._live(workspace_id, owner_id, source_id) is None:
                raise NotFoundError
            members = self._members.setdefault((workspace_id, collection_id), {})
            members.setdefault(source_id, datetime.now(UTC))

    def remove_source(
        self, workspace_id: str, owner_id: str, collection_id: str, source_id: str
    ) -> None:
        with self._lock:
            found = self._rows.get((workspace_id, collection_id))
            if found is not None and found.owner_id == owner_id:
                self._members.get((workspace_id, collection_id), {}).pop(source_id, None)

    def list_sources(
        self,
        workspace_id: str,
        owner_id: str,
        collection_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> tuple[Source, ...]:
        with self._lock:
            self._get(workspace_id, owner_id, collection_id)
            live = [
                source
                for sid in self._members.get((workspace_id, collection_id), {})
                if (source := self._live(workspace_id, owner_id, sid)) is not None
            ]
        live.sort(key=lambda s: (s.created_at, s.id), reverse=True)
        if cursor is not None:
            live = [s for s in live if (s.created_at, s.id) < cursor]
        return tuple(live[:limit])

    def collections_of(self, workspace_id: str, owner_id: str, source_id: str) -> tuple[str, ...]:
        with self._lock:
            # A source tombstoned without forget_source (an import discard) holds no membership.
            if self._live(workspace_id, owner_id, source_id) is None:
                return ()
            return tuple(
                sorted(
                    cid
                    for (ws, cid), members in self._members.items()
                    if ws == workspace_id
                    and source_id in members
                    and self._rows[(ws, cid)].owner_id == owner_id
                )
            )

    def forget_source(self, workspace_id: str, source_id: str) -> None:
        with self._lock:
            for (ws, _), members in self._members.items():
                if ws == workspace_id:
                    members.pop(source_id, None)
