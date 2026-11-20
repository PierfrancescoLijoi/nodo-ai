"""Fragment search in memory, for tests and the demo backend (Task 8). Words in common with the
query stand in for the hybrid ranking of the SQLite index; the rules around it (owner scope,
source filter, replace on reindex, rejection of bad input) are the same, and one contract suite
checks both."""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence

from nodo_api.adapters.memory.sources import MemorySourceRepository
from nodo_api.domain.sources import (
    MAX_EMBEDDING_TOKENS,
    Fragment,
    FragmentHit,
    IndexResult,
    check_indexable,
    estimate_tokens,
    searchable,
)

_WORD = re.compile(r"\w+", re.UNICODE)
MIN_TOKEN = 2  # as in the SQLite index: one-letter words are not searched


class MemoryFragmentIndex:
    def __init__(self, sources: MemorySourceRepository) -> None:
        self._source_store = sources
        self._sources: dict[tuple[str, str], tuple[str, tuple[Fragment, ...]]] = {}
        self._lock = threading.Lock()

    def index(
        self, workspace_id: str, owner_id: str, source_id: str, fragments: Sequence[Fragment]
    ) -> IndexResult:
        check_indexable(source_id, fragments)
        with self._lock:
            known = self._source_store.status(workspace_id, source_id)
            if known is None or known[0]:
                return IndexResult(0, 0, "source_gone")
            if known[1] is not None and any(f.version != known[1] for f in fragments):
                return IndexResult(0, 0, "stale_version")
            if fragments:
                self._sources[(workspace_id, source_id)] = (owner_id, tuple(fragments))
            else:
                self._sources.pop((workspace_id, source_id), None)
        long = sum(1 for f in fragments if estimate_tokens(f.text) > MAX_EMBEDDING_TOKENS)
        return IndexResult(len(fragments), long)

    def erase_source(self, workspace_id: str, source_id: str) -> None:
        with self._lock:
            self._sources.pop((workspace_id, source_id), None)

    def compact(self) -> None:
        """Nothing is kept on disk."""

    def count_source(self, workspace_id: str, source_id: str) -> int:
        with self._lock:
            return len(self._sources.get((workspace_id, source_id), ("", ()))[1])

    def search(
        self,
        workspace_id: str,
        owner_id: str,
        query: str,
        limit: int,
        *,
        source_ids: frozenset[str] | None = None,
    ) -> tuple[FragmentHit, ...]:
        terms = {w for w in _WORD.findall(query.lower()) if len(w) >= MIN_TOKEN}
        if not terms or limit <= 0 or not searchable(query):
            return ()
        with self._lock:
            held = [
                fragment
                for (stored_workspace, source_id), (owner, fragments) in self._sources.items()
                if stored_workspace == workspace_id
                and owner == owner_id
                and (source_ids is None or source_id in source_ids)
                for fragment in fragments
            ]
        hits = []
        for fragment in held:
            found = len(terms & set(_WORD.findall(fragment.text.lower())))
            if found:
                hits.append(FragmentHit(fragment, found / len(terms)))
        hits.sort(key=lambda h: (-h.score, h.fragment.source_id, h.fragment.ordinal))
        return tuple(hits[:limit])
