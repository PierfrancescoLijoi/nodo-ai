"""In-memory document parser used by service tests."""

from __future__ import annotations

from pathlib import Path

from nodo_api.domain.documents import ParsedDocument, ParseError


class MemoryDocumentParser:
    def __init__(self) -> None:
        self._results: dict[Path, ParsedDocument | ParseError] = {}

    def register(self, path: Path, document: ParsedDocument) -> None:
        self._results[Path(path)] = document

    def register_error(self, path: Path, error: ParseError) -> None:
        self._results[Path(path)] = error

    def parse(
        self,
        path: Path,
        *,
        timeout_seconds: float | None = None,
        declared_name: str | None = None,
    ) -> ParsedDocument:
        del timeout_seconds, declared_name
        result = self._results.get(Path(path))
        if result is None:
            raise ParseError("damaged")
        if isinstance(result, ParseError):
            raise ParseError(result.code)
        return result
