"""Isolated document parser process.

Usage: python -I -m nodo_api.adapters.local.documents.worker <path> [<extension>]

The extension is the one of the original file name (".md"): the stored copy may have none. The
result is one JSON object written to a private copy of the original stdout; while the parsers run,
file descriptor 1 points at stderr, so whatever a library prints cannot corrupt it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import BinaryIO

from nodo_api.adapters.local.documents.formats import PARSERS
from nodo_api.adapters.local.documents.sniff import sniff
from nodo_api.domain.documents import ParseError

_EXTENSION = re.compile(r"\.[a-z0-9]{1,16}")
_ballast: list[bytearray] = []


def _apply_memory_limit() -> None:
    """Windows is limited by the parent Job; POSIX applies the equivalent address-space cap."""
    if os.name == "nt":
        return
    try:
        import resource

        limit = int(os.environ["NODO_DOCUMENT_MEMORY_MB"]) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (KeyError, ValueError, OSError, ImportError):
        pass  # the platform refuses: the timeout is the backstop


def _claim_stdout() -> BinaryIO:
    sys.stdout.flush()
    result = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return result


def _test_hook() -> None:
    if os.environ.get("NODO_ENVIRONMENT") != "test":
        return
    hook = os.environ.get("NODO_DOCUMENT_WORKER_TEST_HOOK", "")
    report = os.environ.get("NODO_DOCUMENT_WORKER_TEST_FILE")
    if hook == "sleep":
        time.sleep(60)
    elif hook == "crash":
        os._exit(17)
    elif hook == "spawn" and report:
        child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
        Path(report).write_text(f"{os.getpid()} {child.pid}", encoding="ascii")
        time.sleep(60)
    elif hook == "env" and report:
        Path(report).write_text("\n".join(sorted(os.environ)), encoding="utf-8")
    elif hook == "noise":
        print("text printed by a library")
        os.write(1, b"bytes written by native code")
    elif hook.startswith("alloc:"):
        _ballast.append(bytearray(int(hook.split(":", 1)[1]) * 1024 * 1024))


def _payload(path: Path, extension: str) -> dict[str, object]:
    key = sniff(path, "document" + extension)
    document = PARSERS[key](path)
    return {
        "ok": True,
        "parser": document.parser,
        "blocks": [asdict(block) for block in document.blocks],
        "no_text_layer": document.no_text_layer,
        "warnings": list(document.warnings),
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) not in (1, 2):
        return 2
    extension = args[1].lower() if len(args) == 2 else ""
    if extension and not _EXTENSION.fullmatch(extension):
        extension = ""
    result = _claim_stdout()
    _apply_memory_limit()
    try:
        _test_hook()
        payload = _payload(Path(args[0]), extension)
    except ParseError as exc:
        payload = {"ok": False, "error": exc.code}
    except Exception:  # a parser bug is a damaged file, never a leaked traceback
        payload = {"ok": False, "error": "damaged"}
    result.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
    result.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
