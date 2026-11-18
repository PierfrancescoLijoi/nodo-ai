"""Search over the fragments of documents and other non-meeting sources, on SQLite (Task 8).

The same method as the meeting index in search.py, on its own tables (search_fragments and
fragment_fts, migration 0011): BM25 over an FTS5 index plus exact cosine similarity over stored
float32 embeddings, fused with reciprocal rank fusion. The words of a query go through the meeting
index's `match_expression`, so no query character can be FTS5 syntax. Every query filters by
(workspace_id, owner_id) inside the SQL, before anything is ranked.

Two differences from the meeting index, on purpose:
- the rows of one source are replaced as a set, never patched one by one;
- the vector matrix is cached per (workspace, owner) and checked on every search against a token
  read in the same transaction (row count and highest rowid, which never repeats), so a delete made
  by a trigger, or by another process, can not leave a stale matrix in use.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Sequence

import numpy as np

from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.search import (
    CANDIDATES,
    EMBED_BATCH,
    RRF_K,
    _unit,
    match_expression,
)
from nodo_api.domain.models import Locale
from nodo_api.domain.sources import (
    MAX_EMBEDDING_TOKENS,
    Fragment,
    FragmentHit,
    IndexResult,
    Locator,
    check_indexable,
    estimate_tokens,
    searchable,
)
from nodo_api.ports import Embedder

_ONLY = "AND s.source_id IN (SELECT value FROM json_each(?))"


def _hit(row: sqlite3.Row, score: float) -> FragmentHit:
    return FragmentHit(
        Fragment(
            id=row["fragment_id"],
            source_id=row["source_id"],
            section_id=row["section_id"],
            ordinal=row["ordinal"],
            text=row["text"],
            locator=Locator.from_json(row["locator"]),
            version=row["version"],
            speaker_id=row["speaker_id"],
            confidence=row["confidence"],
            locale=Locale(row["locale"]) if row["locale"] else None,
        ),
        score,
    )


class SqliteFragmentIndex:
    def __init__(
        self,
        db: LocalDatabase,
        embedder: Embedder,
        min_similarity: float = 0.0,
        min_margin: float = 0.0,
    ) -> None:
        self._db = db
        self._embedder = embedder
        # Nearest neighbours always exist; these two drop the ones that are not close enough.
        self._min_similarity = min_similarity
        self._min_margin = min_margin
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str], tuple[tuple[int, int], np.ndarray, np.ndarray]] = {}

    # ------------------------------------------------------------------ writes

    def index(
        self, workspace_id: str, owner_id: str, source_id: str, fragments: Sequence[Fragment]
    ) -> IndexResult:
        check_indexable(source_id, fragments)
        truncated = self._over_ceiling(fragments)
        vectors = self._embed([f.text for f in fragments])  # slow, so outside the transaction
        with self._db.transaction() as conn:
            # Looked at inside the write transaction, after the slow part: a delete or a newer
            # version that arrived meanwhile wins, and the rows of the source stay as they were.
            current = conn.execute(
                "SELECT kind, version, tombstoned_at FROM sources WHERE workspace_id = ?"
                " AND id = ?",
                (workspace_id, source_id),
            ).fetchone()
            if current is None or current["tombstoned_at"] is not None:
                return IndexResult(0, 0, "source_gone")
            # Meeting versions live in the transcript; the sources row does not follow them.
            if current["kind"] != "meeting" and any(
                f.version != current["version"] for f in fragments
            ):
                return IndexResult(0, 0, "stale_version")
            conn.execute(
                "DELETE FROM search_fragments WHERE workspace_id = ? AND source_id = ?",
                (workspace_id, source_id),
            )
            conn.executemany(
                "INSERT INTO search_fragments (workspace_id, owner_id, source_id, fragment_id,"
                " version, section_id, ordinal, locator, speaker_id, confidence, locale, text,"
                " truncated, vector) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        workspace_id,
                        owner_id,
                        source_id,
                        f.id,
                        f.version,
                        f.section_id,
                        f.ordinal,
                        f.locator.to_json(),
                        f.speaker_id,
                        f.confidence,
                        f.locale.value if f.locale else None,
                        f.text,
                        int(cut),
                        vector.tobytes(),
                    )
                    for f, cut, vector in zip(fragments, truncated, vectors, strict=True)
                ],
            )
        return IndexResult(len(fragments), sum(truncated))

    def _over_ceiling(self, fragments: Sequence[Fragment]) -> list[bool]:
        """Which fragments are longer than the model reads. The embedder's own tokenizer is the
        only authority when it has one; otherwise a floor estimate, which can miss a truncation but
        never invents one. The model cuts at the ceiling by itself, so nothing is refused."""
        texts = [f.text for f in fragments]
        within = getattr(self._embedder, "within_ceiling", None)
        if within is not None:
            return [not fits for fits in within(texts)]
        return [estimate_tokens(t) > MAX_EMBEDDING_TOKENS for t in texts]

    def _embed(self, texts: list[str]) -> list[np.ndarray]:
        embed = getattr(self._embedder, "embed_passages", self._embedder.embed)
        out: list[np.ndarray] = []
        for start in range(0, len(texts), EMBED_BATCH):
            out.extend(_unit(embed(texts[start : start + EMBED_BATCH])).astype(np.float32))
        return out

    def erase_source(self, workspace_id: str, source_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM search_fragments WHERE workspace_id = ? AND source_id = ?",
                (workspace_id, source_id),
            )

    def count_source(self, workspace_id: str, source_id: str) -> int:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                "SELECT count(*) AS n FROM search_fragments WHERE workspace_id = ?"
                " AND source_id = ?",
                (workspace_id, source_id),
            ).fetchone()
        return int(row["n"])

    # ------------------------------------------------------------------- reads

    def search(
        self,
        workspace_id: str,
        owner_id: str,
        query: str,
        limit: int,
        *,
        source_ids: frozenset[str] | None = None,
    ) -> tuple[FragmentHit, ...]:
        # No searchable word (empty, one letter, only symbols): nothing to look for. The meeting
        # index would still return nearest neighbours of the punctuation; here that is noise.
        expression = match_expression(query) if searchable(query) else None
        if expression is None or limit <= 0 or (source_ids is not None and not source_ids):
            return ()
        only = json.dumps(sorted(source_ids)) if source_ids is not None else None
        scores: dict[int, float] = {}
        for hits in (
            self._text_hits(workspace_id, owner_id, expression, only),
            self._vector_hits(workspace_id, owner_id, query, only),
        ):
            for rank, rowid in enumerate(hits):
                scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (RRF_K + rank + 1)
        best = sorted(scores, key=lambda r: (-scores[r], r))[:limit]
        return self._load(workspace_id, owner_id, best, scores)

    def _text_hits(
        self, workspace_id: str, owner_id: str, expression: str, only: str | None
    ) -> list[int]:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT s.rowid FROM fragment_fts f JOIN search_fragments s ON s.rowid = f.rowid"
                " WHERE fragment_fts MATCH ? AND s.workspace_id = ? AND s.owner_id = ?"
                f" {_ONLY if only else ''} ORDER BY bm25(fragment_fts), s.rowid LIMIT ?",
                (expression, workspace_id, owner_id, *([only] if only else []), CANDIDATES),
            ).fetchall()
        return [r["rowid"] for r in rows]

    def _vector_hits(
        self, workspace_id: str, owner_id: str, query: str, only: str | None
    ) -> list[int]:
        rowids, matrix = self._matrix(workspace_id, owner_id)
        if len(rowids) == 0:
            return []
        embed = getattr(self._embedder, "embed_query", None)
        vector = _unit(embed(query) if embed else self._embedder.embed([query]))[0]
        scores = matrix @ vector
        if only:
            allowed = self._allowed_rowids(workspace_id, owner_id, only)
            scores = np.where(np.isin(rowids, allowed), scores, -np.inf)
        finite = scores[np.isfinite(scores)]
        if finite.size == 0:
            return []
        # E5 scores sit in a narrow band for related and unrelated text alike, so an absolute
        # cut-off does not separate them; standing out from the median does (as in search.py).
        floor = max(self._min_similarity, float(np.median(finite)) + self._min_margin)
        top = np.argsort(-scores, kind="stable")[:CANDIDATES]
        return [int(rowids[i]) for i in top if scores[i] >= floor and np.isfinite(scores[i])]

    def _matrix(self, workspace_id: str, owner_id: str) -> tuple[np.ndarray, np.ndarray]:
        key = (workspace_id, owner_id)
        with self._db.transaction(write=False) as conn:  # token and rows from one snapshot
            count, newest = conn.execute(
                "SELECT count(*), coalesce(max(rowid), 0) FROM search_fragments"
                " WHERE workspace_id = ? AND owner_id = ?",
                key,
            ).fetchone()
            token = (count, newest)
            with self._lock:
                cached = self._cache.get(key)
            if cached is not None and cached[0] == token:
                return cached[1], cached[2]
            rows = conn.execute(
                "SELECT rowid, vector FROM search_fragments WHERE workspace_id = ? AND owner_id = ?"
                " ORDER BY rowid",
                key,
            ).fetchall()
        rowids = np.fromiter((r["rowid"] for r in rows), dtype=np.int64, count=len(rows))
        matrix = (
            np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
            if rows
            else np.empty((0, 0), dtype=np.float32)
        )
        with self._lock:
            self._cache[key] = (token, rowids, matrix)
        return rowids, matrix

    def _allowed_rowids(self, workspace_id: str, owner_id: str, only: str) -> np.ndarray:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT s.rowid FROM search_fragments s WHERE s.workspace_id = ?"
                f" AND s.owner_id = ? {_ONLY}",
                (workspace_id, owner_id, only),
            ).fetchall()
        return np.array([r["rowid"] for r in rows], dtype=np.int64)

    def _load(
        self, workspace_id: str, owner_id: str, rowids: list[int], scores: dict[int, float]
    ) -> tuple[FragmentHit, ...]:
        if not rowids:
            return ()
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT * FROM search_fragments WHERE workspace_id = ? AND owner_id = ?"
                f" AND rowid IN ({','.join('?' * len(rowids))})",
                (workspace_id, owner_id, *rowids),
            ).fetchall()
        by_id = {r["rowid"]: r for r in rows}
        return tuple(_hit(by_id[r], scores[r]) for r in rowids if r in by_id)
