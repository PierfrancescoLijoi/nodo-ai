"""Hybrid search on SQLite: BM25 over an FTS5 index plus cosine similarity over stored embeddings,
fused with reciprocal rank fusion. The local counterpart of Azure AI Search (ADR-011, ADR-014).

Differences from Azure, on purpose and documented: the text index folds case and accents but does
not stem words per language (Azure has Polish, Italian and Danish analyzers), so a crude prefix rule
and the vector ranking carry the morphology. The vector side is exact (a matrix product over the
owner's segments, cached until the next write), which is fine for a personal archive of tens of
thousands of sentences.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np

from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.domain.models import Locale, TranscriptSegment
from nodo_api.ports import Embedder

EMBED_BATCH = 64
CANDIDATES = 50
RRF_K = 60
MIN_TOKEN = 2
PREFIX_FROM = 5  # words this long are matched by prefix, a stand-in for stemming
_WORD = re.compile(r"\w+", re.UNICODE)
logger = logging.getLogger("nodo_search")


class Reranker(Protocol):
    """Scores an already authorized candidate list without retrieving new documents."""

    def score(self, query: str, documents: list[str]) -> list[float]: ...


def match_expression(query: str) -> str | None:
    """An FTS5 query that finds any of the words; quotes keep user text from being syntax."""
    terms = []
    for word in dict.fromkeys(w.lower() for w in _WORD.findall(query)):
        if len(word) < MIN_TOKEN:
            continue
        stem = word[: max(PREFIX_FROM - 1, len(word) - 2)] if len(word) >= PREFIX_FROM else word
        terms.append(f'"{stem}"*' if len(word) >= PREFIX_FROM else f'"{word}"')
    return " OR ".join(terms) or None


def _unit(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-9)


def _segment(row: sqlite3.Row) -> TranscriptSegment:
    return TranscriptSegment(
        id=row["segment_id"],
        meeting_id=row["meeting_id"],
        speaker_id=row["speaker_id"],
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        text=row["text"],
        confidence=row["confidence"],
        version=row["version"],
        locale=Locale(row["locale"]),
        project=row["project"],
    )


class SqliteRetriever:
    def __init__(
        self,
        db: LocalDatabase,
        embedder: Embedder,
        min_similarity: float = 0.0,
        min_margin: float = 0.0,
        reranker: Reranker | None = None,
        rerank_candidates: int = 20,
    ) -> None:
        self._db = db
        self._embedder = embedder
        # Nearest neighbours always exist; these two drop the ones that are not close enough.
        self._min_similarity = min_similarity
        self._min_margin = min_margin
        self._reranker = reranker
        self._rerank_candidates = max(1, rerank_candidates)
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}

    # ------------------------------------------------------------------ writes

    def index(
        self, workspace_id: str, owner_id: str, segments: Sequence[TranscriptSegment]
    ) -> None:
        for start in range(0, len(segments), EMBED_BATCH):
            batch = segments[start : start + EMBED_BATCH]
            embed = getattr(self._embedder, "embed_passages", self._embedder.embed)
            vectors = _unit(embed([s.text for s in batch]))
            with self._db.transaction() as conn:
                for segment, vector in zip(batch, vectors, strict=True):
                    self._put(conn, workspace_id, owner_id, segment, vector)
        self._forget()

    @staticmethod
    def _put(
        conn: sqlite3.Connection,
        workspace_id: str,
        owner_id: str,
        segment: TranscriptSegment,
        vector: np.ndarray,
    ) -> None:
        old = conn.execute(
            "SELECT rowid, text FROM search_segments WHERE workspace_id = ? AND meeting_id = ?"
            " AND segment_id = ?",
            (workspace_id, segment.meeting_id, segment.id),
        ).fetchone()
        if old is not None:  # an external-content FTS index must be told what to forget
            conn.execute(
                "INSERT INTO search_fts (search_fts, rowid, text) VALUES ('delete', ?, ?)",
                (old["rowid"], old["text"]),
            )
        row = conn.execute(
            "INSERT INTO search_segments (workspace_id, owner_id, meeting_id, segment_id, speaker_id,"
            " start_ms, end_ms, version, confidence, project, locale, text, vector)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (workspace_id, meeting_id, segment_id) DO UPDATE SET"
            " owner_id = excluded.owner_id, speaker_id = excluded.speaker_id,"
            " start_ms = excluded.start_ms, end_ms = excluded.end_ms, version = excluded.version,"
            " confidence = excluded.confidence, project = excluded.project,"
            " locale = excluded.locale, text = excluded.text, vector = excluded.vector"
            " RETURNING rowid",
            (
                workspace_id,
                owner_id,
                segment.meeting_id,
                segment.id,
                segment.speaker_id,
                segment.start_ms,
                segment.end_ms,
                segment.version,
                segment.confidence,
                segment.project,
                segment.locale.value,
                segment.text,
                vector.astype(np.float32).tobytes(),
            ),
        ).fetchone()
        conn.execute(
            "INSERT INTO search_fts (rowid, text) VALUES (?, ?)", (row["rowid"], segment.text)
        )

    def count(self, workspace_id: str, meeting_id: str) -> int:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                "SELECT count(*) AS n FROM search_segments WHERE workspace_id = ? AND meeting_id = ?",
                (workspace_id, meeting_id),
            ).fetchone()
        return int(row["n"])

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        with self._db.transaction() as conn:
            rows = conn.execute(
                "SELECT rowid, text FROM search_segments WHERE workspace_id = ? AND meeting_id = ?",
                (workspace_id, meeting_id),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "INSERT INTO search_fts (search_fts, rowid, text) VALUES ('delete', ?, ?)",
                    (row["rowid"], row["text"]),
                )
            conn.execute(
                "DELETE FROM search_segments WHERE workspace_id = ? AND meeting_id = ?",
                (workspace_id, meeting_id),
            )
        self._forget()

    def _forget(self) -> None:
        with self._lock:
            self._cache.clear()

    # ------------------------------------------------------------------- reads

    def search(
        self,
        workspace_id: str,
        owner_id: str,
        query: str,
        filters: dict[str, Any] | None,
        limit: int,
    ) -> tuple[TranscriptSegment, ...]:
        project = (filters or {}).get("project")
        text_hits = self._text_hits(workspace_id, owner_id, query, project)
        vector_hits = self._vector_hits(workspace_id, owner_id, query, project)
        scores: dict[int, float] = {}
        for hits in (text_hits, vector_hits):
            for rank, rowid in enumerate(hits):
                scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (RRF_K + rank + 1)
        best = sorted(scores, key=lambda r: (-scores[r], r))
        if self._reranker is None:
            return self._load(best[:limit])
        candidates = self._load(best[: max(limit, self._rerank_candidates)])
        try:
            rerank_scores = self._reranker.score(query, [segment.text for segment in candidates])
            if len(rerank_scores) != len(candidates) or not np.all(np.isfinite(rerank_scores)):
                raise ValueError("reranker returned invalid scores")
        except Exception as error:
            logger.warning("reranker_fallback type=%s", type(error).__name__)
            return candidates[:limit]
        order = sorted(range(len(candidates)), key=lambda i: (-rerank_scores[i], i))
        return tuple(candidates[i] for i in order[:limit])

    def _text_hits(
        self, workspace_id: str, owner_id: str, query: str, project: str | None
    ) -> list[int]:
        expression = match_expression(query)
        if expression is None:
            return []
        extra = " AND s.project = ?" if project else ""
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT s.rowid FROM search_fts f JOIN search_segments s ON s.rowid = f.rowid"
                " WHERE search_fts MATCH ? AND s.workspace_id = ? AND s.owner_id = ?"
                f"{extra} ORDER BY bm25(search_fts) LIMIT ?",
                (expression, workspace_id, owner_id, *([project] if project else []), CANDIDATES),
            ).fetchall()
        return [r["rowid"] for r in rows]

    def _vector_hits(
        self, workspace_id: str, owner_id: str, query: str, project: str | None
    ) -> list[int]:
        rowids, matrix = self._matrix(workspace_id, owner_id)
        if len(rowids) == 0:
            return []
        embed = getattr(self._embedder, "embed_query", None)
        vector = _unit(embed(query) if embed else self._embedder.embed([query]))[0]
        scores = matrix @ vector
        if project:
            allowed = self._project_rowids(workspace_id, owner_id, project)
            scores = np.where(np.isin(rowids, allowed), scores, -np.inf)
        finite = scores[np.isfinite(scores)]
        if finite.size == 0:
            return []
        # E5 scores sit in a narrow band (about 0.72 to 0.85) for related and unrelated text alike, so
        # an absolute cut-off does not separate them; standing out from the median does.
        floor = max(self._min_similarity, float(np.median(finite)) + self._min_margin)
        top = np.argsort(-scores)[:CANDIDATES]
        return [int(rowids[i]) for i in top if scores[i] >= floor and np.isfinite(scores[i])]

    def _matrix(self, workspace_id: str, owner_id: str) -> tuple[np.ndarray, np.ndarray]:
        key = (workspace_id, owner_id)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT rowid, vector FROM search_segments WHERE workspace_id = ? AND owner_id = ?"
                " ORDER BY rowid",
                (workspace_id, owner_id),
            ).fetchall()
        rowids = np.fromiter((r["rowid"] for r in rows), dtype=np.int64, count=len(rows))
        matrix = (
            np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
            if rows
            else np.empty((0, 0), dtype=np.float32)
        )
        with self._lock:
            self._cache[key] = (rowids, matrix)
        return rowids, matrix

    def _project_rowids(self, workspace_id: str, owner_id: str, project: str) -> np.ndarray:
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT rowid FROM search_segments WHERE workspace_id = ? AND owner_id = ?"
                " AND project = ?",
                (workspace_id, owner_id, project),
            ).fetchall()
        return np.array([r["rowid"] for r in rows], dtype=np.int64)

    def _load(self, rowids: list[int]) -> tuple[TranscriptSegment, ...]:
        if not rowids:
            return ()
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                f"SELECT * FROM search_segments WHERE rowid IN ({','.join('?' * len(rowids))})",
                rowids,
            ).fetchall()
        by_id = {r["rowid"]: r for r in rows}
        return tuple(_segment(by_id[r]) for r in rowids if r in by_id)
