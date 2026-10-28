"""Answers already given, found again by the meaning of the question (ADR-026).

Rules that keep a cached answer as trustworthy as a fresh one:
- an entry belongs to one workspace and one owner and answers that owner's questions only;
- it is used only for the same language, the same filters, the same figures, names and
  negation in the question, and the same models and prompts (`version`);
- any change to the owner's meetings, transcripts or graph invalidates the owner's entries, and an
  answer computed before the change can not be stored after it (epoch);
- on a hit the quotes are checked again against the transcript, so an entry never outlives the text
  it cites;
- only complete answers are stored: not the sources-only fallback, not "I can not answer".

The cache is an optimisation that must never cost an answer: lookup and store failures are logged
and the question is answered as if there were no cache. Invalidation is the opposite: it raises, so
the operation that changed the data can be repeated."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from nodo_api.domain.answer_cache import AnswerKey, CachedAnswer, question_hash
from nodo_api.domain.models import Answerability, Locale
from nodo_api.ports import AnswerStore
from nodo_api.telemetry import instruments

logger = logging.getLogger("nodo_answer_cache")

DEFAULT_TTL_SECONDS = 24 * 3600
DEFAULT_KEEP = 200
_FIGURES = re.compile(r"\d+(?:[.,]\d+)?")
_WORDS = re.compile(r"\w+")
_CONTRACTED_NOT = re.compile(r"n['\u2019]t\b", re.IGNORECASE)
# Embedding models score "what did we decide" and "what did we not decide" as nearer than two
# honest paraphrases (measured: 0.97 against 0.93 to 0.98), so negation can not be left to the
# similarity. English, Italian, Polish, Danish.
_NEGATIONS = frozenset(
    {
        # English
        "not",
        "no",
        "never",
        "without",
        "none",
        "nobody",
        "nothing",
        # Italian
        "non",
        "mai",
        "senza",
        "nessuno",
        "nessuna",
        "niente",
        "nulla",
        # Polish
        "nie",
        "nigdy",
        "bez",
        "nikt",
        "nic",
        "żaden",
        "żadna",
        # Danish
        "ikke",
        "aldrig",
        "uden",
        "ingen",
        "intet",
        "ingenting",
    }
)


@dataclass(frozen=True, slots=True)
class Ticket:
    """What a miss needs to store its answer afterwards."""

    workspace_id: str
    owner_id: str
    key: AnswerKey
    vector: tuple[float, ...]
    epoch: int


@dataclass(frozen=True, slots=True)
class Lookup:
    hit: CachedAnswer | None = None
    ticket: Ticket | None = (
        None  # None: this question can not use the cache (no vector, or a failure)
    )


def figures_in(question: str) -> list[str]:
    """Two questions that name different numbers ask different things, however alike they read."""
    return sorted(m.replace(",", ".") for m in _FIGURES.findall(question))


def discriminators(question: str) -> dict[str, Any]:
    """What the meaning of a question can hinge on while its embedding hardly moves: the numbers it
    names, the people and things it names (capitalised words after the first), and whether it
    denies something. Two questions are the same question only if these are equal."""
    words = _WORDS.findall(question)
    names = sorted(
        {w.casefold() for i, w in enumerate(words) if i > 0 and len(w) > 1 and w[0].isupper()}
    )
    negated = bool(_CONTRACTED_NOT.search(question)) or any(
        w.casefold() in _NEGATIONS for w in words
    )
    return {"figures": figures_in(question), "names": names, "negated": negated}


class AnswerCache:
    def __init__(
        self,
        store: AnswerStore,
        embed_question: Callable[[str], Sequence[float] | None],
        *,
        version: str,
        min_similarity: float,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        keep: int = DEFAULT_KEEP,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._embed = embed_question
        self._version = version
        self._min_similarity = min_similarity
        self._ttl = timedelta(seconds=ttl_seconds)
        self._keep = keep
        self._clock = clock

    def key(self, question: str, locale: Locale, filters: dict[str, Any] | None) -> AnswerKey:
        shape = json.dumps(
            {"filters": filters or {}, **discriminators(question)},
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        return AnswerKey(self._version, locale.value, shape, question_hash(question))

    def lookup(
        self,
        workspace_id: str,
        owner_id: str,
        question: str,
        locale: Locale,
        filters: dict[str, Any] | None,
    ) -> Lookup:
        try:
            vector = self._embed(question)
            if not vector:
                return Lookup()
            vector = tuple(float(x) for x in vector)
            key = self.key(question, locale, filters)
            hit = self._store.nearest(
                workspace_id,
                owner_id,
                key,
                vector,
                self._min_similarity,
                self._clock() - self._ttl,
            )
            # Read before the answer is worked out: a change that lands while the question is being
            # answered raises the epoch and the answer is then not stored.
            epoch = self._store.epoch(workspace_id, owner_id)
            return Lookup(hit, Ticket(workspace_id, owner_id, key, vector, epoch))
        except Exception as error:
            logger.error("answer_cache_lookup_failed type=%s", type(error).__name__)
            return Lookup()

    def remember(self, ticket: Ticket, answer: CachedAnswer, *, degraded: bool) -> bool:
        if degraded or answer.answerability is Answerability.UNANSWERABLE or not answer.citations:
            return False
        now = self._clock()
        try:
            return self._store.put(
                ticket.workspace_id,
                ticket.owner_id,
                ticket.key,
                ticket.vector,
                answer,
                epoch=ticket.epoch,
                created_at=now,
                fresh_after=now - self._ttl,
                keep=self._keep,
            )
        except Exception as error:
            logger.error("answer_cache_store_failed type=%s", type(error).__name__)
            return False

    def invalidate(self, workspace_id: str, owner_id: str) -> None:
        """The owner's meetings, transcripts or graph changed. Raises if it can not be recorded."""
        self._store.bump(workspace_id, owner_id)
        instruments.answer_cache.add(1, {"outcome": "invalidated"})


def invalidate(answers: AnswerCache | None, workspace_id: str, owner_id: str) -> None:
    """For the services that change data: a no-op where there is no cache."""
    if answers is not None:
        answers.invalidate(workspace_id, owner_id)
