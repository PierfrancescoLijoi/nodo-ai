"""Answer cache in PostgreSQL (migration 0011), scoped by workspace with row level security and by
owner in every query (ADR-026). Exact search over the owner's few hundred entries."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

from nodo_api.adapters.azure.graph_pg import _vector_text
from nodo_api.adapters.azure.postgres import PostgresDatabase
from nodo_api.domain.answer_cache import (
    AnswerKey,
    CachedAnswer,
    citations_from_json,
    citations_to_json,
)
from nodo_api.domain.models import Answerability

_EPOCH = (
    "COALESCE((SELECT epoch FROM answer_cache_epoch"
    " WHERE workspace_id = %(ws)s AND owner_id = %(owner)s), 0)"
)


class PgAnswerStore:
    def __init__(self, db: PostgresDatabase) -> None:
        self._db = db

    def epoch(self, workspace_id: str, owner_id: str) -> int:
        with self._db.workspace(workspace_id) as conn:
            row = conn.execute(
                f"SELECT {_EPOCH} AS epoch", {"ws": workspace_id, "owner": owner_id}
            ).fetchone()
        return int(row["epoch"])

    def bump(self, workspace_id: str, owner_id: str) -> None:
        with self._db.workspace(workspace_id) as conn:
            conn.execute(
                "INSERT INTO answer_cache_epoch (workspace_id, owner_id, epoch) VALUES (%s, %s, 1)"
                " ON CONFLICT (workspace_id, owner_id)"
                " DO UPDATE SET epoch = answer_cache_epoch.epoch + 1",
                (workspace_id, owner_id),
            )
            conn.execute(
                "DELETE FROM answer_cache WHERE workspace_id = %s AND owner_id = %s",
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
        with self._db.workspace(workspace_id) as conn:
            row = conn.execute(
                "SELECT answer, answerability, citations, related_entities,"
                " 1 - (vector <=> %(vec)s::vector) AS similarity FROM answer_cache"
                " WHERE workspace_id = %(ws)s AND owner_id = %(owner)s AND version = %(version)s"
                " AND locale = %(locale)s AND filters = %(filters)s AND dims = %(dims)s"
                f" AND epoch = {_EPOCH} AND created_at >= %(fresh)s"
                " AND 1 - (vector <=> %(vec)s::vector) >= %(min)s"
                " ORDER BY vector <=> %(vec)s::vector LIMIT 1",
                {
                    "ws": workspace_id,
                    "owner": owner_id,
                    "version": key.version,
                    "locale": key.locale,
                    "filters": key.filters,
                    "dims": len(vector),
                    "vec": _vector_text(vector),
                    "fresh": fresh_after,
                    "min": min_similarity,
                },
            ).fetchone()
        if row is None:
            return None
        return CachedAnswer(
            row["answer"],
            Answerability(row["answerability"]),
            citations_from_json(row["citations"]),
            tuple(row["related_entities"]),
            float(row["similarity"]),
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
        params = {
            "ws": workspace_id,
            "owner": owner_id,
            "version": key.version,
            "locale": key.locale,
            "filters": key.filters,
            "question": key.question,
            "epoch": epoch,
            "dims": len(vector),
            "vec": _vector_text(vector),
            "answer": answer.answer,
            "answerability": answer.answerability.value,
            "citations": json.dumps(citations_to_json(answer.citations)),
            "entities": json.dumps(list(answer.related_entities)),
            "created": created_at,
            "fresh": fresh_after,
            "keep": keep,
        }
        with self._db.workspace(workspace_id) as conn:
            # The epoch is compared in the statement that writes, so a change that lands first wins.
            written = conn.execute(
                "INSERT INTO answer_cache (workspace_id, owner_id, version, locale, filters,"
                " question_key, epoch, dims, vector, answer, answerability, citations,"
                " related_entities, created_at)"
                " SELECT %(ws)s, %(owner)s, %(version)s, %(locale)s, %(filters)s, %(question)s,"
                " %(epoch)s, %(dims)s, %(vec)s::vector, %(answer)s, %(answerability)s,"
                " %(citations)s::jsonb, %(entities)s::jsonb, %(created)s"
                f" WHERE {_EPOCH} = %(epoch)s"
                " ON CONFLICT (workspace_id, owner_id, version, locale, filters, question_key)"
                " DO UPDATE SET epoch = EXCLUDED.epoch, dims = EXCLUDED.dims,"
                " vector = EXCLUDED.vector, answer = EXCLUDED.answer,"
                " answerability = EXCLUDED.answerability, citations = EXCLUDED.citations,"
                " related_entities = EXCLUDED.related_entities, created_at = EXCLUDED.created_at",
                params,
            ).rowcount
            if not written:
                return False
            conn.execute(
                "DELETE FROM answer_cache WHERE workspace_id = %(ws)s AND owner_id = %(owner)s"
                " AND (created_at < %(fresh)s OR (version, locale, filters, question_key) IN ("
                " SELECT version, locale, filters, question_key FROM answer_cache"
                " WHERE workspace_id = %(ws)s AND owner_id = %(owner)s"
                " ORDER BY created_at DESC OFFSET %(keep)s))",
                params,
            )
        return True
