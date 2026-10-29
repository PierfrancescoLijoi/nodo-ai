from __future__ import annotations

import pytest

from nodo_api.domain.graph import (
    EXISTING_PREFIX,
    EntityType,
    ExtractedEntity,
    ExtractedRelation,
    Extraction,
    ExtractionError,
    RelationKind,
)
from nodo_api.domain.models import Locale, TranscriptSegment
from nodo_api.services.extraction import ChunkedExtractor, merge, windows


def _segments(count: int, size: int = 100) -> tuple[TranscriptSegment, ...]:
    return tuple(
        TranscriptSegment(f"s{i}", "m1", "S1", i, i + 1, "x" * size, 0.9, 1, Locale.EN)
        for i in range(count)
    )


def test_windows_keep_segments_whole_and_in_order() -> None:
    parts = windows(_segments(10, 100), max_tokens=100)  # about 300 characters per window
    assert len(parts) > 1
    assert [s.id for part in parts for s in part] == [f"s{i}" for i in range(10)]
    assert all(parts)


def test_a_transcript_that_fits_is_one_window_and_an_oversized_segment_still_goes_out() -> None:
    assert len(windows(_segments(3, 10), max_tokens=1000)) == 1
    assert len(windows(_segments(1, 5000), max_tokens=10)) == 1


def _part(summary, people, decision=None, supersedes=None):
    entities = [
        ExtractedEntity(f"p{i}", EntityType.PERSON, name, (f"s{i}",))
        for i, name in enumerate(people)
    ]
    relations = []
    if decision:
        entities.append(ExtractedEntity("d", EntityType.DECISION, decision, ("sd",)))
        relations.append(ExtractedRelation("d", "p0", RelationKind.PROPOSED_BY, 0.6, ("sd",)))
        if supersedes:
            relations.append(
                ExtractedRelation(
                    "d", f"{EXISTING_PREFIX}{supersedes}", RelationKind.SUPERSEDES, 0.7, ("sd",)
                )
            )
    return Extraction(summary, tuple(entities), tuple(relations))


def test_the_same_entity_in_two_windows_becomes_one_with_both_evidences() -> None:
    merged = merge([_part("A.", ["Sara", "Marco"]), _part("B.", ["sara"], decision="Use a queue")])
    people = [e for e in merged.entities if e.type is EntityType.PERSON]
    assert sorted(p.label for p in people) == ["Marco", "Sara"]
    sara = next(p for p in people if p.label == "Sara")
    assert set(sara.evidence) == {"s0"} or "s0" in sara.evidence
    assert merged.summary == "A. B."
    decision = next(e for e in merged.entities if e.type is EntityType.DECISION)
    relation = merged.relations[0]
    assert relation.source == decision.key and relation.target == sara.key


def test_relations_to_existing_decisions_are_kept_and_duplicates_collapse() -> None:
    first = _part("", ["Sara"], decision="Use a queue", supersedes="old-id")
    second = _part("", ["Sara"], decision="Use a queue", supersedes="old-id")
    merged = merge([first, second])
    kinds = sorted(r.kind.value for r in merged.relations)
    assert kinds == ["PROPOSED_BY", "SUPERSEDES"]
    supersedes = next(r for r in merged.relations if r.kind is RelationKind.SUPERSEDES)
    assert supersedes.target == f"{EXISTING_PREFIX}old-id"


def test_a_relation_to_an_unknown_key_is_dropped() -> None:
    bad = Extraction(
        "",
        (ExtractedEntity("a", EntityType.PERSON, "Ann", ("s1",)),),
        (ExtractedRelation("a", "ghost", RelationKind.DEPENDS_ON, 0.5, ("s1",)),),
    )
    assert merge([bad, bad]).relations == ()


class Recorder:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def extract(self, segments, locale, known_decisions, speakers=None):
        self.calls.append(len(segments))
        return _part(f"part {len(self.calls)}.", ["Sara"])


def test_chunked_extractor_passes_short_transcripts_straight_through() -> None:
    inner = Recorder()
    ChunkedExtractor(inner, max_tokens=10_000).extract(_segments(5), Locale.EN, (), None)
    assert inner.calls == [5]


def test_chunked_extractor_calls_the_model_once_per_window_and_merges() -> None:
    inner = Recorder()
    result = ChunkedExtractor(inner, max_tokens=100).extract(_segments(10), Locale.EN, (), None)
    assert len(inner.calls) > 1 and sum(inner.calls) == 10
    assert [e.label for e in result.entities] == ["Sara"]
    assert result.summary.startswith("part 1.")


class FailsOnLongInput:
    """Fails whenever it is given more than two segments, like a model that runs out of room."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def extract(self, segments, locale, known_decisions, speakers=None):
        self.calls.append(len(segments))
        if len(segments) > 2:
            raise ExtractionError("too long")
        return _part(f"{len(segments)} segments.", [f"Person{segments[0].id}"])


def test_a_window_the_model_fails_on_is_halved_and_retried() -> None:
    inner = FailsOnLongInput()
    result = ChunkedExtractor(inner, max_tokens=100_000).extract(_segments(6), Locale.EN, (), None)
    assert inner.calls[0] == 6 and set(inner.calls[1:]) <= {3, 1, 2}
    assert len([e for e in result.entities if e.type is EntityType.PERSON]) >= 2


def test_nothing_extracted_at_all_is_an_error_so_the_job_is_retried() -> None:
    class AlwaysFails:
        def extract(self, *args, **kwargs):
            raise ExtractionError("no")

    with pytest.raises(ExtractionError):
        ChunkedExtractor(AlwaysFails(), max_tokens=100).extract(_segments(4), Locale.EN, (), None)


def test_one_hopeless_stretch_is_skipped_but_the_rest_is_kept() -> None:
    class FailsOnOneSegment:
        def extract(self, segments, locale, known_decisions, speakers=None):
            if any(s.id == "s3" for s in segments) and len(segments) == 1:
                raise ExtractionError("no")
            if any(s.id == "s3" for s in segments):
                raise ExtractionError("no")
            return _part("ok.", [f"P{segments[0].id}"])

    result = ChunkedExtractor(FailsOnOneSegment(), max_tokens=100).extract(
        _segments(6), Locale.EN, (), None
    )
    assert result.entities, "the segments that could be read still count"
    assert all("s3" not in e.evidence for e in result.entities)


def test_the_merged_summary_is_a_few_whole_distinct_sentences() -> None:
    parts = [
        _part("The group chose a queue. Marco will measure the cost.", ["A"]),
        _part("The group chose a queue. Katarzyna reviews the Polish text.", ["B"]),
        _part("A fourth sentence that is not needed at all.", ["C"]),
    ]
    merged = merge(parts)
    assert merged.summary == (
        "The group chose a queue. Marco will measure the cost. Katarzyna reviews the Polish text."
    )
    long_part = _part("word " * 400 + "end.", ["D"])
    assert (
        merge([long_part, long_part]).summary == ""
    )  # one sentence over the limit is dropped, not cut
