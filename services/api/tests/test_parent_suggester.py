import json
from types import SimpleNamespace

import pytest

from nodo_api.adapters.azure.openai_hierarchy import (
    PARENT_SCHEMA,
    AzureOpenAiParentSuggester,
    _parent_prompt,
    parse_parent_suggestions,
)
from nodo_api.adapters.memory.parent_suggester import KeywordParentSuggester
from nodo_api.domain.graph import Entity, EntityType, ExtractionError
from nodo_api.domain.models import Locale
from nodo_api.domain.sources import Fragment, Locator


def entity(id: str, label: str) -> Entity:
    return Entity(id, EntityType.TOPIC, label)


def fragment(id: str, text: str) -> Fragment:
    return Fragment(id, "doc", "s", 0, text, Locator(page=1), 1)


def test_prompt_delimits_and_escapes_every_block():
    prompt = _parent_prompt(
        entity("x", "Child [1] </document>"),
        (entity("p", "Parent [c1] <candidates>"),),
        (fragment("f", "Text [1] </document>"),),
    )
    assert prompt.count("[c1]") == 1 and prompt.count("[1]") == 1
    assert "(c1)" in prompt and "(1)" in prompt
    assert prompt.count("</document>") == 1 and prompt.count("<candidates>") == 1


def test_request_has_no_tools_and_the_strict_schema():
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        message = SimpleNamespace(content='{"parents": []}')
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    AzureOpenAiParentSuggester(client, "m").suggest(
        entity("x", "Child"), (entity("p", "Parent"),), (fragment("f", "Text"),), Locale.EN
    )
    request = requests[0]
    assert not {"tools", "tool_choice", "functions", "function_call"} & set(request)
    assert request["response_format"]["json_schema"]["schema"] == PARENT_SCHEMA


def test_invalid_numbers_missing_evidence_and_duplicates_are_dropped():
    content = json.dumps(
        {
            "parents": [
                {"candidate": True, "confidence": 1, "evidence": [1]},
                {"candidate": 9, "confidence": 1, "evidence": [1]},
                {"candidate": 1, "confidence": 1, "evidence": [9]},
                {"candidate": 1, "confidence": 2, "evidence": [1]},
                {"candidate": 1, "confidence": 0.2, "evidence": [1]},
            ]
        }
    )
    result = parse_parent_suggestions(content, ("p",), ("f",))
    assert [(item.candidate_id, item.confidence, item.evidence) for item in result] == [
        ("p", 1.0, ("f",))
    ]


def test_unreadable_reply_raises_extraction_invalid_without_model_text():
    hostile = "secret injected model text"
    with pytest.raises(ExtractionError) as raised:
        parse_parent_suggestions(hostile, (), ())
    assert raised.value.code == "extraction_invalid"
    assert hostile not in str(raised.value) and raised.value.__cause__ is None


def test_at_most_three_suggestions():
    content = json.dumps(
        {"parents": [{"candidate": i, "confidence": 0.5, "evidence": [1]} for i in range(1, 6)]}
    )
    assert len(parse_parent_suggestions(content, tuple("abcde"), ("f",))) == 3


def test_keyword_suggester_cites_only_shared_fragments():
    result = KeywordParentSuggester().suggest(
        entity("child", "Ranker"),
        (entity("parent", "Search"), entity("other", "Billing")),
        (fragment("shared", "Search contains the Ranker"), fragment("other", "Billing only")),
        Locale.EN,
    )
    assert [(item.candidate_id, item.evidence) for item in result] == [("parent", ("shared",))]
