"""Behaviour every AnswerStore must have: memory, SQLite and PostgreSQL run the same tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nodo_api.domain.answer_cache import AnswerKey, CachedAnswer
from nodo_api.domain.models import Answerability, Citation

OWNER = "alice"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=1)
KEY = AnswerKey(version="v1", locale="en", filters="{}", question="q-1")
ANSWER = CachedAnswer(
    "Marco owns the rollout.",
    Answerability.SUPPORTED,
    (Citation("m1", "s1", 1000, 4000, "Marco takes the rollout.", "S1"),),
    ("Marco",),
)
NO_ANSWER = CachedAnswer("", Answerability.UNANSWERABLE, (), ())


def put(store, ws, key=KEY, vector=(1.0, 0.0, 0.0), owner=OWNER, **over):
    args = {
        "epoch": store.epoch(ws, owner),
        "created_at": NOW,
        "fresh_after": LONG_AGO,
        "keep": 50,
        **over,
    }
    return store.put(ws, owner, key, list(vector), ANSWER, **args)


def find(store, ws, vector=(1.0, 0.0, 0.0), key=KEY, owner=OWNER, minimum=0.9):
    return store.nearest(ws, owner, key, list(vector), minimum, LONG_AGO)


class AnswerStoreContract:
    def test_nothing_is_found_before_anything_is_stored(self, store, ws) -> None:
        assert find(store, ws) is None
        assert store.epoch(ws, OWNER) == 0

    def test_an_answer_comes_back_whole_with_its_similarity(self, store, ws) -> None:
        assert put(store, ws)
        hit = find(store, ws, vector=(2.0, 0.0, 0.0))  # same direction, other length
        assert hit is not None
        assert (hit.answer, hit.answerability, hit.related_entities) == (
            ANSWER.answer,
            ANSWER.answerability,
            ANSWER.related_entities,
        )
        assert hit.citations == ANSWER.citations
        assert hit.similarity > 0.999

    def test_a_question_that_means_something_else_finds_nothing(self, store, ws) -> None:
        put(store, ws)
        assert find(store, ws, vector=(0.0, 1.0, 0.0)) is None
        near = find(store, ws, vector=(1.0, 0.3, 0.0), minimum=0.9)  # cosine 0.958
        assert near is not None and 0.95 < near.similarity < 0.97
        assert find(store, ws, vector=(1.0, 0.3, 0.0), minimum=0.97) is None

    def test_the_nearest_of_several_wins(self, store, ws) -> None:
        put(store, ws, key=AnswerKey("v1", "en", "{}", "q-a"), vector=(1.0, 0.2, 0.0))
        put(store, ws, key=AnswerKey("v1", "en", "{}", "q-b"), vector=(1.0, 0.0, 0.0))
        assert find(store, ws, vector=(1.0, 0.01, 0.0)).similarity > 0.9999

    def test_an_unanswerable_entry_matches_only_the_exact_question(self, store, ws) -> None:
        assert store.put(
            ws,
            OWNER,
            KEY,
            [1.0, 0.0, 0.0],
            NO_ANSWER,
            epoch=0,
            created_at=NOW,
            fresh_after=LONG_AGO,
            keep=50,
        )
        assert find(store, ws).answerability is Answerability.UNANSWERABLE
        similar_key = AnswerKey("v1", "en", "{}", "q-similar")
        assert find(store, ws, key=similar_key, vector=(1.0, 0.01, 0.0)) is None

    def test_version_language_and_filters_must_all_match(self, store, ws) -> None:
        put(store, ws)
        for other in (
            AnswerKey("v2", "en", "{}", "q-1"),
            AnswerKey("v1", "it", "{}", "q-1"),
            AnswerKey("v1", "en", '{"project":"x"}', "q-1"),
        ):
            assert find(store, ws, key=other) is None

    def test_the_same_question_replaces_its_entry(self, store, ws) -> None:
        put(store, ws)
        again = CachedAnswer("Updated.", Answerability.PARTIAL, (), ())
        assert store.put(
            ws,
            OWNER,
            KEY,
            [1.0, 0.0, 0.0],
            again,
            epoch=0,
            created_at=NOW,
            fresh_after=LONG_AGO,
            keep=50,
        )
        hit = find(store, ws)
        assert (hit.answer, hit.citations) == ("Updated.", ())

    def test_nobody_else_sees_an_entry(self, store, ws) -> None:
        put(store, ws)
        assert find(store, ws, owner="mallory") is None  # same workspace, another person
        assert find(store, f"{ws}-other") is None  # another workspace

    def test_a_change_drops_everything_and_raises_the_epoch(self, store, ws) -> None:
        put(store, ws)
        put(store, ws, owner="bob")
        store.bump(ws, OWNER)
        assert store.epoch(ws, OWNER) == 1 and find(store, ws) is None
        assert find(store, ws, owner="bob") is not None  # someone else's entries stay

    def test_an_answer_computed_before_a_change_is_refused(self, store, ws) -> None:
        before = store.epoch(ws, OWNER)
        store.bump(ws, OWNER)  # a meeting was deleted while the question was being answered
        assert put(store, ws, epoch=before) is False
        assert find(store, ws) is None
        assert put(store, ws) is True

    def test_old_entries_are_not_served_and_are_removed_on_the_next_write(self, store, ws) -> None:
        assert put(
            store,
            ws,
            created_at=NOW - timedelta(hours=30),
            fresh_after=LONG_AGO - timedelta(days=1),
        )
        assert find(store, ws) is None  # older than fresh_after (LONG_AGO is 24 h before NOW)
        put(store, ws, key=AnswerKey("v1", "en", "{}", "q-2"), vector=(0.0, 1.0, 0.0))
        assert (
            store.nearest(ws, OWNER, KEY, [1.0, 0.0, 0.0], 0.9, LONG_AGO - timedelta(days=2))
            is None
        )

    def test_only_the_newest_entries_are_kept(self, store, ws) -> None:
        for n in range(5):
            put(
                store,
                ws,
                key=AnswerKey("v1", "en", "{}", f"q-{n}"),
                vector=(1.0, float(n), 0.0),
                created_at=NOW + timedelta(minutes=n),
                keep=3,
            )
        survivors = [
            n
            for n in range(5)
            if find(store, ws, vector=(1.0, float(n), 0.0), minimum=0.99999) is not None
        ]
        assert survivors == [2, 3, 4]

    def test_a_vector_of_another_size_is_never_compared(self, store, ws) -> None:
        put(store, ws)  # three numbers
        assert find(store, ws, vector=(1.0, 0.0)) is None  # the embedding model changed
