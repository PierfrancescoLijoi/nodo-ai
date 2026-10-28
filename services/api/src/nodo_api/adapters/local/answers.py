"""Answer cache on the local SQLite file (ADR-026)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

import numpy as np

from nodo_api.adapters.local.db import LocalDatabase, to_text
from nodo_api.domain.answer_cache import (
    AnswerKey,
    CachedAnswer,
    citations_from_json,
    citations_to_json,
)
from nodo_api.domain.models import Answerability


def _epoch(conn, workspace_id: str, owner_id: str) -> int:
    row = conn.execute(
        "SELECT epoch FROM answer_cache_epoch WHERE workspace_id = ? AND owner_id = ?",
        (workspace_id, owner_id),
    ).fetchone()
    return 0 if row is None else int(row["epoch"])


class SqliteAnswerStore:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    def epoch(self, workspace_id: str, owner_id: str) -> int:
        with self._db.transaction(write=False) as conn:
            return _epoch(conn, workspace_id, owner_id)

    def bump(self, workspace_id: str, owner_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO answer_cache_epoch (workspace_id, owner_id, epoch) VALUES (?, ?, 1)"
                " ON CONFLICT (workspace_id, owner_id) DO UPDATE SET epoch = epoch + 1",
                (workspace_id, owner_id),
            )
            conn.execute(
                "DELETE FROM answer_cache WHERE workspace_id = ? AND owner_id = ?",
                (workspace_id, owner_id),
            )

    def nearest(
        self,
        workspace_id: str,
        owner_id: str,
        key: AnswerKey,
        vector: Sequence[float],
        min_similarity: float,
        fresh_after: datetime,
    ) -> CachedAnswer | None:
        with self._db.transaction(write=False) as conn:
            epoch = _epoch(conn, workspace_id, owner_id)
            rows = conn.execute(
                "SELECT vector, answer, answerability, citations, related_entities"
                " FROM answer_cache WHERE workspace_id = ? AND owner_id = ? AND version = ?"
                " AND locale = ? AND filters = ? AND dims = ? AND epoch = ? AND created_at >= ?",
                (
                    workspace_id,
                    owner_id,
                    key.version,
                    key.locale,
                    key.filters,
                    len(vector),
                    epoch,
                    to_text(fresh_after),
                ),
            ).fetchall()
        if not rows:
            return None
        query = np.asarray(vector, dtype=np.float32)
        query = query / max(float(np.linalg.norm(query)), 1e-9)
        matrix = np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
        matrix = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)
        scores = matrix @ query
        best = int(np.argmax(scores))
        if float(scores[best]) < min_similarity:
            return None
        row = rows[best]
        return CachedAnswer(
            row["answer"],
            Answerability(row["answerability"]),
            citations_from_json(json.loads(row["citations"])),
            tuple(json.loads(row["related_entities"])),
            float(scores[best]),
        )

    def put(
        self,
        workspace_id: str,
        owner_id: str,
        key: AnswerKey,
        vector: Sequence[float],
        answer: CachedAnswer,
        *,
        epoch: int,
        created_at: datetime,
        fresh_after: datetime,
        keep: int,
    ) -> bool:
        with self._db.transaction() as conn:
            if _epoch(conn, workspace_id, owner_id) != epoch:
                return False
            conn.execute(
                "INSERT OR REPLACE INTO answer_cache (workspace_id, owner_id, version, locale,"
                " filters, question_key, epoch, dims, vector, answer, answerability, citations,"
                " related_entities, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    owner_id,
                    key.version,
                    key.locale,
                    key.filters,
                    key.question,
                    epoch,
                    len(vector),
                    np.asarray(vector, dtype=np.float32).tobytes(),
                    answer.answer,
                    answer.answerability.value,
                    json.dumps(citations_to_json(answer.citations)),
                    json.dumps(list(answer.related_entities)),
                    to_text(created_at),
                ),
            )
            conn.execute(
                "DELETE FROM answer_cache WHERE workspace_id = ? AND owner_id = ?"
                " AND (created_at < ? OR rowid NOT IN (SELECT rowid FROM answer_cache"
                " WHERE workspace_id = ? AND owner_id = ? ORDER BY created_at DESC LIMIT ?))",
                (workspace_id, owner_id, to_text(fresh_after), workspace_id, owner_id, keep),
            )
        return True
