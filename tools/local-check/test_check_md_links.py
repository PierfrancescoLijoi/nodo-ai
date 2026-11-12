from pathlib import Path

from check_md_links import broken_links


def _md(tmp_path: Path, text: str) -> Path:
    f = tmp_path / "doc.md"
    f.write_text(text, encoding="utf-8")
    return f


def test_reports_missing_relative_target(tmp_path):
    assert broken_links(_md(tmp_path, "[x](nope.md)\n")) == [(1, "nope.md")]


def test_ignores_urls_and_anchors(tmp_path):
    text = "[a](https://example.com) [b](mailto:a@b.c) [c](#top)\n"
    assert broken_links(_md(tmp_path, text)) == []


def test_accepts_existing_target(tmp_path):
    (tmp_path / "other.md").write_text("x", encoding="utf-8")
    text = "line one\n[x](other.md#section)\n"
    assert broken_links(_md(tmp_path, text)) == []
