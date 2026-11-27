from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nodo_api.adapters.local.documents import formats
from nodo_api.adapters.local.documents.formats import (
    PARSERS,
    parse_docx,
    parse_html,
    parse_markdown,
    parse_pdf,
    parse_pptx,
    parse_txt,
    parse_xlsx,
)
from nodo_api.domain.documents import Block, ParsedDocument, ParseError
from tests import fixtures_documents as fx


@pytest.fixture(scope="session")
def out(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("documents")


def headings(doc: ParsedDocument) -> list[tuple[str, int]]:
    return [(b.text, b.heading_level) for b in doc.blocks if b.kind == "heading"]


def find(doc: ParsedDocument, needle: str) -> Block:
    matches = [b for b in doc.blocks if needle in b.text]
    assert len(matches) == 1, f"{needle!r} found in {len(matches)} blocks"
    return matches[0]


def assert_parser_id(doc: ParsedDocument, library: str) -> None:
    assert re.fullmatch(re.escape(library) + r" \d[\w.]*", doc.parser), doc.parser


# --- DOCX --------------------------------------------------------------------------------------


def test_docx_headings_and_table(out: Path):
    doc = parse_docx(fx.docx3(out))
    assert_parser_id(doc, "python-docx")
    expected = []
    for n in (1, 2, 3):
        expected += [(f"Chapter {n}", 1), (f"Topic {n}.1", 2), (f"Detail {n}.1.1", 3)]
    assert headings(doc) == expected
    table = next(b for b in doc.blocks if b.kind == "table")
    rows = table.text.split("\n")
    assert rows[0] == "\t".join(f"CELL-R0C{c}" for c in range(4))
    assert len(rows) == 3
    assert table.page == 2
    for n in (1, 2, 3):
        assert find(doc, f"PAGEMARK-{n}").page == n
    bullets = [b for b in doc.blocks if b.text == "A bullet point"]
    assert [(b.kind, b.page) for b in bullets] == [
        ("list_item", 1),
        ("list_item", 2),
        ("list_item", 3),
    ]
    assert not doc.no_text_layer
    assert doc.warnings == ()


def test_docx_without_page_markers_has_no_page(out: Path):
    from docx import Document

    doc = Document()
    doc.add_heading("Only", 1)
    doc.add_paragraph("No page break anywhere")
    path = out / "nobreak.docx"
    doc.save(path)
    parsed = parse_docx(path)
    assert [b.page for b in parsed.blocks] == [None, None]


def test_docx_text_after_inline_page_break_uses_the_next_page(out: Path):
    from docx import Document
    from docx.enum.text import WD_BREAK

    doc = Document()
    paragraph = doc.add_paragraph("BEFORE")
    paragraph.add_run().add_break(WD_BREAK.PAGE)
    paragraph.add_run("AFTER")
    path = out / "inline-break.docx"
    doc.save(path)

    parsed = parse_docx(path)

    assert [(b.text, b.page) for b in parsed.blocks] == [("BEFORE", 1), ("AFTER", 2)]


def test_corrupt_docx_is_damaged_not_text(out: Path):
    with pytest.raises(ParseError) as err:
        parse_docx(fx.not_a_zip(out))
    assert err.value.code == "damaged"
    garbage = out / "garbage.docx"
    garbage.write_bytes(b"PK\x03\x04" + b"\x00garbage" * 50)
    with pytest.raises(ParseError) as err:
        parse_docx(garbage)
    assert err.value.code == "damaged"


def test_injection_sentence_is_plain_paragraph_text(out: Path):
    doc = parse_docx(fx.docx_injection(out))
    block = find(doc, fx.INJECTION)
    assert block.kind == "paragraph"
    assert block.heading_level is None
    assert headings(doc) == [("Quarterly notes", 1)]


# --- PPTX --------------------------------------------------------------------------------------


def test_pptx_slide_is_a_section_and_carries_notes(out: Path):
    doc = parse_pptx(fx.pptx5(out))
    assert_parser_id(doc, "python-pptx")
    assert headings(doc) == [(f"Slide title {n}", 1) for n in range(1, 6)]
    assert [b.slide for b in doc.blocks if b.kind == "heading"] == [1, 2, 3, 4, 5]
    for n in range(1, 6):
        assert find(doc, f"SLIDEMARK-{n}").slide == n
        notes = find(doc, f"NOTEMARK-{n}")
        assert (notes.kind, notes.slide) == ("notes", n)
    assert all(b.slide is not None for b in doc.blocks)
    table = next(b for b in doc.blocks if b.kind == "table")
    assert table.slide == 3
    assert table.text == "CELL-R0C0\tCELL-R0C1\nCELL-R1C0\tCELL-R1C1"
    # reading order inside a slide: title, body, then notes last
    slide1 = [b for b in doc.blocks if b.slide == 1]
    assert slide1[0].kind == "heading" and slide1[-1].kind == "notes"


def test_pptx_slide_without_title_gets_a_numbered_heading(out: Path):
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text_frame.text = "Loose"
    path = out / "untitled.pptx"
    prs.save(path)
    doc = parse_pptx(path)
    assert headings(doc) == [("Slide 1", 1)]
    assert find(doc, "Loose").slide == 1


# --- XLSX --------------------------------------------------------------------------------------


def test_xlsx_sheet_blocks_repeat_the_header(out: Path):
    doc = parse_xlsx(fx.xlsx2(out))
    assert_parser_id(doc, "openpyxl")
    assert headings(doc) == [("Budget", 1), ("Notes", 1)]
    tables = [b for b in doc.blocks if b.kind == "table"]
    budget = tables[:-1]
    assert [len(t.text.split("\n")) for t in budget] == [51, 51, 21]
    assert all(t.text.startswith("Item\tQ1\tQ2\tTotal\n") for t in budget)
    assert "Item 120\t120\t240" in budget[-1].text
    # a formula without a cached value is an empty cell, never the string NaN or None
    assert "NaN" not in " ".join(b.text for b in doc.blocks)
    assert "None" not in " ".join(b.text for b in doc.blocks)
    assert tables[-1].text == "Topic\tText\nSHEETMARK-2\tSecond sheet text"


def test_xlsx_row_cap_is_20000_and_warns(out: Path, monkeypatch: pytest.MonkeyPatch):
    assert formats.MAX_SHEET_ROWS == 20_000
    monkeypatch.setattr(formats, "MAX_SHEET_ROWS", 60)
    doc = parse_xlsx(fx.xlsx2(out))
    assert "rows_capped:1" in doc.warnings
    budget_lines = sum(
        len(b.text.split("\n")) - 1 for b in doc.blocks if b.kind == "table" and "Item" in b.text
    )
    assert budget_lines == 59  # 60 rows read, the first is the header


# --- PDF ---------------------------------------------------------------------------------------


def test_pdf_pages_and_outline_headings(out: Path):
    doc = parse_pdf(fx.pdf5_outline(out))
    assert_parser_id(doc, "pdfminer.six")
    assert not doc.no_text_layer
    assert doc.warnings == ()
    heads = [b for b in doc.blocks if b.kind == "heading"]
    assert [(b.text, b.heading_level, b.page) for b in heads] == [
        item for n in range(1, 6) for item in ((f"Chapter {n}", 1, n), (f"Topic {n}.1", 2, n))
    ]
    for n in range(1, 6):
        assert find(doc, f"PAGEMARK-{n}").page == n
    assert find(doc, "CELL-R0C0").page == 3
    assert all(b.page is not None for b in doc.blocks)
    # the heading text is not repeated inside a paragraph
    assert not any(b.kind != "heading" and b.text.startswith("Chapter") for b in doc.blocks)


def test_pdf_without_outline_ranks_font_sizes(out: Path):
    doc = parse_pdf(fx.pdf5_no_outline(out))
    heads = [b for b in doc.blocks if b.kind == "heading"]
    assert [(b.text, b.heading_level, b.page) for b in heads] == [
        item for n in range(1, 6) for item in ((f"Chapter {n}", 1, n), (f"Topic {n}.1", 2, n))
    ]
    assert find(doc, "PAGEMARK-4").page == 4


def test_pdf_without_text_layer_is_flagged(out: Path):
    doc = parse_pdf(fx.pdf_bitmap_only(out))
    assert doc.no_text_layer is True
    assert doc.blocks == ()


def test_mixed_pdf_imports_text_pages_and_warns(out: Path):
    doc = parse_pdf(fx.pdf_mixed(out))
    assert doc.no_text_layer is False
    assert "pages_without_text:3,4" in doc.warnings
    assert sorted({b.page for b in doc.blocks}) == [1, 2, 5]
    assert find(doc, "PAGEMARK-5").page == 5


def test_encrypted_pdf_is_encrypted(out: Path):
    with pytest.raises(ParseError) as err:
        parse_pdf(fx.pdf_encrypted(out))
    assert err.value.code == "encrypted"


def test_truncated_pdf_is_damaged(out: Path):
    with pytest.raises(ParseError) as err:
        parse_pdf(fx.pdf_truncated(out))
    assert err.value.code == "damaged"


def test_not_a_pdf_is_damaged(out: Path):
    path = out / "fake.pdf"
    path.write_text("plain text pretending to be a pdf", encoding="utf-8")
    with pytest.raises(ParseError) as err:
        parse_pdf(path)
    assert err.value.code == "damaged"


def test_fifty_page_pdf_is_fast_and_keeps_every_page(out: Path):
    path = fx.pdf50(out)
    started = time.perf_counter()
    doc = parse_pdf(path)
    elapsed = time.perf_counter() - started
    assert find(doc, "PAGEMARK-50").page == 50
    assert {b.page for b in doc.blocks} == set(range(1, 51))
    assert elapsed < 15, f"50-page PDF took {elapsed:.1f}s"


# --- HTML --------------------------------------------------------------------------------------


def test_html_drops_hidden_and_navigation_text(out: Path):
    doc = parse_html(fx.html_page(out))
    assert_parser_id(doc, "beautifulsoup4")
    everything = " ".join(b.text for b in doc.blocks)
    for junk in (
        "NAVJUNK",
        "SCRIPTJUNK",
        "HIDDENTEXT",
        "HIDDENATTR",
        "ASIDEJUNK",
        "FOOTERJUNK",
        "Page title",
    ):
        assert junk not in everything
    assert "hidden_text_dropped" in doc.warnings
    assert headings(doc) == [("Chapter 1", 1), ("Topic 1.1", 2), ("Topic 1.2", 2)]
    assert find(doc, "PAGEMARK-1").kind == "paragraph"
    assert find(doc, "Plain bold and link text.").kind == "paragraph"
    assert [b.text for b in doc.blocks if b.kind == "list_item"] == ["one", "two", "nested"]
    assert find(doc, "CELL-R1C0").text == "A\tB\nCELL-R1C0\tCELL-R1C1"
    assert find(doc, "code  block").text == "code  block\n  indented"


def test_html_without_hidden_content_has_no_warning(out: Path):
    path = out / "plain.html"
    path.write_text("<html><body><h1>T</h1><p>Body</p></body></html>", encoding="utf-8")
    assert parse_html(path).warnings == ()


# --- Markdown ----------------------------------------------------------------------------------


def test_markdown_front_matter_is_one_block(out: Path):
    doc = parse_markdown(fx.markdown_notes(out))
    assert_parser_id(doc, "markdown-it-py")
    first = doc.blocks[0]
    assert first.kind == "code"
    assert first.text == "title: Notes\ntags: [a, b]"
    assert headings(doc) == [("Chapter 1", 1), ("Topic 1.1", 2), ("Setext heading", 2)]
    assert [b.text for b in doc.blocks if b.kind == "list_item"] == ["- one\n- two"]
    assert find(doc, "CELL-R1C0").kind == "table"
    assert find(doc, 'print("code")').kind == "code"
    assert find(doc, "Tail paragraph.").kind == "paragraph"


# --- TXT ---------------------------------------------------------------------------------------


def test_txt_decodes_cp1252_and_keeps_accents(out: Path):
    doc = parse_txt(fx.txt_cp1252(out))
    assert [b.text for b in doc.blocks] == [
        "Caffè à Milano",
        "Second paragraph: città, €5.",
    ]
    assert all(b.kind == "paragraph" for b in doc.blocks)


def test_txt_decodes_utf16_with_bom_and_utf8_sig(out: Path):
    assert [b.text for b in parse_txt(fx.txt_utf16(out)).blocks] == ["Caffè UTF16", "Second."]
    path = out / "sig.txt"
    path.write_bytes(b"\xef\xbb\xbfHello \xc3\xa8\n\nWorld")
    assert [b.text for b in parse_txt(path).blocks] == ["Hello è", "World"]


def test_text_is_normalised(out: Path):
    path = out / "dirty.txt"
    path.write_bytes(b"a\x00b\x07c\td\r\n\r\n\r\n\r\n\r\ne\x0cf\n\n\n\n")
    doc = parse_txt(path)
    assert [b.text for b in doc.blocks] == ["abc\td", "e\nf"]


def test_empty_file_is_empty(out: Path):
    path = out / "blank.txt"
    path.write_text("  \n\n \n", encoding="utf-8")
    with pytest.raises(ParseError) as err:
        parse_txt(path)
    assert err.value.code == "empty"


# --- registry and hygiene ----------------------------------------------------------------------


def test_registry_maps_every_extension():
    assert set(PARSERS) == {
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".html",
        ".htm",
        ".md",
        ".markdown",
        ".txt",
    }
    assert PARSERS[".htm"] is PARSERS[".html"] is parse_html
    assert PARSERS[".markdown"] is PARSERS[".md"] is parse_markdown


def test_no_module_imports_fitz_or_pymupdf():
    root = Path(__file__).resolve().parents[1]
    needles = [kw + " " + lib for kw in ("import", "from") for lib in ("fitz", "pymupdf")]
    files = [*(root / "src" / "nodo_api").rglob("*.py"), *(root / "tests").rglob("*.py")]
    files = [f for f in files if f != Path(__file__).resolve()]
    assert files
    offenders = [f.name for f in files if any(n in f.read_text(encoding="utf-8") for n in needles)]
    assert offenders == []


def test_importing_formats_module_does_not_import_parser_libraries():
    code = (
        "import sys\n"
        "import nodo_api.adapters.local.documents.formats\n"
        "names = ['pdfminer', 'docx', 'pptx', 'openpyxl', 'bs4', 'pypdfium2', 'markdown_it']\n"
        "loaded = [n for n in names if n in sys.modules]\n"
        "assert not loaded, loaded\n"
    )
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
