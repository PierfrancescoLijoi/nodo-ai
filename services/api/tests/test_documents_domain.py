from __future__ import annotations

import itertools

import pytest

from nodo_api.domain import MAX_FRAGMENT_CHARS
from nodo_api.domain.documents import (
    Block,
    ParsedDocument,
    ParseError,
    build_sections_and_fragments,
    fragment_id,
    split_text,
    stable_text,
)
from nodo_api.domain.sources import check_version, outline_order

SRC = "src-1"


def h(text: str, level: int = 1, **kw: int) -> Block:
    return Block("heading", text, heading_level=level, **kw)


def p(text: str, **kw: int) -> Block:
    return Block("paragraph", text, **kw)


def build(*blocks: Block, version: int = 1):
    return build_sections_and_fragments(ParsedDocument("test", tuple(blocks)), SRC, version)


def assert_covers(text: str, spans: tuple[tuple[int, int], ...]) -> None:
    """Spans tile the text: no gap, no overlap, nothing empty."""
    assert spans, "a non-empty text has at least one span"
    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    for (_, end), (start, _) in itertools.pairwise(spans):
        assert end == start
    assert all(s < e for s, e in spans)


# --- stable text -------------------------------------------------------------------------------


def test_stable_text_spans_slice_back_to_block_text():
    blocks = (h("Title"), p("first paragraph"), Block("list_item", "- item"), p("last"))
    text, spans = stable_text(blocks)
    assert text == "Title\n\nfirst paragraph\n\n- item\n\nlast"
    assert len(spans) == len(blocks)
    for block, (start, end) in zip(blocks, spans, strict=True):
        assert text[start:end] == block.text


def test_stable_text_of_no_blocks_is_empty():
    assert stable_text(()) == ("", ())


# --- sections ----------------------------------------------------------------------------------


def test_heading_stack_builds_nested_sections():
    sections, fragments = build(
        h("A", 1), p("a1"), h("A.1", 2), p("a11"), h("A.2", 2), p("a21"), h("B", 1), p("b1")
    )
    by_title = {s.title: s for s in sections}
    assert [s.title for s in sections] == ["A", "A.1", "A.2", "B"]
    assert [s.ordinal for s in sections] == [0, 1, 2, 3]
    assert by_title["A"].parent_id is None
    assert by_title["A.1"].parent_id == by_title["A"].id
    assert by_title["A.2"].parent_id == by_title["A"].id
    assert by_title["B"].parent_id is None
    assert [f.section_id for f in fragments] == [
        by_title["A"].id,
        by_title["A.1"].id,
        by_title["A.2"].id,
        by_title["B"].id,
    ]
    assert [f.ordinal for f in fragments] == [0, 1, 2, 3]
    assert [s.level for s in sections] == [1, 2, 2, 1]
    assert [s.id for s in outline_order(sections)] == [s.id for s in sections]


def test_level_jump_attaches_to_nearest_shallower_section():
    sections, _ = build(h("A", 1), p("x"), h("C", 3), p("y"), h("B", 2), p("z"), h("D", 4), p("w"))
    by_title = {s.title: s for s in sections}
    assert by_title["C"].parent_id == by_title["A"].id
    assert by_title["C"].level == 3
    assert by_title["B"].parent_id == by_title["A"].id
    assert by_title["D"].parent_id == by_title["B"].id


def test_blocks_before_first_heading_go_to_root_section():
    sections, fragments = build(p("preface"), h("Chapter", 1), p("body"))
    root = sections[0]
    assert (root.title, root.level, root.ordinal, root.parent_id) == ("Document", 1, 0, None)
    assert [s.title for s in sections] == ["Document", "Chapter"]
    assert sections[1].ordinal == 1
    assert sections[1].parent_id is None
    assert fragments[0].section_id == root.id
    assert fragments[1].section_id == sections[1].id


def test_deeper_heading_after_root_attaches_to_the_root():
    sections, _ = build(p("preface"), h("Sub", 2), p("x"))
    assert sections[1].parent_id == sections[0].id


def test_no_root_section_when_the_document_starts_with_a_heading():
    sections, _ = build(h("Only", 2), p("x"))
    assert [s.title for s in sections] == ["Only"]
    assert sections[0].parent_id is None


def test_empty_document_has_no_fragments_but_one_root_section():
    sections, fragments = build()
    assert fragments == ()
    assert len(sections) == 1
    assert (sections[0].title, sections[0].level, sections[0].ordinal) == ("Document", 1, 0)
    # a document with only headings has sections and still no fragments
    sections, fragments = build(h("A"), h("B", 2))
    assert [s.title for s in sections] == ["A", "B"]
    assert fragments == ()


def test_section_ids_are_stable_and_derived_from_the_path():
    a, _ = build(h("A"), h("B", 2), p("x"))
    b, _ = build(p("other"), h("A"), h("B", 2), p("y"), version=2)
    ids_a = {s.title: s.id for s in a}
    ids_b = {s.title: s.id for s in b}
    assert ids_a["A"] == ids_b["A"] and ids_a["B"] == ids_b["B"]
    assert ids_a["A"] != ids_a["B"]
    assert all(i.startswith("s_") and len(i) == 22 for i in ids_a.values())


def test_duplicate_heading_paths_get_distinct_section_ids():
    sections, fragments = build(
        h("Intro"), p("same text"), h("Sub", 2), p("s"), h("Intro"), p("same text"), h("Sub", 2)
    )
    ids = [s.id for s in sections]
    assert len(set(ids)) == len(ids) == 4
    # the first occurrence keeps the plain path hash: a later duplicate cannot move it
    plain, _ = build(h("Intro"), p("same text"))
    assert plain[0].id == sections[0].id
    # identical paragraphs under the two Intro sections do not collide either
    assert len({f.id for f in fragments}) == len(fragments)
    check_version(SRC, sections, fragments)


def test_a_heading_titled_like_the_root_does_not_collide_with_it():
    sections, fragments = build(p("preface"), h("Document"), p("body"))
    assert len({s.id for s in sections}) == 2
    check_version(SRC, sections, fragments)


# --- splitting ---------------------------------------------------------------------------------


def test_fragments_never_exceed_the_hard_limit():
    sentence = "This is a sentence of ordinary length. "
    big = (sentence * (45_000 // len(sentence) + 1))[:45_000]
    _, fragments = build(p(big))
    assert len(fragments) > 1
    assert all(0 < len(f.text) <= MAX_FRAGMENT_CHARS for f in fragments)
    assert all(len(f.text) <= 1100 + 275 for f in fragments)


def test_a_text_with_no_break_at_all_still_splits_under_the_limit():
    blob = "x" * 45_000
    spans = split_text(blob)
    assert_covers(blob, spans)
    assert max(e - s for s, e in spans) <= 1100
    spans = split_text(blob, target_chars=50_000)
    assert max(e - s for s, e in spans) <= MAX_FRAGMENT_CHARS


def test_split_never_exceeds_hard_max_even_when_target_is_larger():
    text = "word " * 400
    spans = split_text(text, target_chars=5000, hard_max=300)
    assert_covers(text, spans)
    assert max(e - s for s, e in spans) <= 300


def test_split_keeps_a_short_text_whole():
    assert split_text("short text") == ((0, 10),)
    assert split_text("") == ()
    text = "a" * 1300  # within the tail tolerance of the 1100 target: no sliver
    assert split_text(text) == ((0, 1300),)


def test_split_prefers_paragraph_then_sentence_boundaries():
    para1 = "Alpha beta gamma. " * 30  # about 540 chars
    para2 = "Delta epsilon zeta. " * 30  # about 600 chars
    para3 = "Eta theta iota. " * 30
    text = para1.strip() + "\n\n" + para2.strip() + "\n\n" + para3.strip()
    spans = split_text(text)
    assert_covers(text, spans)
    first_end = spans[0][1]
    assert text[:first_end].endswith("\n\n"), "the cut falls after a paragraph break"
    # one long paragraph: the cut falls after a sentence, never mid-sentence
    long_para = ("Sentence number %d is here. " * 1) * 1
    one = " ".join(long_para % i for i in range(200))
    spans = split_text(one)
    assert_covers(one, spans)
    for start, end in spans[:-1]:
        assert one[start:end].rstrip().endswith(".")
    # no sentence end: falls back to a line break, then to a space
    lines = "\n".join("word " * 10 for _ in range(100))
    spans = split_text(lines)
    assert_covers(lines, spans)
    assert all(lines[:e].endswith("\n") for _, e in spans[:-1])
    spaced = "word " * 1000
    spans = split_text(spaced)
    assert all(spaced[:e].endswith(" ") for _, e in spans[:-1])


def test_split_never_cuts_inside_a_surrogate_pair():
    pair = chr(0xD83D) + chr(0xDE00)
    text = pair * 200
    for target in (11, 12, 13, 101):
        spans = split_text(text, target_chars=target, hard_max=target)
        assert_covers(text, spans)
        for start, end in spans:
            assert not (0xDC00 <= ord(text[start]) <= 0xDFFF), "span starts on a low surrogate"
            assert not (0xD800 <= ord(text[end - 1]) <= 0xDBFF), "span ends on a high surrogate"
        assert all(e - s <= target for s, e in spans)


def test_split_rejects_nonsense_limits():
    with pytest.raises(ValueError):
        split_text("abc", target_chars=1)
    with pytest.raises(ValueError):
        split_text("abc", hard_max=1)


def _table(rows: int, header: str = "Name | Amount") -> str:
    return "\n".join([header] + [f"item number {i} | {i * 100}" for i in range(rows)])


def test_table_parts_repeat_the_header_row():
    table = _table(200)
    header = table.split("\n", 1)[0]
    sections, fragments = build(h("Sheet"), Block("table", table))
    assert len(fragments) > 2
    assert fragments[0].text.startswith(header + "\n")
    for fragment in fragments:
        assert fragment.text.startswith(header + "\n")
        assert len(fragment.text) <= 1100
    # every row appears exactly once as a body row
    body_rows: list[str] = []
    for fragment in fragments:
        body_rows.extend(fragment.text.split("\n")[1:])
    assert body_rows == table.split("\n")[1:]
    check_version(SRC, sections, fragments)


def test_table_part_locator_covers_its_own_rows_not_the_repeated_header():
    table = _table(200)
    header = table.split("\n", 1)[0]
    block = Block("table", table, page=3)
    text, spans = stable_text((h("T"), block))
    _, fragments = build(h("T"), block)
    start, _ = spans[1]
    for i, fragment in enumerate(fragments):
        assert fragment.locator.chars is not None
        a, z = fragment.locator.chars
        body = text[a:z]
        assert start <= a < z <= spans[1][1]
        if i == 0:
            assert fragment.text == body  # the first part is an exact slice
        else:
            assert fragment.text == header + "\n" + body  # later parts: header, then own rows
            assert not body.startswith(header + "\n")
    # row spans tile the table: no row is lost or covered twice
    covered = "\n".join(text[f.locator.chars[0] : f.locator.chars[1]] for f in fragments)
    assert covered == table


def test_table_rows_are_split_with_split_text_in_table_mode():
    table = _table(100)
    header_chars = table.index("\n")
    spans = split_text(table, header_chars=header_chars)
    assert_covers(table, spans)
    assert len(spans) > 1
    for _, end in spans[:-1]:
        assert table[:end].endswith("\n"), "a part ends after a whole row"
    first_start, first_end = spans[0]
    assert first_end - first_start <= 1100
    for start, end in spans[1:]:
        body = table[start:end].strip()
        assert len(body) + header_chars + 1 <= 1100  # room for the repeated header


def test_a_long_header_is_not_repeated_and_nothing_overflows():
    header = "H" * 800
    table = header + "\n" + "\n".join(f"row {i}" for i in range(600))
    _, fragments = build(Block("table", table))
    assert len(fragments) > 1
    assert all(len(f.text) <= 1100 for f in fragments)
    assert not fragments[1].text.startswith(header)
    assert [f.text for f in fragments] == [
        table[slice(*f.locator.chars)].strip() for f in fragments
    ]


def test_a_table_row_longer_than_the_budget_is_split_inside_the_row():
    table = "a | b\n" + "x" * 3000 + "\nlast | row"
    _, fragments = build(Block("table", table))
    assert all(len(f.text) <= 1100 for f in fragments)
    assert fragments[-1].text.endswith("last | row")
    assert sum(f.text.count("x") for f in fragments) == 3000


# --- locators and ids --------------------------------------------------------------------------


def test_chars_locator_slices_the_stable_text_back_to_the_fragment():
    blocks = (
        p("  leading and trailing spaces are not part of the fragment  "),
        h("Heading"),
        p("Short."),
        Block("code", "def f():\n    return 1"),
        p(("Long sentence here. " * 120).strip()),
        Block("notes", "speaker notes"),
        Block("list_item", "- one"),
    )
    text, _ = stable_text(blocks)
    _, fragments = build(*blocks)
    assert len(fragments) > 6
    for fragment in fragments:
        assert fragment.locator.chars is not None
        a, z = fragment.locator.chars
        assert text[a:z] == fragment.text
        assert fragment.text == fragment.text.strip()
    # non-table fragments appear in reading order without overlap
    ranges = [f.locator.chars for f in fragments]
    assert ranges == sorted(ranges)
    assert all(r1[1] <= r2[0] for r1, r2 in itertools.pairwise(ranges))


def test_blank_blocks_are_rejected_so_no_empty_fragment_exists():
    with pytest.raises(ValueError):
        Block("paragraph", "   ")
    with pytest.raises(ValueError):
        Block("paragraph", "")


def test_fragment_ids_are_stable_for_unchanged_text_and_differ_for_changed_text():
    v1, f1 = build(h("A"), p("keep me"), p("old text"))
    v2, f2 = build(h("A"), p("keep me"), p("new text"), p("added"), version=2)
    assert f1[0].id == f2[0].id
    assert f1[1].id != f2[1].id
    assert len({f.id for f in f1 + f2}) == 4
    assert all(f.id.startswith("f_") and len(f.id) == 22 for f in f1)
    assert f1[0].version == 1 and f2[0].version == 2
    assert fragment_id(("A",), "keep me") == f1[0].id
    assert fragment_id(("A",), "keep me") != fragment_id(("B",), "keep me")
    # the join is unambiguous: two path parts are not one part
    assert fragment_id(("a", "b"), "t") != fragment_id(("ab",), "t")
    assert fragment_id(("A",), "t", occurrence=1) != fragment_id(("A",), "t")
    assert len(v1) == len(v2) == 1


def test_identical_paragraphs_in_one_section_get_distinct_stable_ids():
    blocks = (h("A"), p("same"), p("same"), p("same"))
    _, first = build(*blocks)
    _, second = build(*blocks)
    assert len({f.id for f in first}) == 3
    assert [f.id for f in first] == [f.id for f in second]
    assert first[0].id == fragment_id(("A",), "same")


def test_page_and_slide_are_copied_into_the_locator():
    _, fragments = build(
        h("Slide 1", slide=1),
        p("on slide one", slide=1),
        h("Page text", page=7),
        p("on page seven", page=7),
        p("no position"),
    )
    assert (fragments[0].locator.slide, fragments[0].locator.page) == (1, None)
    assert (fragments[1].locator.page, fragments[1].locator.slide) == (7, None)
    assert (fragments[2].locator.page, fragments[2].locator.slide) == (None, None)
    assert all(f.locator.chars is not None for f in fragments)
    # long block: every part keeps the page
    _, parts = build(p("word " * 2000, page=2))
    assert len(parts) > 1 and all(f.locator.page == 2 for f in parts)


def test_built_versions_pass_check_version_and_fill_the_domain_fields():
    sections, fragments = build(h("A"), p("one"), h("B", 2), p("two"), version=3)
    check_version(SRC, sections, fragments)
    assert {f.source_id for f in fragments} == {SRC} == {s.source_id for s in sections}
    assert {f.version for f in fragments} == {3}
    assert all(f.speaker_id is None and f.confidence is None for f in fragments)


# --- value objects -----------------------------------------------------------------------------


def test_block_validation():
    with pytest.raises(ValueError):
        Block("heading", "Title")
    with pytest.raises(ValueError):
        Block("heading", "Title", heading_level=0)
    with pytest.raises(ValueError):
        Block("heading", "Title", heading_level=7)
    with pytest.raises(ValueError):
        Block("paragraph", "x", page=0)
    with pytest.raises(ValueError):
        Block("paragraph", "x", slide=0)
    Block("heading", "Title", heading_level=6)


def test_parse_error_carries_a_known_code_and_no_text():
    for code in ("encrypted", "damaged", "unsupported", "empty", "too_large"):
        assert ParseError(code).code == code
    assert ParseError("timeout").code == "timeout"
    assert str(ParseError("damaged")) == "damaged"
    with pytest.raises(ValueError):
        ParseError("surprise")


def test_parsed_document_defaults():
    doc = ParsedDocument("p", ())
    assert doc.no_text_layer is False and doc.warnings == ()
