"""One parser per document format: a file in, a `ParsedDocument` (ordered blocks) out (ADR-032).

Rules shared by every parser:

- The third-party libraries are imported inside each function, so importing this module (or the
  engine that imports it) never loads them; they are an optional extra.
- `ParsedDocument.parser` is `"<library> <version>"` from `importlib.metadata`, stored with each
  Source version because a library upgrade can change the text.
- Text is normalised: NUL and control characters are removed (tab and newline stay), runs of blank
  lines collapse to one, and a block that ends up empty is never produced.
- Failures are `ParseError` with a stable code and never carry text from the document: an
  encrypted PDF is `encrypted`, a truncated or corrupt container is `damaged` (a `.docx` that is
  not a zip is never read as text), and a file with no text at all is `empty`.
- Tables are one `table` block: rows joined by a newline, cells by a tab, the header row first.
- Parsers run in a subprocess (see the document worker); they do not enforce size limits.
"""

from __future__ import annotations

import platform
import re
import statistics
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Any, NamedTuple

from nodo_api.domain.documents import Block, BlockKind, ParsedDocument, ParseError

MAX_SHEET_ROWS = 20_000
XLSX_BLOCK_ROWS = 50
MIN_PAGE_CHARS = 10
MAX_PDF_HEADING_LEVELS = 6
_MAX_HEADING_LINE_CHARS = 120
_HEADING_SIZE_RATIO = 1.15

# --- text normalisation ------------------------------------------------------------------------

# Control characters go; vertical tab and form feed are line breaks; so are lone surrogates (they
# cannot be stored as UTF-8). Tab and newline stay.
_STRIP: dict[int, str | None] = {
    code: None
    for code in (*range(0x20), 0x7F, *range(0x80, 0xA0), *range(0xD800, 0xE000))
    if code not in (0x09, 0x0A)
}
_STRIP[0x0B] = "\n"
_STRIP[0x0C] = "\n"
_BLANK_RUNS = re.compile(r"\n(?:[ \t]*\n){2,}")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(_STRIP)
    return _BLANK_RUNS.sub("\n\n", text).strip()


def _cell(value: object) -> str:
    return " ".join(_clean(str(value)).split())


def _table(rows: list[list[str]]) -> str:
    """Rows of cells as table text; rows with no text at all are dropped."""
    return "\n".join("\t".join(cells) for cells in rows if any(cells))


class _Blocks:
    """Collects blocks and drops the ones that are empty after normalisation."""

    def __init__(self) -> None:
        self.items: list[Block] = []

    def add(
        self,
        kind: BlockKind,
        text: str,
        *,
        level: int | None = None,
        page: int | None = None,
        slide: int | None = None,
    ) -> None:
        text = _clean(text)
        if kind == "heading":
            text = " ".join(text.split())
        if text:
            self.items.append(Block(kind, text, heading_level=level, page=page, slide=slide))


def _parser_id(distribution: str) -> str:
    return f"{distribution} {version(distribution)}"


def _finish(parser: str, out: _Blocks, warnings: list[str] | None = None) -> ParsedDocument:
    if not out.items:
        raise ParseError("empty")
    return ParsedDocument(parser, tuple(out.items), warnings=tuple(warnings or ()))


@contextmanager
def _damaged_on_error() -> Iterator[None]:
    """Any failure while a library reads the file means the container is unusable.

    Call it after the imports: a missing library must stay an ImportError."""
    try:
        yield
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError("damaged") from exc


def _decode(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


# --- PDF ---------------------------------------------------------------------------------------


class _Line(NamedTuple):
    text: str
    size: float
    new_box: bool  # first line of a pdfminer text box: a paragraph starts here


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _pdfium_gate(path: Path) -> tuple[list[int], dict[int, list[tuple[int, str]]]]:
    """Characters per page and the outline (page index to [(level, title)]), with pypdfium2.

    pypdfium2 is the fast gate that tells which pages have a text layer (0.1 s per 50 pages)."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_raw

    try:
        pdf = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        encrypted = exc.err_code == pdfium_raw.FPDF_ERR_PASSWORD
        raise ParseError("encrypted" if encrypted else "damaged") from exc
    try:
        counts: list[int] = []
        for index in range(len(pdf)):
            page = pdf[index]
            textpage = page.get_textpage()
            counts.append(len("".join(textpage.get_text_range().split())))
            textpage.close()
            page.close()
        outline: dict[int, list[tuple[int, str]]] = {}
        for item in pdf.get_toc():
            dest = item.get_dest()
            index = dest.get_index() if dest is not None else None
            title = " ".join((item.get_title() or "").split())
            if title and index is not None and 0 <= index < len(counts):
                outline.setdefault(index, []).append((min(item.level + 1, 6), title))
    except pdfium.PdfiumError as exc:
        raise ParseError("damaged") from exc
    finally:
        pdf.close()
    return counts, outline


def _pdfminer_lines(path: Path, pages: list[int]) -> dict[int, list[_Line]]:
    """Text lines with their median font size, for the given 0-based pages only."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams, LTChar, LTTextBox

    result: dict[int, list[_Line]] = {}
    with _damaged_on_error():
        layouts = extract_pages(str(path), page_numbers=set(pages), laparams=LAParams())
        for index, layout in zip(pages, layouts, strict=False):
            lines: list[_Line] = []
            for box in layout:
                if not isinstance(box, LTTextBox):
                    continue
                first = True
                for line in box:
                    text = _clean(line.get_text())
                    if not text:
                        continue
                    sizes = [c.size for c in line if isinstance(c, LTChar)]
                    lines.append(_Line(text, statistics.median(sizes) if sizes else 0.0, first))
                    first = False
            result[index] = lines
    return result


def _size_levels(pages: dict[int, list[_Line]]) -> dict[float, int]:
    """Heading level per font size: sizes clearly above the body size, largest first, at most 6.

    The body size is the one that carries most characters. ponytail: a document that sets headings
    in the body size gets no headings; the outline is the better source and is preferred."""
    weight: Counter[float] = Counter()
    for lines in pages.values():
        for line in lines:
            weight[round(line.size, 1)] += len(line.text)
    if not weight:
        return {}
    body = weight.most_common(1)[0][0]
    sizes = {
        round(line.size, 1)
        for lines in pages.values()
        for line in lines
        if len(line.text) <= _MAX_HEADING_LINE_CHARS
        and round(line.size, 1) >= body * _HEADING_SIZE_RATIO
    }
    ranked = sorted(sizes, reverse=True)[:MAX_PDF_HEADING_LEVELS]
    return {size: rank + 1 for rank, size in enumerate(ranked)}


def _title_line_count(lines: list[_Line], start: int, title: str) -> int:
    """How many lines from `start` spell the outline title (0 when they do not)."""
    wanted = _norm(title)
    spelled = ""
    for offset in range(start, len(lines)):
        spelled = f"{spelled} {_norm(lines[offset].text)}".strip()
        if spelled == wanted:
            return offset - start + 1
        if not wanted.startswith(spelled + " "):
            return 0
    return 0


def _outline_marks(
    lines: list[_Line], entries: list[tuple[int, str]]
) -> dict[int, list[tuple[int, str, int]]]:
    """Where each outline entry of a page sits: line index to [(level, title, lines spelling it)].

    A title that is not found on the page is placed right after the previous one (0 lines), so its
    section still exists and the page text lands under it."""
    marks: dict[int, list[tuple[int, str, int]]] = {}
    cursor = 0
    for level, title in entries:
        for start in range(cursor, len(lines)):
            count = _title_line_count(lines, start, title)
            if count:
                marks.setdefault(start, []).append((level, title, count))
                cursor = start + count
                break
        else:
            marks.setdefault(cursor, []).append((level, title, 0))
    return marks


def _emit_pdf_page(
    out: _Blocks,
    page: int,
    lines: list[_Line],
    marks: dict[int, list[tuple[int, str, int]]],
    levels: dict[float, int],
) -> None:
    paragraph: list[str] = []
    heading: list[str] = []
    heading_level = 0

    def flush_paragraph() -> None:
        if paragraph:
            out.add("paragraph", " ".join(paragraph), page=page)
            paragraph.clear()

    def flush_heading() -> None:
        if heading:
            out.add("heading", " ".join(heading), level=heading_level, page=page)
            heading.clear()

    def emit_marks(index: int) -> int:
        skip = 0
        for level, title, count in marks.get(index, ()):
            flush_heading()
            flush_paragraph()
            out.add("heading", title, level=level, page=page)
            skip = max(skip, count)
        return skip

    index = 0
    while index < len(lines):
        skip = emit_marks(index)
        if skip:
            index += skip
            continue
        line = lines[index]
        level = (
            levels.get(round(line.size, 1)) if len(line.text) <= _MAX_HEADING_LINE_CHARS else None
        )
        if level:
            flush_paragraph()
            if heading and (heading_level != level or line.new_box):
                flush_heading()
            heading.append(line.text)
            heading_level = level
        else:
            flush_heading()
            if line.new_box:
                flush_paragraph()
            paragraph.append(line.text)
        index += 1
    emit_marks(len(lines))
    flush_heading()
    flush_paragraph()


def parse_pdf(path: Path) -> ParsedDocument:
    parser = _parser_id("pdfminer.six")
    counts, outline = _pdfium_gate(path)
    if not counts:
        raise ParseError("empty")
    text_pages = [i for i, chars in enumerate(counts) if chars >= MIN_PAGE_CHARS]
    if not text_pages:
        return ParsedDocument(parser, (), no_text_layer=True)
    blank = [str(i + 1) for i, chars in enumerate(counts) if chars < MIN_PAGE_CHARS]
    warnings = [f"pages_without_text:{','.join(blank)}"] if blank else []

    pages = _pdfminer_lines(path, text_pages)
    # An outline is authoritative; font sizes are the fallback when the file has none.
    levels = {} if outline else _size_levels(pages)
    out = _Blocks()
    for index in text_pages:
        lines = pages.get(index, [])
        marks = _outline_marks(lines, outline.get(index, []))
        _emit_pdf_page(out, index + 1, lines, marks, levels)
    return _finish(parser, out, warnings)


# --- DOCX --------------------------------------------------------------------------------------


def _docx_children(parent: Any) -> Iterator[Any]:
    """Body elements in order, looking inside content controls (w:sdt)."""
    from docx.oxml.ns import qn

    for child in parent.iterchildren():
        if child.tag == qn("w:sdt"):
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                yield from _docx_children(content)
        else:
            yield child


def _docx_paragraph_parts(element: Any, *, rendered: bool) -> tuple[list[tuple[str, int]], int]:
    """Return paragraph text split at the page markers Word actually uses.

    Each part carries its page offset from the start of the paragraph. Word can place a page break
    between two runs in the same paragraph, so assigning one page to ``Paragraph.text`` loses the
    locator for everything after that marker.
    """
    from docx.oxml.ns import qn

    parts: list[tuple[str, int]] = []
    text: list[str] = []
    page_offset = 0
    text_tag = qn("w:t")
    tab_tag = qn("w:tab")
    break_tag = qn("w:br")
    carriage_return_tag = qn("w:cr")
    rendered_break_tag = qn("w:lastRenderedPageBreak")
    break_type = qn("w:type")

    for node in element.iter():
        is_page_break = (
            node.tag == rendered_break_tag
            if rendered
            else (node.tag == break_tag and node.get(break_type) == "page")
        )
        if is_page_break:
            parts.append(("".join(text), page_offset))
            text.clear()
            page_offset += 1
        elif node.tag == text_tag:
            text.append(node.text or "")
        elif node.tag == tab_tag:
            text.append("\t")
        elif node.tag in (break_tag, carriage_return_tag):
            text.append("\n")
    parts.append(("".join(text), page_offset))
    return parts, page_offset


def parse_docx(path: Path) -> ParsedDocument:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    parser = _parser_id("python-docx")
    out = _Blocks()
    with _damaged_on_error():
        document = Document(str(path))
        body = document.element.body
        # Word records where it broke pages when it last saved (lastRenderedPageBreak); a file
        # from another tool only has explicit breaks. Use one source, never both, so no double
        # count. Without either the page is unknown, not 1.
        rendered = bool(body.xpath(".//w:lastRenderedPageBreak"))
        breaks = ".//w:lastRenderedPageBreak" if rendered else './/w:br[@w:type="page"]'
        has_pages = rendered or bool(body.xpath(breaks))
        page = 1
        for element in _docx_children(body):
            tag = element.tag.rsplit("}", 1)[-1]
            where = page if has_pages else None
            if tag == "p":
                paragraph = Paragraph(element, document)
                style = (paragraph.style.name if paragraph.style is not None else "") or ""
                heading = re.fullmatch(r"Heading (\d)", style)
                if heading or style == "Title":
                    kind: BlockKind = "heading"
                    level = min(int(heading[1]), 6) if heading else 1
                elif style.startswith("List"):
                    kind, level = "list_item", None
                else:
                    kind, level = "paragraph", None
                parts, page_breaks = _docx_paragraph_parts(element, rendered=rendered)
                for text, page_offset in parts:
                    where = page + page_offset if has_pages else None
                    out.add(kind, text, level=level, page=where)
            elif tag == "tbl":
                page_breaks = len(element.xpath(breaks))
                rows = []
                for row in Table(element, document).rows:
                    seen: set[int] = set()
                    cells = []
                    for cell in row.cells:
                        if id(cell._tc) not in seen:  # a merged cell is returned once per column
                            seen.add(id(cell._tc))
                            cells.append(_cell(cell.text))
                    rows.append(cells)
                out.add("table", _table(rows), page=where)
            else:
                page_breaks = len(element.xpath(breaks))
            page += page_breaks
    return _finish(parser, out)


# --- PPTX --------------------------------------------------------------------------------------


def _pptx_shapes(shapes: Any, out: _Blocks, slide: int, skip_id: int | None) -> None:
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for shape in sorted(shapes, key=lambda s: (s.top or 0, s.left or 0)):
        if shape.shape_id == skip_id:
            continue
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            _pptx_shapes(shape.shapes, out, slide, skip_id)
        elif getattr(shape, "has_table", False) and shape.has_table:
            rows = [[_cell(c.text) for c in row.cells] for row in shape.table.rows]
            out.add("table", _table(rows), slide=slide)
        elif getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            for paragraph in shape.text_frame.paragraphs:
                kind: BlockKind = "list_item" if paragraph.level > 0 else "paragraph"
                out.add(kind, paragraph.text, slide=slide)


def parse_pptx(path: Path) -> ParsedDocument:
    from pptx import Presentation

    parser = _parser_id("python-pptx")
    out = _Blocks()
    with _damaged_on_error():
        presentation = Presentation(str(path))
        for number, slide in enumerate(presentation.slides, 1):
            title = slide.shapes.title
            title_text = title.text_frame.text if title is not None and title.has_text_frame else ""
            before = len(out.items)
            out.add("heading", title_text, level=1, slide=number)
            if len(out.items) == before:  # no title: a slide is still a section
                out.add("heading", f"Slide {number}", level=1, slide=number)
            _pptx_shapes(slide.shapes, out, number, title.shape_id if title is not None else None)
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
                out.add("notes", slide.notes_slide.notes_text_frame.text, slide=number)
    return _finish(parser, out)


# --- XLSX --------------------------------------------------------------------------------------


def _sheet_blocks(out: _Blocks, sheet: Any, index: int, warnings: list[str]) -> None:
    header: str | None = None
    rows: list[str] = []
    emitted = False
    for read, values in enumerate(sheet.iter_rows(values_only=True), 1):
        if read > MAX_SHEET_ROWS:
            warnings.append(f"rows_capped:{index}")
            break
        cells = ["" if value is None else _cell(value) for value in values]
        while cells and not cells[-1]:
            cells.pop()
        if not cells:
            continue
        line = "\t".join(cells)
        if header is None:
            header = line
            continue
        rows.append(line)
        if len(rows) == XLSX_BLOCK_ROWS:
            out.add("table", "\n".join([header, *rows]))
            rows, emitted = [], True
    if header is not None and (rows or not emitted):
        out.add("table", "\n".join([header, *rows]))


def parse_xlsx(path: Path) -> ParsedDocument:
    from openpyxl import load_workbook

    parser = _parser_id("openpyxl")
    out = _Blocks()
    warnings: list[str] = []
    with _damaged_on_error():
        # data_only reads the value Excel cached; a formula never calculated is an empty cell.
        book = load_workbook(str(path), read_only=True, data_only=True)
        try:
            for index, sheet in enumerate(book.worksheets, 1):
                out.add("heading", sheet.title, level=1)
                _sheet_blocks(out, sheet, index, warnings)
        finally:
            book.close()
    return _finish(parser, out, warnings)


# --- HTML --------------------------------------------------------------------------------------

_DROPPED_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "head",
    "title",
    "nav",
    "footer",
    "aside",
)
_INLINE_TAGS = frozenset(
    [
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "big",
        "cite",
        "code",
        "data",
        "del",
        "dfn",
        "em",
        "font",
        "i",
        "img",
        "ins",
        "kbd",
        "label",
        "mark",
        "q",
        "s",
        "samp",
        "small",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "time",
        "tt",
        "u",
        "var",
        "wbr",
    ]
)
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
_HEADING_TAG = re.compile(r"h([1-6])")


def _drop_hidden(soup: Any) -> bool:
    """Remove hidden elements; True when any of them carried text."""
    dropped_text = False
    for tag in soup.find_all(True):
        if tag.decomposed:
            continue
        if tag.has_attr("hidden") or _HIDDEN_STYLE.search(tag.get("style") or ""):
            dropped_text = dropped_text or bool(tag.get_text(strip=True))
            tag.decompose()
    return dropped_text


def _html_walk(node: Any, kind: BlockKind, out: _Blocks) -> None:
    from bs4 import NavigableString, Tag

    buffer: list[str] = []

    def flush() -> None:
        out.add(kind, " ".join("".join(buffer).split()))
        buffer.clear()

    for child in node.children:
        if isinstance(child, Tag):
            name = child.name
            if name in _INLINE_TAGS:
                buffer.append(child.get_text())
                continue
            if name == "br":
                buffer.append(" ")
                continue
            flush()
            heading = _HEADING_TAG.fullmatch(name)
            if heading:
                out.add("heading", child.get_text(), level=int(heading[1]))
            elif name == "p":
                out.add(kind, " ".join(child.get_text().split()))
            elif name == "li":
                _html_walk(child, "list_item", out)
            elif name == "pre":
                out.add("code", child.get_text())
            elif name == "table":
                rows = [
                    [_cell(c.get_text()) for c in tr.find_all(["th", "td"])]
                    for tr in child.find_all("tr")
                ]
                out.add("table", _table(rows))
            else:
                _html_walk(child, kind, out)
        elif type(child) is NavigableString:  # not a comment, doctype or CDATA
            buffer.append(str(child))
    flush()


def parse_html(path: Path) -> ParsedDocument:
    from bs4 import BeautifulSoup, Comment

    parser = _parser_id("beautifulsoup4")
    out = _Blocks()
    with _damaged_on_error():
        soup = BeautifulSoup(path.read_bytes(), "html.parser")
        for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
            comment.extract()
        for tag in soup(_DROPPED_TAGS):
            if not tag.decomposed:
                tag.decompose()
        hidden = _drop_hidden(soup)
        _html_walk(soup.body or soup, "paragraph", out)
    return _finish(parser, out, ["hidden_text_dropped"] if hidden else [])


# --- Markdown ----------------------------------------------------------------------------------

_FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)


def _markdown_table(tokens: list[Any], start: int) -> str:
    rows: list[list[str]] = []
    for token in tokens[start:]:
        if token.type == "table_close":
            break
        if token.type == "tr_open":
            rows.append([])
        elif token.type == "inline":
            rows[-1].append(_cell(token.content))
    return _table(rows)


def parse_markdown(path: Path) -> ParsedDocument:
    from markdown_it import MarkdownIt

    parser = _parser_id("markdown-it-py")
    out = _Blocks()
    with _damaged_on_error():
        text = _clean(_decode(path.read_bytes()))
        front = _FRONT_MATTER.match(text)
        if front:
            out.add("code", front[1])  # kept as one block: it often names the title and date
            text = text[front.end() :]
        lines = text.split("\n")
        tokens = MarkdownIt("commonmark").enable("table").parse(text)
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token.level == 0 and token.map is not None:
                raw = "\n".join(lines[token.map[0] : token.map[1]])
                if token.type == "heading_open":
                    out.add("heading", tokens[i + 1].content, level=int(token.tag[1]))
                elif token.type == "table_open":
                    out.add("table", _markdown_table(tokens, i))
                elif token.type in ("bullet_list_open", "ordered_list_open"):
                    out.add("list_item", raw)
                elif token.type in ("fence", "code_block"):
                    out.add("code", token.content)
                elif token.type in ("paragraph_open", "blockquote_open"):
                    out.add("paragraph", raw)
                elif token.type == "html_block" and not token.content.lstrip().startswith("<!--"):
                    out.add("paragraph", token.content)
            if token.nesting == 1:  # the children were handled above: jump to the closing token
                depth = 1
                while depth and i + 1 < len(tokens):
                    i += 1
                    depth += tokens[i].nesting
            i += 1
    return _finish(parser, out)


# --- TXT ---------------------------------------------------------------------------------------


def parse_txt(path: Path) -> ParsedDocument:
    parser = f"python {platform.python_version()}"
    out = _Blocks()
    for paragraph in _PARAGRAPH_BREAK.split(_clean(_decode(path.read_bytes()))):
        out.add("paragraph", paragraph)
    return _finish(parser, out)


PARSERS: dict[str, Callable[[Path], ParsedDocument]] = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".pptx": parse_pptx,
    ".xlsx": parse_xlsx,
    ".html": parse_html,
    ".htm": parse_html,
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".txt": parse_txt,
}
