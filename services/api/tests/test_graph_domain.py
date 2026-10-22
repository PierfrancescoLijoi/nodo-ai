from __future__ import annotations

import pytest

from nodo_api.domain.graph import (
    EXISTING_PREFIX,
    MAX_ENTITIES,
    MAX_LABEL,
    EntityType,
    ExtractedEntity,
    ExtractedRelation,
    Extraction,
    RelationKind,
    entity_id,
    normalize_label,
    relation_id,
    validate_extraction,
)

SEGMENTS = frozenset({"s1", "s2", "s3"})
P, T, D, A = EntityType.PERSON, EntityType.TOPIC, EntityType.DECISION, EntityType.ACTION
K = RelationKind


def ent(key, kind, label, *segments):
    return ExtractedEntity(key, kind, label, tuple(segments))


def rel(source, kind, target, *segments, confidence=0.8):
    return ExtractedRelation(source, target, kind, confidence, tuple(segments))


def clean(entities=(), relations=(), known=None):
    return validate_extraction(
        Extraction("summary", tuple(entities), tuple(relations)), SEGMENTS, known
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Łukasz   KOWALSKI ", "lukasz kowalski"),
        ("Ørsted A/S", "orsted a/s"),
        ("José", "jose"),
        (chr(0xFF2E) + chr(0xFF2F) + chr(0xFF24) + chr(0xFF2F), "nodo"),  # full-width forms
        ("Straße", "strasse"),
        ("", ""),
    ],
)
def test_labels_are_normalised_for_recognition(raw, expected) -> None:
    assert normalize_label(raw) == expected


def test_identity_depends_on_workspace_owner_type_and_name_only() -> None:
    base = entity_id("w", "o", P, "marco")
    assert entity_id("w", "o", P, "marco") == base
    assert (
        len(
            {
                base,
                entity_id("w2", "o", P, "marco"),
                entity_id("w", "o2", P, "marco"),
                entity_id("w", "o", T, "marco"),
                entity_id("w", "o", P, "sara"),
            }
        )
        == 5
    )
    assert relation_id("w", "o", "a", K.ABOUT, "b") != relation_id("w", "o", "b", K.ABOUT, "a")


def test_entities_without_evidence_or_with_unknown_segments_are_dropped() -> None:
    result = clean([ent("a", P, "Marco", "s1"), ent("b", P, "Ghost", "s9"), ent("c", P, "Empty")])
    assert [e.label for e in result.entities] == ["Marco"]


def test_labels_are_trimmed_capped_and_deduplicated() -> None:
    long = "x" * (MAX_LABEL + 50)
    result = clean(
        [
            ent("a", P, "  Marco   Rossi ", "s1", "s1", "s2"),
            ent("b", P, "marco rossi", "s1"),  # same thing, other spelling
            ent("c", T, long, "s1"),
            ent("d", EntityType.MEETING, "Meeting", "s1"),  # meetings are made by the system
            ent("e", P, "   ", "s1"),
        ]
    )
    assert result.entities[0].label == "Marco Rossi"
    assert result.entities[0].evidence == ("s1", "s2")
    assert len(result.entities[1].label) == MAX_LABEL
    assert len(result.entities) == 2


def test_the_number_of_entities_is_capped() -> None:
    many = [ent(f"k{i}", T, f"Topic {i}", "s1") for i in range(MAX_ENTITIES + 30)]
    assert len(clean(many).entities) == MAX_ENTITIES


def test_relations_need_known_endpoints_the_right_types_and_evidence() -> None:
    entities = [
        ent("p", P, "Marco", "s1"),
        ent("d", D, "Use a queue", "s1"),
        ent("t", T, "Queue", "s1"),
        ent("a", A, "Benchmark", "s1"),
    ]
    good = [
        rel("d", K.PROPOSED_BY, "p", "s1"),
        rel("d", K.ABOUT, "t", "s1", "s9"),  # unknown segment dropped, the known one remains
        rel("a", K.ASSIGNED_TO, "p", "s2"),
    ]
    bad = [
        rel("p", K.PROPOSED_BY, "d", "s1"),  # wrong direction
        rel("a", K.PROPOSED_BY, "p", "s1"),  # only decisions are proposed
        rel("d", K.ASSIGNED_TO, "p", "s1"),  # only actions are assigned
        rel("d", K.ABOUT, "ghost", "s1"),  # unknown endpoint
        rel("d", K.ABOUT, "t"),  # no evidence
        rel("d", K.ABOUT, "t", "s9"),  # evidence not in the transcript
        rel("d", K.ABOUT, "d", "s1"),  # self relation
        rel("d", K.MENTIONED_IN, "t", "s1"),  # the system records mentions itself
        rel("t", K.SUPERSEDES, "t", "s1"),
    ]
    result = clean(entities, good + bad)
    assert {(r.source, r.kind, r.target) for r in result.relations} == {
        ("d", K.PROPOSED_BY, "p"),
        ("d", K.ABOUT, "t"),
        ("a", K.ASSIGNED_TO, "p"),
    }
    assert next(r for r in result.relations if r.kind is K.ABOUT).evidence == ("s1",)


def test_a_new_decision_can_replace_a_known_one_but_not_an_unknown_one() -> None:
    known = {"old-1": D, "person-1": P}
    entities = [ent("d", D, "New decision", "s1")]
    ok = rel("d", K.SUPERSEDES, f"{EXISTING_PREFIX}old-1", "s1")
    wrong_type = rel("d", K.SUPERSEDES, f"{EXISTING_PREFIX}person-1", "s1")
    unknown = rel("d", K.SUPERSEDES, f"{EXISTING_PREFIX}missing", "s1")
    result = clean(entities, [ok, wrong_type, unknown], known)
    assert [r.target for r in result.relations] == [f"{EXISTING_PREFIX}old-1"]


def test_two_decisions_cannot_replace_each_other() -> None:
    entities = [ent("a", D, "Decision A", "s1"), ent("b", D, "Decision B", "s1")]
    result = clean(entities, [rel("a", K.SUPERSEDES, "b", "s1"), rel("b", K.SUPERSEDES, "a", "s1")])
    assert [(r.source, r.target) for r in result.relations] == [("a", "b")]


def test_confidence_is_clamped_and_duplicates_collapse() -> None:
    entities = [ent("d", D, "Decision", "s1"), ent("t", T, "Topic", "s1")]
    result = clean(
        entities,
        [rel("d", K.ABOUT, "t", "s1", confidence=7), rel("d", K.ABOUT, "t", "s2", confidence=0.1)],
    )
    assert len(result.relations) == 1 and result.relations[0].confidence == 1.0


def test_the_summary_is_trimmed_and_capped() -> None:
    raw = Extraction("  " + "s" * 5000 + "  ")
    assert len(validate_extraction(raw, SEGMENTS).summary) == 2000


def test_validation_never_raises_on_empty_input() -> None:
    assert clean() == Extraction("summary", (), ())
