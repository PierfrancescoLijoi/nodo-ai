"""Document extraction keeps every entity the model returns, even when it reuses a key or writes a
role where the name belongs. Meeting extraction is unchanged."""

from __future__ import annotations

from nodo_api.domain.graph import (
    EXISTING_PREFIX,
    EntityType,
    ExtractedEntity,
    ExtractedRelation,
    Extraction,
    RelationKind,
    validate_extraction,
)
from nodo_api.domain.models import Locale
from nodo_api.domain.sources import Fragment, Locator
from nodo_api.services.fragment_extraction import ChunkedFragmentExtractor, repair_keys


def _fragment(fid: str, text: str, ordinal: int = 0) -> Fragment:
    return Fragment(fid, "src", None, ordinal, text, Locator(chars=(0, len(text))), 1)


class _Fake:
    """Answers each window with the extraction built for its fragments."""

    def __init__(self, answer) -> None:
        self._answer = answer

    def extract(self, fragments, section_path, locale, known_decisions=()):
        return self._answer(fragments)


def _run(fragments, answer, max_tokens: int = 2000) -> Extraction:
    extractor = ChunkedFragmentExtractor(_Fake(answer), max_tokens)
    result = extractor.extract_document((), fragments, Locale.EN)
    return validate_extraction(result.extraction, frozenset(f.id for f in fragments))


def _person(key: str, label: str, fid: str = "f1") -> ExtractedEntity:
    return ExtractedEntity(key, EntityType.PERSON, label, (fid,))


def test_two_entities_sharing_a_key_are_both_kept() -> None:
    fragments = (_fragment("f1", "Ada Brook and Ola Senn review the budget."),)
    answer = lambda frs: Extraction(  # noqa: E731
        "", (_person("person", "Ada Brook"), _person("person", "Ola Senn"))
    )
    clean = _run(fragments, answer)
    assert sorted(e.label for e in clean.entities) == ["Ada Brook", "Ola Senn"]


def test_a_relation_on_a_duplicated_key_is_dropped_and_others_kept() -> None:
    fragments = (_fragment("f1", "Ada Brook and Ola Senn decide to use a queue."),)
    raw = Extraction(
        "",
        (
            _person("person", "Ada Brook"),
            _person("person", "Ola Senn"),
            ExtractedEntity("d1", EntityType.DECISION, "Use a queue", ("f1",)),
            ExtractedEntity("t1", EntityType.TOPIC, "Queue", ("f1",)),
        ),
        (
            ExtractedRelation("d1", "person", RelationKind.PROPOSED_BY, 0.8, ("f1",)),
            ExtractedRelation("d1", "t1", RelationKind.ABOUT, 0.8, ("f1",)),
        ),
    )
    repaired = repair_keys(raw, fragments)
    assert [e.key for e in repaired.entities] == ["person", "person#2", "d1", "t1"]
    assert [(r.source, r.target) for r in repaired.relations] == [("d1", "t1")]


def test_a_new_key_skips_keys_that_already_exist() -> None:
    fragments = (_fragment("f1", "Ada Brook, Ola Senn and Ian Moor meet."),)
    raw = Extraction(
        "",
        (
            _person("p", "Ada Brook"),
            _person("p#2", "Ola Senn"),
            _person("p", "Ian Moor"),
        ),
    )
    assert [e.key for e in repair_keys(raw, fragments).entities] == ["p", "p#2", "p#3"]


def test_role_label_with_the_name_as_key_takes_the_name() -> None:
    fragments = (_fragment("f1", "Priya Halvorsen will publish the schedule."),)
    raw = Extraction("", (_person("Priya  Halvorsen", "Project lead"),))
    (entity,) = repair_keys(raw, fragments).entities
    assert entity.label == "Priya Halvorsen"
    assert entity.key == "Priya  Halvorsen"


def test_label_found_in_the_text_is_never_replaced() -> None:
    fragments = (_fragment("f1", "The project lead, Priya Halvorsen, signs off."),)
    raw = Extraction("", (_person("Priya Halvorsen", "Project lead"),))
    assert repair_keys(raw, fragments).entities[0].label == "Project lead"


def test_a_key_inside_a_longer_word_is_not_taken_as_the_name() -> None:
    fragments = (_fragment("f1", "Ann owns the budget; see table1 in phase1."),)
    raw = Extraction("", (_person("e1", "Project lead"),))
    assert repair_keys(raw, fragments).entities[0].label == "Project lead"


def test_a_placeholder_key_in_the_text_is_not_taken_as_the_name() -> None:
    fragments = (_fragment("f1", "Each person signs; e1 is the first form."),)
    raw = Extraction("", (_person("person", "Project lead"), _person("e1", "Team lead")))
    assert [e.label for e in repair_keys(raw, fragments).entities] == [
        "Project lead",
        "Team lead",
    ]


def test_an_exact_repeat_of_an_entity_is_not_a_duplicate_key() -> None:
    fragments = (_fragment("f1", "Ada Brook decides to use a queue."),)
    raw = Extraction(
        "",
        (
            _person("p1", "Ada Brook"),
            ExtractedEntity("d1", EntityType.DECISION, "Use a queue", ("f1",)),
            _person("p1", "ada  brook"),
        ),
        (ExtractedRelation("d1", "p1", RelationKind.PROPOSED_BY, 0.8, ("f1",)),),
    )
    repaired = repair_keys(raw, fragments)
    assert [e.key for e in repaired.entities] == ["p1", "d1", "p1"]
    assert [(r.source, r.target) for r in repaired.relations] == [("d1", "p1")]
    clean = validate_extraction(repaired, frozenset({"f1"}))
    assert [e.label for e in clean.entities] == ["Ada Brook", "Use a queue"]
    assert len(clean.relations) == 1


def test_only_persons_are_relabelled() -> None:
    fragments = (_fragment("f1", "Billing moves to the new ledger."),)
    raw = Extraction("", (ExtractedEntity("Billing", EntityType.TOPIC, "Payments", ("f1",)),))
    assert repair_keys(raw, fragments).entities[0].label == "Payments"


def test_existing_keys_are_untouched() -> None:
    fragments = (_fragment("f1", "Ada Brook and Ola Senn replace the old rule."),)
    old = f"{EXISTING_PREFIX}d0"
    raw = Extraction(
        "",
        (
            _person("person", "Ada Brook"),
            _person("person", "Ola Senn"),
            ExtractedEntity("d1", EntityType.DECISION, "Use the new rule", ("f1",)),
        ),
        (ExtractedRelation("d1", old, RelationKind.SUPERSEDES, 0.8, ("f1",)),),
    )
    repaired = repair_keys(raw, fragments)
    assert [(r.source, r.target) for r in repaired.relations] == [("d1", old)]


def test_keys_stay_unique_across_two_windows_after_merge() -> None:
    names = {"f1": ("Ada Brook", "Ola Senn"), "f2": ("Ian Moor", "Eva Lund")}
    fragments = (
        _fragment("f1", "Ada Brook and Ola Senn plan. " + "x" * 300, 0),
        _fragment("f2", "Ian Moor and Eva Lund build. " + "y" * 300, 1),
    )

    def answer(frs):
        return Extraction(
            "",
            tuple(_person("person", name, f.id) for f in frs for name in names[f.id]),
        )

    clean = _run(fragments, answer, max_tokens=120)
    assert sorted(e.label for e in clean.entities) == [
        "Ada Brook",
        "Eva Lund",
        "Ian Moor",
        "Ola Senn",
    ]


def test_meeting_validation_is_unchanged() -> None:
    raw = Extraction(
        "", (_person("person", "Ada Brook", "s1"), _person("person", "Ola Senn", "s1"))
    )
    clean = validate_extraction(raw, frozenset({"s1"}))
    assert [e.label for e in clean.entities] == ["Ada Brook"]
