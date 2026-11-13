"""Rules for original files (Task 6): which names and ids are safe, and the upload link record.

Pure code, no disk access, shared by the disk store and its memory twin. Every message here is
fixed text: a refused file name or title never appears in an error."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

MAX_FILENAME_CHARS = 200
MAX_VERSION = 1_000_000

# Windows device names are refused with or without an extension, and with trailing spaces.
RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_BIDI_CONTROLS = frozenset(range(0x202A, 0x202F)) | frozenset(range(0x2066, 0x206A))
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class UploadRejected(Exception):
    """An upload or copy was refused. `code` is one of: unknown_token, too_large, size_mismatch,
    low_disk, cancelled, erased, busy."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class UploadSession:
    """A one-time upload link: the token is the capability, valid until `expires_at`."""

    token: str = field(repr=False)  # a live capability: never printed
    workspace_id: str
    owner_id: str
    source_id: str
    version: int
    filename: str = field(repr=False)  # sanitised, metadata only, never a path part or printed
    size_bytes: int
    expires_at: datetime


def _is_reserved(stem: str) -> bool:
    # NFKC folds look-alikes such as the superscript digits that Windows also reserves.
    return unicodedata.normalize("NFKC", stem.rstrip(" ")).upper() in RESERVED_NAMES


def safe_filename(name: str) -> str:
    """The last path part of a client-supplied file name, or ValueError when it is not safe.

    Refused: empty names, `.` and `..` parts, NUL and other control characters, lone surrogates,
    bidirectional overrides, colons (drive letters and NTFS streams), Windows device names, a
    trailing dot or space, and more than 200 characters."""
    bad = ValueError("Unsafe file name")
    if not isinstance(name, str):
        raise bad
    parts = re.split(r"[\\/]", name)
    base = parts[-1]
    if any(part in {".", ".."} for part in parts) or not base:
        raise bad
    if len(base) > MAX_FILENAME_CHARS or base.endswith((" ", ".")):
        raise bad
    for char in base:
        if unicodedata.category(char) in {"Cc", "Cs"} or ord(char) in _BIDI_CONTROLS or char == ":":
            raise bad
    if _is_reserved(base.split(".")[0]):
        raise bad
    return base


def check_id(value: str) -> str:
    """A workspace or source id that may be used as one folder name."""
    if not isinstance(value, str) or not _ID.fullmatch(value) or _is_reserved(value):
        raise ValueError("Unsafe identifier")
    return value


def check_version(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_VERSION:
        raise ValueError("Unsafe version")
    return value
