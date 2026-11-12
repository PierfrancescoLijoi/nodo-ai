"""Document domain: parsed blocks in, sections and fragments out (ADR-029, ADR-032).

A parser (see the document adapters) turns a file into an ordered list of `Block`s. This module is
pure: it joins the blocks into one "stable text", cuts every block into fragments that are small
enough to cite, builds the section tree from the headings, and gives every section and fragment a
deterministic id so that an unchanged part of a document keeps its id when a new version is
imported.

Locator contract. `stable_text` joins the blocks with a blank line and reports the exact
`[start, end)` of each block. A fragment's `Locator.chars` is a range of that text:

- for every fragment that is not a later part of a table, `stable_text[a:z] == fragment.text`;
- for a table (one block, rows separated by one newline, the first row is the header) every part
  after the first is stored as the header row, a newline, then the part's own rows, so that a
  fragment read on its own still says what its columns are. For those parts `chars` covers the
  part's own rows only (never the repeated header), so
  `fragment.text == header + "\\n" + stable_text[a:z]`; the first part is an exact slice like any
  other fragment. A header longer than half the target size is not repeated: those parts are
  exact slices too.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from nodo_api.domain.sources import MAX_FRAGMENT_CHARS, Fragment, Locator, Section

BlockKind = Literal["heading", "paragraph", "list_item", "table", "code", "notes"]

PARSE_ERROR_CODES = frozenset(
    {"encrypted", "damaged", "unsupported", "empty", "too_large", "no_text_layer", "timeout"}
)

TARGET_FRAGMENT_CHARS = 1100
ROOT_SECTION_TITLE = "Document"

_BLOCK_SEPARATOR = "\n\n"
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*\s+")


@dataclass(frozen=True, slots=True)
class Block:
    kind: BlockKind
    text: str
    heading_level: int | None = None
    page: int | None = None
    slide: int | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("A block needs text")
        if self.kind == "heading" and (
            self.heading_level is None or not 1 <= self.heading_level <= 6
        ):
            raise ValueError("A heading block needs a heading_level from 1 to 6")
        if self.page is not None and self.page < 1:
            raise ValueError("page starts at 1")
        if self.slide is not None and self.slide < 1:
            raise ValueError("slide starts at 1")


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    parser: str
    blocks: tuple[Block, ...]
    no_text_layer: bool = False
    warnings: tuple[str, ...] = ()


class ParseError(Exception):
    """A file that cannot be turned into blocks. The message is the code: it never carries text
    from the document."""

    def __init__(self, code: str) -> None:
        if code not in PARSE_ERROR_CODES:
            raise ValueError(f"unknown parse error code: {code}")
        super().__init__(code)
        self.code = code


def stable_text(blocks: tuple[Block, ...]) -> tuple[str, tuple[tuple[int, int], ...]]:
    """The blocks joined by a blank line, and the exact `[start, end)` of each block in it."""
    spans: list[tuple[int, int]] = []
    offset = 0
    for block in blocks:
        spans.append((offset, offset + len(block.text)))
        offset += len(block.text) + len(_BLOCK_SEPARATOR)
    return _BLOCK_SEPARATOR.join(b.text for b in blocks), tuple(spans)


def _cut(text: str, start: int, limit: int) -> int:
    """End of the next piece: at most `limit` characters after `start`, at the best boundary.

    Preference: paragraph break, sentence end, line break, space, then the limit itself. A boundary
    in the first quarter of the window is ignored so that a piece is never a sliver. The separator
    stays at the end of the piece it follows. A character cut never splits a surrogate pair."""
    window_end = min(len(text), start + limit)
    floor = start + max(1, limit // 4)
    i = text.rfind("\n\n", floor, window_end)
    if i != -1:
        return i + 2
    ends = [m.end() for m in _SENTENCE_END.finditer(text, floor, window_end)]
    if ends:
        return ends[-1]
    for separator in ("\n", " "):
        i = text.rfind(separator, floor, window_end)
        if i != -1:
            return i + 1
    if (
        window_end < len(text)
        and window_end - 1 > start
        and 0xD800 <= ord(text[window_end - 1]) <= 0xDBFF
        and 0xDC00 <= ord(text[window_end]) <= 0xDFFF
    ):
        return window_end - 1
    return window_end


def _row_atoms(text: str, limit: int) -> list[tuple[int, int]]:
    """Rows as spans that include their newline; a row longer than `limit` is cut inside."""
    atoms: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        newline = text.find("\n", start)
        end = len(text) if newline == -1 else newline + 1
        content_end = end - 1 if text[end - 1] == "\n" else end
        piece = start
        while content_end - piece > limit:
            cut = _cut(text, piece, limit)
            atoms.append((piece, cut))
            piece = cut
        atoms.append((piece, end))
        start = end
    return atoms


def _split_rows(text: str, target: int, header_chars: int) -> tuple[tuple[int, int], ...]:
    if header_chars > len(text) or (header_chars < len(text) and text[header_chars] != "\n"):
        raise ValueError("header_chars must end at the first row break of the text")
    repeat = _repeats_header(header_chars, target)
    rest_budget = target - header_chars - 1 if repeat else target
    parts: list[tuple[int, int]] = []
    budget = target
    current: tuple[int, int] | None = None
    for start, end in _row_atoms(text, rest_budget):
        if current is not None:
            content_end = end - 1 if text[end - 1] == "\n" else end
            if content_end - current[0] <= budget:
                current = (current[0], end)
                continue
            parts.append(current)
            budget = rest_budget
        current = (start, end)
    if current is not None:
        parts.append(current)
    return tuple(parts)


def _repeats_header(header_chars: int, target: int) -> bool:
    return header_chars > 0 and header_chars + 1 <= target // 2


def split_text(
    text: str,
    target_chars: int = TARGET_FRAGMENT_CHARS,
    hard_max: int = MAX_FRAGMENT_CHARS,
    *,
    header_chars: int | None = None,
) -> tuple[tuple[int, int], ...]:
    """Cut `text` into spans of about `target_chars`, never more than `hard_max`.

    The spans tile the text: no gap, no overlap, so the whitespace between pieces stays at the end
    of the span before it and the caller strips it. A cut falls after a paragraph break, else after
    a sentence, else after a line break, else after a space, else at the limit (never inside a
    surrogate pair). A tail of up to a quarter more than the target stays in the last span instead
    of becoming a sliver.

    Table mode (`header_chars` given: the length of the first row, the header): the text is cut by
    rows, a row longer than the budget is cut inside. The spans still tile the text. A span after
    the first has a budget reduced by the header and a newline, so that the caller can put the
    header in front of it and still stay under the target; a header longer than half the target is
    not repeated (see `_repeats_header`) and the budget is not reduced."""
    if target_chars < 2 or hard_max < 2:
        raise ValueError("target_chars and hard_max must be at least 2")
    if header_chars is not None and header_chars < 0:
        raise ValueError("header_chars cannot be negative")
    target = min(target_chars, hard_max)
    if header_chars is not None:
        return _split_rows(text, target, header_chars)
    spans: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        if len(text) - start <= min(target + target // 4, hard_max):
            end = len(text)
        else:
            end = _cut(text, start, target)
        spans.append((start, end))
        start = end
    return tuple(spans)


def fragment_id(section_path: tuple[str, ...], text: str, occurrence: int = 0) -> str:
    """Deterministic id: an unchanged fragment under an unchanged heading path keeps its id.

    `occurrence` is 0 for the first fragment with this path and text in a document; the builder
    passes 1, 2, ... for identical repeats, which would otherwise share an id."""
    key = "\x00".join(section_path) + "\x00" + text
    if occurrence:
        key += f"\x00{occurrence}"
    return "f_" + hashlib.sha1(key.encode("utf-8", "surrogatepass")).hexdigest()[:20]


def _section_id(path: tuple[str, ...], occurrence: int) -> str:
    key = "\x00".join(path)
    if occurrence:
        key += f"\x00{occurrence}"
    return "s_" + hashlib.sha1(key.encode("utf-8", "surrogatepass")).hexdigest()[:20]


def _strip_span(text: str, start: int, end: int) -> tuple[int, int]:
    piece = text[start:end]
    lead = len(piece) - len(piece.lstrip())
    return start + lead, end - (len(piece) - len(piece.rstrip()))


def build_sections_and_fragments(
    doc: ParsedDocument, source_id: str, version: int
) -> tuple[tuple[Section, ...], tuple[Fragment, ...]]:
    """Sections from the headings, fragments from every other block.

    - A heading closes every open section of the same or a deeper level and opens a section under
      the nearest shallower one, so a jump (level 1 then level 3) attaches to the level 1 section.
    - Blocks before the first heading go to a root section titled "Document" (level 1); it is also
      the only section of a document with no blocks. A heading deeper than 1 that follows it hangs
      under it; a level 1 heading is its sibling.
    - Section ordinals are dense from 0 in reading order, as are fragment ordinals.
    - Section id: `s_` + sha1 of the title path. When the same path appears again in the document
      (two "Introduction" headings under one parent) the n-th repeat adds `n` to the hash input,
      so ids are distinct while the first occurrence keeps the plain path hash.
    - Fragment id: see `fragment_id`; the occurrence index counts identical text under an identical
      path anywhere in the document, so ids stay distinct and stable for unchanged text."""
    text, spans = stable_text(doc.blocks)
    sections: list[Section] = []
    fragments: list[Fragment] = []
    stack: list[tuple[Section, tuple[str, ...]]] = []
    paths_seen: Counter[tuple[str, ...]] = Counter()
    fragments_seen: Counter[tuple[tuple[str, ...], str]] = Counter()

    def open_section(title: str, level: int) -> None:
        while stack and stack[-1][0].level >= level:
            stack.pop()
        parent, parent_path = stack[-1] if stack else (None, ())
        path = (*parent_path, title)
        section = Section(
            id=_section_id(path, paths_seen[path]),
            source_id=source_id,
            parent_id=parent.id if parent else None,
            ordinal=len(sections),
            level=level,
            title=title,
        )
        paths_seen[path] += 1
        sections.append(section)
        stack.append((section, path))

    def add_fragment(block: Block, body: str, chars: tuple[int, int]) -> None:
        section, path = stack[-1]
        occurrence = fragments_seen[(path, body)]
        fragments_seen[(path, body)] += 1
        fragments.append(
            Fragment(
                id=fragment_id(path, body, occurrence),
                source_id=source_id,
                section_id=section.id,
                ordinal=len(fragments),
                text=body,
                locator=Locator(page=block.page, slide=block.slide, chars=chars),
                version=version,
            )
        )

    for block, (block_start, _) in zip(doc.blocks, spans, strict=True):
        if block.kind == "heading":
            assert block.heading_level is not None
            open_section(block.text.strip(), block.heading_level)
            continue
        if not stack:
            open_section(ROOT_SECTION_TITLE, 1)
        header = ""
        if block.kind == "table":
            header_chars = block.text.find("\n")
            header_chars = len(block.text) if header_chars == -1 else header_chars
            row_spans = split_text(block.text, header_chars=header_chars)
            if _repeats_header(header_chars, TARGET_FRAGMENT_CHARS):
                header = block.text[:header_chars].strip()
        else:
            row_spans = split_text(block.text)
        for index, (a, z) in enumerate(row_spans):
            a, z = _strip_span(text, block_start + a, block_start + z)
            if a == z:
                continue
            body = text[a:z]
            if header and index > 0:
                body = header + "\n" + body
            add_fragment(block, body, (a, z))

    if not sections:
        open_section(ROOT_SECTION_TITLE, 1)
    return tuple(sections), tuple(fragments)
