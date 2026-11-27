from nodo_api.domain.sources import Fragment, Locator, SectionSummary
from nodo_api.services.context import expand_context


def _fragment(id: str, ordinal: int, text: str, section: str = "s") -> Fragment:
    return Fragment(
        id,
        "source",
        section,
        ordinal,
        text,
        Locator(chars=(ordinal * 10, ordinal * 10 + len(text))),
        1,
    )


def test_context_preserves_whole_match_then_summary_then_siblings():
    matched, sibling = _fragment("match", 1, "one two"), _fragment("next", 2, "three four")
    summary = SectionSummary("source", "s", 1, "model", "prompt", "summary words", 2)

    context = expand_context((matched,), (sibling, matched), (summary,), 6)

    assert [fragment.id for fragment in context.fragments] == ["match", "next"]
    assert context.summaries == (summary,)
    assert context.token_count == 6


def test_context_never_truncates_an_oversized_quote():
    oversized = _fragment("match", 0, "one two three")
    context = expand_context((oversized,), (oversized,), (), 2)
    assert context.fragments == () and context.token_count == 0
