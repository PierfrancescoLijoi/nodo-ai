"""In-memory answer store: the reference for the semantics every AnswerStore must have
(tests/answer_cache_contract.py runs the same tests on SQLite and PostgreSQL)."""

from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from nodo_api.domain.answer_cache import AnswerKey, CachedAnswer
from nodo_api.domain.models import Answerability


@dataclass(slots=True)
class _Row:
    epoch: int
    vector: list[float]
    answer: CachedAnswer
    created_at: datetime


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = (math.sqrt(sum(x * x for x in a)) or 1.0) * (math.sqrt(sum(x * x for x in b)) or 1.0)
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm


class MemoryAnswerStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._epochs: dict[tuple[str, str], int] = {}
        self._rows: dict[tuple[str, str], dict[AnswerKey, _Row]] = {}

    def epoch(self, workspace_id: str, owner_id: str) -> int:
        with self._lock:
            return self._epochs.get((workspace_id, owner_id), 0)

    def bump(self, workspace_id: str, owner_id: str) -> None:
        with self._lock:
            scope = (workspace_id, owner_id)
            self._epochs[scope] = self._epochs.get(scope, 0) + 1
            self._rows.pop(scope, None)

    def nearest(
        self,
        workspace_id: str,
        owner_id: str,
        key: AnswerKey,
        vector: Sequence[float],
        min_similarity: float,
        fresh_after: datetime,
    ) -> CachedAnswer | None:
        with self._lock:
            scope = (workspace_id, owner_id)
            epoch = self._epochs.get(scope, 0)
            best: tuple[float, AnswerKey, _Row] | None = None
            for stored_key, row in self._rows.get(scope, {}).items():
                if (
                    row.epoch != epoch
                    or row.created_at < fresh_after
                    or (stored_key.version, stored_key.locale, stored_key.filters)
                    != (key.version, key.locale, key.filters)
                    or len(row.vector) != len(vector)
                    or (
                        row.answer.answerability is Answerability.UNANSWERABLE
                        and stored_key.question != key.question
                    )
                ):
                    continue
                score = cosine(vector, row.vector)
                if score >= min_similarity and (best is None or score > best[0]):
                    best = (score, stored_key, row)
        return None if best is None else replace(best[2].answer, similarity=best[0])

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
        with self._lock:
            scope = (workspace_id, owner_id)
            if self._epochs.get(scope, 0) != epoch:
                return False
            rows = self._rows.setdefault(scope, {})
            rows[key] = _Row(epoch, [float(x) for x in vector], answer, created_at)
            for stale in [k for k, r in rows.items() if r.created_at < fresh_after]:
                del rows[stale]
            newest_first = sorted(rows.items(), key=lambda kv: kv[1].created_at, reverse=True)
            for old, _ in newest_first[keep:]:
                del rows[old]
        return True
