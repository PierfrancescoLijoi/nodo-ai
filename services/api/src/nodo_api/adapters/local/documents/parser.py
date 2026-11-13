"""Document parser port implementation backed by an isolated Python process."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nodo_api.adapters.local.documents.isolation import run_isolated
from nodo_api.domain.documents import PARSE_ERROR_CODES, Block, ParsedDocument, ParseError

_EXTENSION = re.compile(r"\.[a-z0-9]{1,16}")


def _document(payload: Any) -> ParsedDocument:
    if not isinstance(payload, dict):
        raise ParseError("damaged")
    if payload.get("ok") is False:
        code = payload.get("error")
        raise ParseError(code if isinstance(code, str) and code in PARSE_ERROR_CODES else "damaged")
    try:
        blocks = tuple(Block(**item) for item in payload["blocks"])
        parser = payload["parser"]
        no_text_layer = payload["no_text_layer"]
        warnings = tuple(payload["warnings"])
        if (
            not isinstance(parser, str)
            or not isinstance(no_text_layer, bool)
            or not all(isinstance(warning, str) for warning in warnings)
        ):
            raise TypeError
        return ParsedDocument(parser, blocks, no_text_layer=no_text_layer, warnings=warnings)
    except (KeyError, TypeError, ValueError):
        raise ParseError("damaged") from None


@dataclass(frozen=True, slots=True)
class SubprocessDocumentParser:
    """Defaults live in Settings (local_parser_timeout_seconds, local_parser_memory_mb)."""

    timeout_seconds: float
    memory_mb: int

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.memory_mb <= 0:
            raise ValueError("parser timeout and memory limit must be positive")

    def parse(
        self,
        path: Path,
        *,
        timeout_seconds: float | None = None,
        declared_name: str | None = None,
    ) -> ParsedDocument:
        """`declared_name` is the original file name; only its extension reaches the worker."""
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout <= 0:
            raise ValueError("parser timeout must be positive")
        extension = Path(declared_name if declared_name is not None else path.name).suffix.lower()
        args = ["-m", "nodo_api.adapters.local.documents.worker", str(Path(path))]
        if _EXTENSION.fullmatch(extension):
            args.append(extension)
        returncode, stdout = run_isolated(args, timeout=timeout, memory_mb=self.memory_mb)
        if returncode != 0 or not stdout:
            raise ParseError("damaged")
        try:
            payload = json.loads(stdout)
        except (ValueError, RecursionError):
            raise ParseError("damaged") from None
        return _document(payload)
