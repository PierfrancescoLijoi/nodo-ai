from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from nodo_api.adapters.local.documents import parser as parser_module
from nodo_api.adapters.local.documents import sniff as sniff_module
from nodo_api.adapters.local.documents.formats import PARSERS, parse_markdown, parse_pdf, parse_txt
from nodo_api.adapters.local.documents.isolation import child_environment, run_isolated
from nodo_api.adapters.local.documents.parser import SubprocessDocumentParser, _document
from nodo_api.adapters.local.documents.sniff import check_limits, sniff
from nodo_api.adapters.memory.documents import MemoryDocumentParser
from nodo_api.config import Settings
from nodo_api.domain.documents import Block, ParsedDocument, ParseError
from tests import fixtures_documents as fx

HOOK = "NODO_DOCUMENT_WORKER_TEST_HOOK"
HOOK_FILE = "NODO_DOCUMENT_WORKER_TEST_FILE"


@pytest.fixture(scope="session")
def out(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("document-parser")


def parse_error_code(call) -> str:
    with pytest.raises(ParseError) as error:
        call()
    return error.value.code


def limits(path: Path, kind: str, **overrides: int) -> None:
    values = {"max_bytes": 10_000_000, "max_pages": 100, "max_ratio": 100} | overrides
    check_limits(path, kind, **values)


def worker_hook(monkeypatch: pytest.MonkeyPatch, hook: str, file: Path | None = None) -> None:
    monkeypatch.setenv("NODO_ENVIRONMENT", "test")
    monkeypatch.setenv(HOOK, hook)
    if file is not None:
        monkeypatch.setenv(HOOK_FILE, str(file))


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_until_dead(pids: list[int], seconds: float = 10.0) -> list[int]:
    deadline = time.monotonic() + seconds
    alive = [pid for pid in pids if pid_alive(pid)]
    while alive and time.monotonic() < deadline:
        time.sleep(0.1)
        alive = [pid for pid in pids if pid_alive(pid)]
    return alive


# --- sniff ------------------------------------------------------------------------------------


def test_sniff_rejects_a_pdf_that_is_really_a_zip(out: Path):
    fake = out / "lie.pdf"
    with zipfile.ZipFile(fake, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")

    assert parse_error_code(lambda: sniff(fake, fake.name)) == "unsupported"


def test_sniff_accepts_ooxml_by_content_not_name(out: Path):
    disguised = out / "upload.bin"
    disguised.write_bytes(fx.docx3(out).read_bytes())

    assert sniff(disguised, disguised.name) == ".docx"


def test_sniff_recognises_pdf_html_and_text(out: Path):
    assert sniff(fx.pdf5_outline(out), "wrong.bin") == ".pdf"
    assert sniff(fx.html_page(out), "wrong.txt") == ".html"
    assert sniff(fx.markdown_notes(out), "notes.md") == ".md"
    assert sniff(fx.txt_cp1252(out), "cafe.txt") == ".txt"


def test_sniff_accepts_utf16_text_with_a_bom(out: Path):
    assert sniff(fx.txt_utf16(out), "wide.txt") == ".txt"


@pytest.mark.parametrize(
    "text",
    [
        b"ID3 tag notes for the meeting",
        b"RIFF analysis meeting minutes",
        b"PKI rollout plan for the quarter",
        b"OggS and fLaC are names of formats",
        b"ftyp is a word in the fourth place",
    ],
)
def test_text_that_starts_like_a_media_signature_is_still_text(out: Path, text: bytes):
    path = out / "notes.txt"
    path.write_bytes(text)

    assert sniff(path, path.name) == ".txt"


def wav() -> bytes:
    return b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + b"\x00" * 24


def mp3() -> bytes:
    return b"ID3\x03\x00\x00\x00\x00\x00\x21" + b"\x00" * 30


def ogg() -> bytes:
    return b"OggS\x00\x02" + b"\x00" * 30


def flac() -> bytes:
    return b"fLaC\x00\x00\x00\x22" + b"\x00" * 34


def mp4() -> bytes:
    return struct.pack(">I", 24) + b"ftypisom" + b"\x00" * 16


def matroska() -> bytes:
    return bytes([0x1A, 0x45, 0xDF, 0xA3]) + b"\x00" * 30


@pytest.mark.parametrize("make", [wav, mp3, ogg, flac, mp4, matroska])
def test_real_media_containers_are_refused(out: Path, make):
    path = out / "recording.bin"
    path.write_bytes(make())

    assert parse_error_code(lambda: sniff(path, path.name)) == "unsupported"


def test_binary_content_with_nul_bytes_is_refused(out: Path):
    path = out / "blob.txt"
    path.write_bytes(b"header\x00\x01\x02\x03 more bytes")

    assert parse_error_code(lambda: sniff(path, path.name)) == "unsupported"


@pytest.mark.parametrize(
    "head",
    [
        b"<meta charset=utf-8><title>x</title><p>Hello there</p>",
        b"<!-- generated -->\n<html><body><p>Hello there</p></body></html>",
        b"<div><p>Hello there</p></div>",
    ],
)
def test_html_is_recognised_without_a_doctype(out: Path, head: bytes):
    path = out / "page.bin"
    path.write_bytes(head)

    assert sniff(path, path.name) == ".html"


def test_a_markdown_file_that_starts_with_a_tag_stays_markdown(out: Path):
    path = out / "README.md"
    path.write_text('<div align="center">\n\n# Title\n\nBody text.', encoding="utf-8")

    assert sniff(path, path.name) == ".md"


def test_sniff_and_limits_map_unreadable_or_invalid_containers(tmp_path: Path):
    missing = tmp_path / "missing.docx"
    assert parse_error_code(lambda: sniff(missing, missing.name)) == "damaged"
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"PK\x03\x04broken")
    assert parse_error_code(lambda: sniff(broken, broken.name)) == "damaged"
    assert parse_error_code(lambda: limits(broken, ".docx")) == "damaged"


def test_a_zip_that_is_not_ooxml_is_unsupported(out: Path):
    path = out / "plain.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "hello")

    assert parse_error_code(lambda: sniff(path, path.name)) == "unsupported"


# --- limits -----------------------------------------------------------------------------------


def test_zip_bomb_is_refused_before_parsing(out: Path):
    path = out / "bomb.docx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", b"0" * 100_000)

    assert parse_error_code(lambda: limits(path, ".docx", max_ratio=2)) == "too_large"


def test_a_bomb_diluted_with_stored_padding_is_still_refused_per_entry(out: Path):
    path = out / "padded.docx"
    padding = os.urandom(150_000)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/media/padding.bin", padding)  # stored: ratio 1
        archive.writestr("word/document.xml", b"0" * 5_000_000, compress_type=zipfile.ZIP_DEFLATED)
    infos = zipfile.ZipFile(path).infolist()
    total = sum(i.file_size for i in infos) / sum(i.compress_size for i in infos)
    assert total < 100  # the aggregate ratio alone would pass this file

    assert parse_error_code(lambda: limits(path, ".docx")) == "too_large"


def test_total_uncompressed_size_has_an_absolute_cap(out: Path):
    path = out / "big.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", os.urandom(4000))

    assert parse_error_code(lambda: limits(path, ".docx", max_uncompressed_bytes=3000)) == (
        "too_large"
    )


def many_entries(path: Path, count: int, name_length: int = 8) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
        for number in range(count):
            archive.writestr(f"word/{number:0{name_length}d}", "")


def test_ooxml_entry_count_is_capped(out: Path):
    path = out / "entries.docx"
    many_entries(path, 1998)

    assert parse_error_code(lambda: limits(path, ".docx")) == "too_large"


def test_entry_count_is_read_from_the_directory_record_without_listing(
    out: Path, monkeypatch: pytest.MonkeyPatch
):
    path = out / "crowded.docx"
    many_entries(path, 3000)

    def forbidden(*args, **kwargs):
        raise AssertionError("the central directory must not be listed")

    monkeypatch.setattr(sniff_module.zipfile, "ZipFile", forbidden)

    assert parse_error_code(lambda: sniff(path, path.name)) == "too_large"
    assert parse_error_code(lambda: limits(path, ".docx")) == "too_large"


def test_a_lying_entry_count_cannot_hide_a_huge_directory(out: Path, monkeypatch):
    path = out / "liar.docx"
    many_entries(path, 6000, name_length=200)
    data = bytearray(path.read_bytes())
    eocd = data.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", data, eocd + 8, 3, 3)  # claim 3 entries
    path.write_bytes(bytes(data))

    def forbidden(*args, **kwargs):
        raise AssertionError("the central directory must not be listed")

    monkeypatch.setattr(sniff_module.zipfile, "ZipFile", forbidden)

    assert parse_error_code(lambda: sniff(path, path.name)) == "too_large"


def test_oversized_file_is_refused(out: Path):
    path = out / "large.txt"
    path.write_bytes(b"x" * 101)

    assert parse_error_code(lambda: limits(path, ".txt", max_bytes=100)) == "too_large"


def test_pdf_page_cap_is_enforced(out: Path):
    assert parse_error_code(lambda: limits(fx.pdf5_outline(out), ".pdf", max_pages=4)) == (
        "too_large"
    )
    limits(fx.pdf5_outline(out), ".pdf", max_pages=5)


def test_encrypted_pdf_passes_limits_and_is_reported_encrypted_by_the_worker(out: Path):
    path = fx.pdf_encrypted(out)
    limits(path, ".pdf", max_pages=10)

    parser = SubprocessDocumentParser(timeout_seconds=30, memory_mb=512)
    assert parse_error_code(lambda: parser.parse(path)) == "encrypted"


def test_a_truncated_pdf_is_damaged_at_the_page_counter(out: Path):
    assert parse_error_code(lambda: limits(fx.pdf_truncated(out), ".pdf")) == "damaged"


def test_a_failing_page_counter_never_skips_the_page_cap(out: Path, monkeypatch):
    path = fx.pdf5_outline(out)
    monkeypatch.setattr(sniff_module, "run_isolated", lambda *a, **k: (1, b""))
    assert parse_error_code(lambda: limits(path, ".pdf")) == "damaged"
    monkeypatch.setattr(sniff_module, "run_isolated", lambda *a, **k: (0, b"not a number"))
    assert parse_error_code(lambda: limits(path, ".pdf")) == "damaged"


def test_page_counter_runs_with_the_memory_ceiling_of_the_caller(out: Path, monkeypatch):
    seen: list[int] = []

    def spy(args, *, timeout, memory_mb, **kwargs):
        seen.append(memory_mb)
        return 0, b"5"

    monkeypatch.setattr(sniff_module, "run_isolated", spy)
    limits(fx.pdf5_outline(out), ".pdf", memory_mb=321)

    assert seen == [321]


# --- isolation --------------------------------------------------------------------------------


def test_child_environment_is_an_allowlist(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NODO_LOCAL_TOKEN", "fake-secret-token")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-aws-secret")
    monkeypatch.setenv("NODO_ENVIRONMENT", "prod")
    monkeypatch.setenv(HOOK, "crash")

    env = child_environment(256)

    assert "NODO_LOCAL_TOKEN" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert HOOK not in env and "NODO_ENVIRONMENT" not in env
    assert env["NODO_DOCUMENT_MEMORY_MB"] == "256"
    monkeypatch.setenv("NODO_ENVIRONMENT", "test")
    assert child_environment(256)[HOOK] == "crash"


def test_the_worker_never_sees_engine_secrets(out: Path, monkeypatch: pytest.MonkeyPatch):
    report = out / "env-report.txt"
    worker_hook(monkeypatch, "env", report)
    monkeypatch.setenv("NODO_LOCAL_TOKEN", "fake-secret-token")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-aws-secret")
    source = out / "plain.txt"
    source.write_text("A paragraph of plain text.", encoding="utf-8")

    parsed = SubprocessDocumentParser(timeout_seconds=30, memory_mb=512).parse(source)

    assert parsed.blocks[0].text == "A paragraph of plain text."
    keys = set(report.read_text(encoding="utf-8").split())
    assert keys, "the worker did not report its environment"
    assert "NODO_LOCAL_TOKEN" not in keys and "AWS_SECRET_ACCESS_KEY" not in keys


def test_the_page_counter_never_sees_engine_secrets(out: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NODO_LOCAL_TOKEN", "fake-secret-token")
    code, stdout = run_isolated(
        ["-c", "import os,sys;sys.stdout.write(','.join(os.environ))"], timeout=30, memory_mb=256
    )

    assert code == 0 and stdout
    assert b"NODO_LOCAL_TOKEN" not in stdout


def test_run_isolated_bounds_what_it_reads():
    code = "import sys;sys.stdout.write('x' * 200000)"

    assert (
        parse_error_code(
            lambda: run_isolated(["-c", code], timeout=30, memory_mb=256, max_output=1000)
        )
        == "damaged"
    )


# --- the worker process -----------------------------------------------------------------------


def test_subprocess_parser_matches_in_process_result(out: Path):
    path = fx.pdf5_outline(out)

    parsed = SubprocessDocumentParser(timeout_seconds=30, memory_mb=512).parse(path)

    assert parsed == parse_pdf(path)


@pytest.mark.parametrize(
    ("make", "name"),
    [
        (fx.docx3, "m.docx"),
        (fx.pptx5, "m.pptx"),
        (fx.xlsx2, "m.xlsx"),
        (fx.html_page, "m.html"),
        (fx.markdown_notes, "m.md"),
        (fx.txt_cp1252, "m.txt"),
        (fx.txt_utf16, "w.txt"),
    ],
)
def test_every_format_parses_under_the_isolated_interpreter(out: Path, make, name: str):
    path = make(out)
    key = sniff(path, path.name)

    parsed = SubprocessDocumentParser(timeout_seconds=60, memory_mb=1024).parse(path)

    assert parsed == PARSERS[key](path)
    assert parsed.blocks


def test_markdown_stored_without_an_extension_still_uses_the_markdown_parser(out: Path):
    stored = out / "blobs" / "original"
    stored.parent.mkdir(exist_ok=True)
    shutil.copyfile(fx.markdown_notes(out), stored)
    parser = SubprocessDocumentParser(timeout_seconds=60, memory_mb=1024)

    parsed = parser.parse(stored, declared_name="Meeting notes.MD")

    assert parsed == parse_markdown(stored)
    assert any(block.kind == "heading" for block in parsed.blocks)
    assert parsed != parse_txt(stored)


def test_every_format_dependency_imports_under_the_isolated_interpreter():
    names = "pdfminer.high_level, pypdfium2, docx, pptx, openpyxl, bs4, lxml, markdown_it, mdurl"
    result = subprocess.run(
        [sys.executable, "-I", "-c", f"import {names}"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_parser_noise_on_stdout_does_not_break_the_result(
    out: Path, monkeypatch: pytest.MonkeyPatch
):
    worker_hook(monkeypatch, "noise")
    source = out / "noisy.txt"
    source.write_text("Body of the noisy file.", encoding="utf-8")

    parsed = SubprocessDocumentParser(timeout_seconds=30, memory_mb=512).parse(source)

    assert parsed.blocks[0].text == "Body of the noisy file."


def test_subprocess_timeout_kills_the_worker_and_its_children(
    out: Path, monkeypatch: pytest.MonkeyPatch
):
    pids = out / "pids.txt"
    pids.unlink(missing_ok=True)
    worker_hook(monkeypatch, "spawn", pids)
    parser = SubprocessDocumentParser(timeout_seconds=5, memory_mb=512)
    started = time.monotonic()

    assert parse_error_code(lambda: parser.parse(fx.pdf5_outline(out))) == "timeout"

    assert time.monotonic() - started < 30
    recorded = [int(value) for value in pids.read_text(encoding="utf-8").split()]
    assert len(recorded) == 2, "the worker never reached the sleeping hook"
    assert wait_until_dead(recorded) == []


def test_worker_crash_maps_to_damaged(out: Path, monkeypatch: pytest.MonkeyPatch):
    worker_hook(monkeypatch, "crash")

    assert (
        parse_error_code(
            lambda: SubprocessDocumentParser(timeout_seconds=30, memory_mb=512).parse(
                fx.pdf5_outline(out)
            )
        )
        == "damaged"
    )


def test_memory_ceiling_stops_a_worker_that_needs_more(out: Path, monkeypatch):
    source = out / "ceiling.txt"
    source.write_text("Small body.", encoding="utf-8")
    low = SubprocessDocumentParser(timeout_seconds=60, memory_mb=160)
    high = SubprocessDocumentParser(timeout_seconds=60, memory_mb=1024)

    assert low.parse(source).blocks[0].text == "Small body."  # the interpreter starts at 160 MB
    worker_hook(monkeypatch, "alloc:400")
    assert parse_error_code(lambda: low.parse(source)) == "damaged"
    assert high.parse(source).blocks[0].text == "Small body."  # same allocation fits in 1 GB


def test_output_that_cannot_be_decoded_is_damaged_not_an_exception(
    out: Path, monkeypatch: pytest.MonkeyPatch
):
    parser = SubprocessDocumentParser(timeout_seconds=1, memory_mb=64)
    for data in (b"\xff\xfe\x80\x81 garbage", b"\x80\x81\x82", b"{not json", b"[1, 2]"):
        monkeypatch.setattr(parser_module, "run_isolated", lambda *a, _d=data, **k: (0, _d))
        assert parse_error_code(lambda: parser.parse(out / "x.txt")) == "damaged"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"ok": False, "error": 3},
        {"ok": False, "error": "not-a-code"},
        {},
        {"ok": True, "parser": 3, "blocks": [], "no_text_layer": False, "warnings": []},
        {"ok": True, "parser": "x 1", "blocks": [], "no_text_layer": "no", "warnings": []},
        {"ok": True, "parser": "x 1", "blocks": [], "no_text_layer": False, "warnings": [3]},
    ],
)
def test_invalid_worker_payload_maps_to_damaged(payload: object):
    assert parse_error_code(lambda: _document(payload)) == "damaged"


def test_a_registered_error_code_in_the_payload_is_kept():
    assert parse_error_code(lambda: _document({"ok": False, "error": "encrypted"})) == ("encrypted")


def test_error_cause_does_not_keep_library_messages(out: Path):
    path = out / "cause.docx"
    path.write_bytes(b"PK\x03\x04broken")
    with pytest.raises(ParseError) as error:
        sniff(path, path.name)

    assert error.value.__cause__ is None
    assert str(error.value) == "damaged"


def test_parser_rejects_invalid_resource_settings(tmp_path: Path):
    with pytest.raises(ValueError):
        SubprocessDocumentParser(timeout_seconds=0, memory_mb=512)
    with pytest.raises(ValueError):
        SubprocessDocumentParser(timeout_seconds=1, memory_mb=0)
    parser = SubprocessDocumentParser(timeout_seconds=1, memory_mb=512)
    with pytest.raises(ValueError):
        parser.parse(tmp_path / "unused", timeout_seconds=0)


def test_engine_process_never_imports_parser_libraries():
    code = (
        "import sys\n"
        "import nodo_api.adapters.local.documents.parser\n"
        "import nodo_api.adapters.local.documents.sniff\n"
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


def test_memory_parser_returns_or_raises_registered_result(tmp_path: Path):
    good = tmp_path / "good.txt"
    bad = tmp_path / "bad.txt"
    document = ParsedDocument("test 1", (Block("paragraph", "body"),))
    parser = MemoryDocumentParser()
    parser.register(good, document)
    parser.register_error(bad, ParseError("damaged"))

    assert parser.parse(good) == document
    assert parser.parse(good, declared_name="good.txt") == document
    assert parse_error_code(lambda: parser.parse(bad)) == "damaged"
    assert parse_error_code(lambda: parser.parse(tmp_path / "missing.txt")) == "damaged"


def test_document_parser_settings_have_bounded_defaults():
    settings = Settings(
        environment="test",
        auth_mode="dev",
        attestation_hmac_key="dev-only-attestation-key",
    )

    assert settings.local_import_max_bytes == 200 * 1024 * 1024
    assert settings.local_import_max_pages == 2000
    assert settings.local_import_max_ratio == 100
    assert settings.local_import_max_uncompressed_bytes == 512 * 1024 * 1024
    assert settings.local_parser_timeout_seconds == 300.0
    assert settings.local_parser_memory_mb == 2048
