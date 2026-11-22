import json

from nodo_api.adapters.azure.openai_extractor import (
    DOCUMENT_EXTRACTION_SCHEMA,
    DOCUMENT_RELATION_KINDS,
    EXTRACTION_SCHEMA,
    MODEL_RELATION_KINDS,
    parse_fragment_extraction,
)
from nodo_api.domain.graph import RelationKind, validate_extraction


def reply(entities, relations) -> str:
    return json.dumps({"summary": "", "entities": entities, "relations": relations})


def concept(key: str, kind: str = "topic") -> dict:
    return {"key": key, "type": kind, "label": key.title(), "evidence": [1], "due_date": None}


def child_of(source: str, target: str, evidence=None) -> dict:
    return {
        "source": source,
        "target": target,
        "kind": "CHILD_OF",
        "confidence": 0.8,
        "evidence": [1] if evidence is None else evidence,
    }


def test_document_schema_offers_child_of_and_the_meeting_schema_does_not():
    meeting_enum = EXTRACTION_SCHEMA["properties"]["relations"]["items"]["properties"]["kind"][
        "enum"
    ]
    document_enum = DOCUMENT_EXTRACTION_SCHEMA["properties"]["relations"]["items"]["properties"][
        "kind"
    ]["enum"]
    assert meeting_enum == MODEL_RELATION_KINDS
    assert "CHILD_OF" not in meeting_enum
    assert document_enum == DOCUMENT_RELATION_KINDS == [*MODEL_RELATION_KINDS, "CHILD_OF"]


def test_model_child_of_between_things_becomes_a_proposal_candidate():
    raw = parse_fragment_extraction(
        reply(
            [concept("child", "system"), concept("parent", "project")],
            [child_of("child", "parent")],
        ),
        ("f1",),
    )
    clean = validate_extraction(raw, frozenset({"f1"}))
    assert len(clean.relations) == 1
    assert clean.relations[0].kind is RelationKind.CHILD_OF
    assert clean.relations[0].evidence == ("f1",)


def test_child_of_naming_a_person_or_decision_is_dropped():
    raw = parse_fragment_extraction(
        reply(
            [concept("person", "person"), concept("decision", "decision"), concept("topic")],
            [child_of("person", "topic"), child_of("topic", "decision")],
        ),
        ("f1",),
    )
    assert validate_extraction(raw, frozenset({"f1"})).relations == ()


def test_child_of_citing_an_unshown_number_is_dropped():
    raw = parse_fragment_extraction(
        reply([concept("child"), concept("parent")], [child_of("child", "parent", [2, 99])]),
        ("f1",),
    )
    assert validate_extraction(raw, frozenset({"f1"})).relations == ()
