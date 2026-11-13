"""Content-based document type detection and cheap pre-parse limits.

Nothing here lists a whole archive before the entry count has been checked, and nothing opens a
PDF in the engine process: pages are counted by a confined child process.
"""

from __future__ import annotations

import re
import struct
import zipfile
from pathlib import Path

from nodo_api.adapters.local.documents.isolation import run_isolated
from nodo_api.config import Settings
from nodo_api.domain.documents import ParseError

MAX_OOXML_ENTRIES = 2000
# A real OOXML directory entry is well under 1 KB, so 2000 entries fit in this with a wide margin.
_MAX_DIRECTORY_BYTES = 1024 * 1024
# Entries below this size are not judged by their own ratio (tiny XML parts compress very well).
_ENTRY_RATIO_FLOOR = 1024 * 1024
_PAGE_COUNT_SECONDS = 30.0
_EOCD = b"PK\x05\x06"
_EOCD_SEARCH = 22 + 65535

_OOXML_KEYS = {"word/": ".docx", "ppt/": ".pptx", "xl/": ".xlsx"}
_OOXML_EXTENSIONS = frozenset(_OOXML_KEYS.values())
_BINARY_EXTENSIONS = frozenset({".pdf", *_OOXML_EXTENSIONS})
_TEXT_EXTENSIONS = frozenset({".txt", ".md", ".markdown"})
_MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown"})
_UTF8_BOM = bytes([0xEF, 0xBB, 0xBF])
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
_HTML_TAG = re.compile(
    rb"<(?:!doctype\s+html|html|head|body|meta|title|link|style|script|div|p|span|a|ul|ol|li"
    rb"|table|section|article|main|nav|header|footer|form|h[1-6]|br|img)(?![a-z0-9])",
    re.I,
)

_DEFAULTS = Settings.model_fields


def _media(head: bytes) -> bool:
    """True only when the header really is a media container, not text that starts alike."""
    if head[:4] == b"RIFF" and head[8:12] in (b"WAVE", b"AVI "):
        return True
    if head[:3] == b"ID3" and len(head) >= 10:
        return head[3] in (2, 3, 4) and all(byte < 0x80 for byte in head[6:10])
    if head[:5] == b"OggS\x00":
        return True
    if head[:4] == b"fLaC" and len(head) >= 8:
        return head[4] & 0x7F == 0 and head[5:8] == b"\x00\x00\x22"
    if head[4:8] == b"ftyp" and len(head) >= 12:
        return True
    if head[:4] == bytes([0x1A, 0x45, 0xDF, 0xA3]):
        return True
    return len(head) >= 2 and head[0] == 0xFF and head[1] in (0xFB, 0xF3, 0xF1)


def _looks_like_html(head: bytes) -> bool:
    """A leading tag, after a BOM, white space and comments (scanned without backtracking)."""
    head = head.removeprefix(_UTF8_BOM).lstrip()
    while head.startswith(b"<!--"):
        end = head.find(b"-->")
        if end < 0:
            return False
        head = head[end + 3 :].lstrip()
    return _HTML_TAG.match(head) is not None


def _entries(path: Path) -> list[zipfile.ZipInfo]:
    """The entries of a zip, read only after the end record says the directory is small."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - _EOCD_SEARCH))
            tail = handle.read()
    except OSError:
        raise ParseError("damaged") from None
    at = tail.rfind(_EOCD)
    if at < 0 or len(tail) - at < 22:
        raise ParseError("damaged")
    (count,) = struct.unpack_from("<H", tail, at + 10)
    (directory_bytes,) = struct.unpack_from("<I", tail, at + 12)
    # 0xFFFF and 0xFFFFFFFF mean Zip64: far over the caps either way.
    if count >= MAX_OOXML_ENTRIES or directory_bytes > _MAX_DIRECTORY_BYTES:
        raise ParseError("too_large")
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.infolist()
    except (OSError, zipfile.BadZipFile, NotImplementedError, ValueError, EOFError):
        raise ParseError("damaged") from None


def _ooxml_key(entries: list[zipfile.ZipInfo]) -> str | None:
    names = [entry.filename for entry in entries]
    if "[Content_Types].xml" not in names:
        return None
    roots = [
        extension
        for prefix, extension in _OOXML_KEYS.items()
        if any(n.startswith(prefix) for n in names)
    ]
    return roots[0] if len(roots) == 1 else None


def _pdf_pages(path: Path, memory_mb: int) -> int | None:
    """Pages of a PDF, counted by pdfium in a confined child process.

    None means the file is password protected: the parser reports that with its own code."""
    code = (
        "import sys\n"
        "import pypdfium2 as p\n"
        "import pypdfium2.raw as r\n"
        "try:\n"
        "    d = p.PdfDocument(sys.argv[1])\n"
        "except p.PdfiumError as e:\n"
        "    print('encrypted' if e.err_code == r.FPDF_ERR_PASSWORD else 'damaged')\n"
        "    sys.exit(0)\n"
        "print(len(d))\n"
        "d.close()\n"
    )
    returncode, stdout = run_isolated(
        ["-c", code, str(path)], timeout=_PAGE_COUNT_SECONDS, memory_mb=memory_mb
    )
    answer = stdout.strip()
    if returncode == 0 and answer == b"encrypted":
        return None
    if returncode == 0 and answer.isdigit() and int(answer) > 0:
        return int(answer)
    raise ParseError("damaged")


def sniff(path: Path, declared_name: str) -> str:
    """Return a supported parser key from content, refusing contradictory binary declarations."""
    path = Path(path)
    declared = Path(declared_name).suffix.lower()
    try:
        with path.open("rb") as handle:
            head = handle.read(8192)
    except OSError:
        raise ParseError("damaged") from None

    detected: str
    if head.startswith(b"%PDF-"):
        detected = ".pdf"
    elif head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        detected = _ooxml_key(_entries(path)) or ""
        if not detected:
            raise ParseError("unsupported")
    elif head.startswith(_UTF16_BOMS):
        detected = declared if declared in _TEXT_EXTENSIONS else ".txt"
    elif _media(head) or b"\x00" in head:
        raise ParseError("unsupported")
    elif declared not in _MARKDOWN_EXTENSIONS and _looks_like_html(head):
        detected = ".html"
    else:
        detected = declared if declared in _TEXT_EXTENSIONS else ".txt"

    if declared in _BINARY_EXTENSIONS and declared != detected:
        raise ParseError("unsupported")
    return detected


def check_limits(
    path: Path,
    kind_key: str,
    *,
    max_bytes: int,
    max_pages: int,
    max_ratio: int,
    max_uncompressed_bytes: int = _DEFAULTS["local_import_max_uncompressed_bytes"].default,
    memory_mb: int = _DEFAULTS["local_parser_memory_mb"].default,
) -> None:
    """Refuse an oversized file, OOXML archive, or PDF before expensive parsing.

    An archive is judged on its directory only: entry count, total uncompressed size, the ratio of
    the whole and the ratio of each large entry, so that padding cannot dilute a bomb."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        raise ParseError("damaged") from None
    if size > max_bytes:
        raise ParseError("too_large")

    if kind_key in _OOXML_EXTENSIONS:
        entries = _entries(path)
        if len(entries) >= MAX_OOXML_ENTRIES:
            raise ParseError("too_large")
        uncompressed = sum(entry.file_size for entry in entries)
        compressed = max(1, sum(entry.compress_size for entry in entries))
        if uncompressed > max_uncompressed_bytes or uncompressed > compressed * max_ratio:
            raise ParseError("too_large")
        for entry in entries:
            if entry.file_size > _ENTRY_RATIO_FLOOR and (
                entry.file_size > max(1, entry.compress_size) * max_ratio
            ):
                raise ParseError("too_large")
    elif kind_key == ".pdf":
        pages = _pdf_pages(path, memory_mb)
        if pages is not None and pages > max_pages:
            raise ParseError("too_large")
