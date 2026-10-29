"""The answer cache (ADR-026): what makes a repeated question cheap, and the rules that keep a
repeated answer as true as a fresh one."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

import nodo_api.main as main_module
from nodo_api.adapters.memory import MemoryJobQueue, MemoryMaintenanceStore, MemorySpeech
from nodo_api.adapters.memory.answers import MemoryAnswerStore
from nodo_api.adapters.memory.extractor import KeywordExtractor
from nodo_api.domain.graph import ReviewStatus
from nodo_api.domain.models import Answerability, Locale
from nodo_api.main import create_app
from nodo_api.resilience import DependencyUnavailable
from nodo_api.services.answer_cache import AnswerCache, discriminators, figures_in
from nodo_api.services.index_maintenance import IndexMaintenanceService
from nodo_api.services.ingestion import IngestionService
from nodo_api.services.query import QueryFilters, QueryRequest

WS, USER = "workspace-a", "user-a"
Q1 = "What changed in the ingestion pipeline?"
Q2 = "How did the ingestion pipeline change?"  # the same question in other words
Q_2024 = "What changed in the ingestion pipeline in 2024?"
Q_2025 = "What changed in the ingestion pipeline in 2025?"
Q_NOT = "What did we not decide about the ingestion pipeline?"
Q_MARCO = "What did Marco say about the ingestion pipeline?"
Q_SARA = "What did Sara say about the ingestion pipeline?"
NO_MATCH = "Tell me about quantum chromodynamics"

BASE = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
NEARBY = [1.0, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # cosine 0.9988 with BASE
MEANING = {
    Q1: BASE,
    Q2: NEARBY,
    Q_2024: BASE,
    Q_2025: BASE,
    Q_NOT: BASE,
    Q_MARCO: BASE,
    Q_SARA: BASE,
}


class Vectors:
    """Questions as points: paraphrases land next to each other, any other text on its own axis."""

    def __init__(self) -> None:
        self.fail = False
        self.calls = 0
        self._alone: dict[str, list[float]] = {}

    def __call__(self, question: str) -> list[float]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("the embedding model is down")
        if question in MEANING:
            return MEANING[question]
        axis = 1 + len(self._alone) % 7
        return self._alone.setdefault(question, [1.0 if i == axis else 0.0 for i in range(8)])


class CountingLlm:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0
        self.fail = False
        self.before_answer = None

    def answer(self, *args):
        self.calls += 1
        if self.before_answer is not None:
            self.before_answer()
        if self.fail:
            raise DependencyUnavailable("llm")
        return self._inner.answer(*args)


class Rig:
    def __init__(self, app, cache: AnswerCache, store, llm, vectors) -> None:
        self.app, self.cache, self.store, self.llm, self.vectors = app, cache, store, llm, vectors
        self.container = app.state.container

    def ask(self, question: str, *, locale=Locale.EN, filters=None, user=USER):
        request = QueryRequest(question, locale, filters)
        return self.container.query_service.query(WS, user, request)

    def epoch(self, user: str = USER) -> int:
        return self.store.epoch(WS, user)


def _build_app(settings, monkeypatch) -> Rig:
    made: dict = {}
    real = main_module.build_adapters

    def build(for_settings):
        adapters = real(for_settings)
        made["vectors"], made["store"] = Vectors(), MemoryAnswerStore()
        made["llm"] = adapters.llm = CountingLlm(adapters.llm)
        made["cache"] = adapters.answers = AnswerCache(
            made["store"], made["vectors"], version="test-1", min_similarity=0.95
        )
        return adapters

    monkeypatch.setattr(main_module, "build_adapters", build)
    app = create_app(settings)
    return Rig(app, made["cache"], made["store"], made["llm"], made["vectors"])


@pytest.fixture
def rig(settings, monkeypatch) -> Rig:
    return _build_app(settings, monkeypatch)


# ------------------------------------------------------------------------------------ the point


def test_a_question_in_other_words_is_answered_without_asking_the_model_again(rig) -> None:
    first = rig.ask(Q1)
    assert first.answerability is Answerability.SUPPORTED and first.citations
    assert rig.llm.calls == 1

    second = rig.ask(Q2)
    assert rig.llm.calls == 1  # served from the cache
    assert (second.answer, second.citations, second.related_entities) == (
        first.answer,
        first.citations,
        first.related_entities,
    )
    assert second.answerability is first.answerability and not second.degraded
    assert second.query_trace_id != first.query_trace_id  # each question is its own event


def test_a_question_that_means_something_else_is_answered_afresh(rig) -> None:
    rig.ask(Q1)
    rig.ask("Who proposed the async queue?")
    assert rig.llm.calls == 2


def test_language_filters_and_figures_make_it_a_different_question(rig) -> None:
    rig.ask(Q1)
    rig.ask(Q2, locale=Locale.IT)  # the answer is written in another language
    assert rig.llm.calls == 2
    rig.ask(Q2, filters=QueryFilters(project="alpha"))  # a narrower search
    assert rig.llm.calls == 3
    rig.ask(Q_2024)  # same vector, but it names a number the first one did not
    assert rig.llm.calls == 4
    rig.ask(Q_2025)
    assert rig.llm.calls == 5
    rig.ask(Q_2024)  # and now it is the same question again
    assert rig.llm.calls == 5


def test_a_question_that_denies_or_names_someone_else_is_a_different_question(rig) -> None:
    rig.ask(Q1)
    rig.ask(Q_NOT)  # "what did we not decide": the embedding barely moves, the answer does
    assert rig.llm.calls == 2
    rig.ask(Q_MARCO)
    rig.ask(Q_SARA)  # same words but another person
    assert rig.llm.calls == 4
    rig.ask(Q_NOT)
    rig.ask(Q_MARCO)
    assert rig.llm.calls == 4  # each is a cached question in its own right


@pytest.mark.parametrize(
    ("question", "negated"),
    [
        ("What did we not decide?", True),
        ("What didn't we decide?", True),
        ("Cosa non abbiamo deciso?", True),
        ("Co nie zostało ustalone?", True),
        ("Hvad besluttede vi ikke?", True),
        ("What did we decide?", False),
        ("Cosa abbiamo deciso a marzo?", False),
    ],
)
def test_negation_is_noticed_in_all_four_languages(question, negated) -> None:
    assert discriminators(question)["negated"] is negated


def test_names_are_the_capitalised_words_after_the_first() -> None:
    assert discriminators("What did Marco say to Sara in Q3?")["names"] == ["marco", "q3", "sara"]
    assert discriminators("Marco said what?")["names"] == []  # the first word is a sentence start
    assert discriminators("what about I and a?")["names"] == []


def test_figures_are_compared_as_numbers_not_as_spellings() -> None:
    assert figures_in("in 2024 or 3,5 and 3.5 and 12") == ["12", "2024", "3.5", "3.5"]
    assert figures_in("no numbers here") == []


def test_somebody_elses_question_never_meets_my_answers(rig) -> None:
    mine = rig.ask(Q1)
    theirs = rig.ask(Q1, user="user-b")
    assert rig.llm.calls == 2
    assert mine.citations and not theirs.citations  # user-b has no meetings
    assert rig.epoch("user-b") == 0


# ------------------------------------------------------------------------- what is not kept


def test_an_exact_unanswerable_question_is_kept_until_the_knowledge_changes(rig) -> None:
    first = rig.ask(NO_MATCH)
    assert first.answerability is Answerability.UNANSWERABLE
    rig.ask(NO_MATCH)
    assert rig.llm.calls == 1

    rig.cache.invalidate(WS, USER)
    rig.ask(NO_MATCH)
    assert rig.llm.calls == 2  # changed knowledge is searched again


def test_an_unanswerable_question_does_not_serve_a_semantic_neighbour(rig) -> None:
    rig.ask(NO_MATCH)
    rig.ask("Explain quantum chromodynamics")
    assert rig.llm.calls == 2


def test_the_sources_only_fallback_is_not_kept(rig) -> None:
    rig.llm.fail = True
    fallback = rig.ask(Q1)
    assert fallback.degraded and fallback.citations
    rig.llm.fail = False
    rig.ask(Q1)  # the model is back: a real answer, and this one is kept
    assert rig.llm.calls == 2
    rig.ask(Q2)
    assert rig.llm.calls == 2


# ------------------------------------------------------------------- the cache never costs an answe


def test_a_model_that_can_not_embed_the_question_only_means_no_cache(rig) -> None:
    rig.vectors.fail = True
    assert rig.ask(Q1).citations
    assert rig.ask(Q1).citations
    assert rig.llm.calls == 2


def test_a_store_that_fails_only_means_no_cache(rig, monkeypatch) -> None:
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(rig.store, "nearest", broken)
    monkeypatch.setattr(rig.store, "put", broken)
    assert rig.ask(Q1).citations
    assert rig.ask(Q1).citations


# ------------------------------------------------------------------------- never stale


def test_an_answer_worked_out_while_the_data_changed_is_not_kept(rig) -> None:
    # A meeting is deleted while the question is being answered; its quotes may be in the answer.
    rig.llm.before_answer = lambda: rig.cache.invalidate(WS, USER)
    rig.ask(Q1)
    rig.llm.before_answer = None
    rig.ask(Q2)
    assert rig.llm.calls == 2


def test_an_entry_whose_quote_was_changed_without_telling_the_cache_is_not_trusted(rig) -> None:
    first = rig.ask(Q1)
    meeting_id = first.citations[0].meeting_id
    segments = rig.container.transcripts.latest(WS, meeting_id)
    revised = tuple(
        replace(s, text=s.text + " (corrected)", version=s.version + 1) for s in segments
    )
    rig.container.transcripts.add_version(WS, meeting_id, revised)  # behind the cache's back
    epoch = rig.epoch()

    rig.ask(Q2)
    assert rig.llm.calls == 2  # not repeated
    assert rig.epoch() == epoch + 1  # and the owner's other entries were dropped too


def test_an_entry_whose_meeting_is_gone_is_not_trusted(rig) -> None:
    first = rig.ask(Q1)
    meeting = rig.container.meetings.get_owned(WS, USER, first.citations[0].meeting_id)
    rig.container.meetings.save(WS, replace(meeting, tombstoned_at=datetime.now(UTC)))
    rig.ask(Q2)
    assert rig.llm.calls == 2


# ------------------------------------------------------- every kind of change drops the entries


def _correct_a_transcript(rig, attestation) -> None:
    rig.container.meeting_service.review(
        WS, USER, "seed-m3", [{"segment_id": "seg-003", "text": "Giulia confirmed the queue."}]
    )


def _delete_a_meeting(rig, attestation) -> None:
    rig.container.meeting_service.delete(WS, USER, "seed-m3")


def _decide_a_proposal(rig, attestation) -> None:
    graph = rig.container.graph
    seeds = [e.id for e in graph.overview(WS, USER, 5)]
    edges = graph.neighborhood(
        WS, USER, seeds, depth=1, node_limit=50, fanout=15, include_proposed=True, as_of=None
    ).edges
    assert edges, "the demo data has relations"
    rig.container.graph_service.review(WS, USER, edges[0].id, ReviewStatus.REJECTED)


def _new_ingestion(rig) -> IngestionService:
    c = rig.container
    return IngestionService(
        c.meetings,
        c.transcripts,
        c.blobs,
        c.retriever,
        MemorySpeech(),
        ("en-GB",),
        KeywordExtractor(),
        c.graph,
        answers=rig.cache,
    )


def _process_a_recording(rig, attestation) -> None:
    created = rig.container.meeting_service.create(WS, USER, "cache-key-1", attestation)
    rig.container.meeting_service.complete(WS, USER, created.id)
    _new_ingestion(rig).process(WS, created.id)


def _reanalyze_a_meeting(rig, attestation) -> None:
    _new_ingestion(rig).reanalyze(WS, "seed-m1")


def _repair_the_index(rig, attestation) -> None:
    rig.container.retriever.erase(WS, "seed-m2")  # the index lost a meeting
    service = IndexMaintenanceService(
        MemoryMaintenanceStore(rig.container.meetings),
        rig.container.transcripts,
        rig.container.retriever,
        rig.container.graph,
        MemoryJobQueue(),
        answers=rig.cache,
    )
    assert service.repair().repaired


@pytest.mark.parametrize(
    "change",
    [
        _correct_a_transcript,
        _delete_a_meeting,
        _decide_a_proposal,
        _process_a_recording,
        _reanalyze_a_meeting,
        _repair_the_index,
    ],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_a_change_to_what_is_known_drops_the_owners_answers(rig, attestation, change) -> None:
    rig.ask(Q1)
    rig.ask("Who proposed the async queue?")
    assert rig.llm.calls == 2 and rig.epoch() == 0

    change(rig, attestation)

    assert rig.epoch() > 0
    assert rig.epoch("user-b") == 0  # nobody else's answers were touched
    before = rig.llm.calls
    rig.ask(Q2)
    assert rig.llm.calls == before + 1  # asked again, not repeated


# --------------------------------------------------------------------------------- switched off


def test_without_a_cache_questions_are_answered_as_before(app) -> None:
    result = app.state.container.query_service.query(WS, USER, QueryRequest(Q1, Locale.EN))
    assert result.citations and result.answerability is Answerability.SUPPORTED
