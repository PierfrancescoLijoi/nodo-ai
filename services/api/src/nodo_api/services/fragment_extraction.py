"""Extraction over the fragments of a document.

A document is cut into windows of whole fragments that stay inside one section when they can, each
window is extracted on its own, and the results are merged like the windows of a long meeting. A
window the model can not answer is halved and retried; if nothing at all could be extracted the
error is raised, so the import fails with a code instead of finishing empty.

Nothing here reads or logs document text: logs carry counts and error codes only."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from nodo_api.domain.graph import DocumentExtraction, Entity, Extraction, ExtractionError
from nodo_api.domain.models import Locale
from nodo_api.domain.sources import Fragment, Section
from nodo_api.ports import FragmentExtractor
from nodo_api.services.extraction import CHARS_PER_TOKEN, MAX_SPLITS, merge, windows

logger = logging.getLogger("nodo_extraction")

# Bump it with any change of the document prompt or schema (a test pins both): it is stored in
# `source_versions.extraction_version` so an older extraction can be told from a newer one.
EXTRACTION_VERSION = "doc-1"


def extraction_version(model: str) -> str:
    """The string kept with a source version: the prompt version and the model that answered."""
    return f"{EXTRACTION_VERSION}|{model}"


@dataclass(frozen=True, slots=True)
class Window:
    section_path: tuple[str, ...]  # titles from the outermost heading down; () without a section
    fragments: tuple[Fragment, ...]


def _section_paths(sections: Sequence[Section]) -> dict[str, tuple[str, ...]]:
    by_id = {s.id: s for s in sections}
    paths: dict[str, tuple[str, ...]] = {}
    for section in sections:
        titles: list[str] = []
        seen: set[str] = set()
        current: Section | None = section
        while current is not None and current.id not in seen:  # a cycle ends the walk
            seen.add(current.id)
            titles.append(current.title)
            current = by_id.get(current.parent_id) if current.parent_id else None
        paths[section.id] = tuple(reversed(titles))
    return paths


def _common_prefix(paths: Sequence[tuple[str, ...]]) -> tuple[str, ...]:
    prefix = paths[0]
    for path in paths[1:]:
        shared = 0
        for a, b in zip(prefix, path, strict=False):
            if a != b:
                break
            shared += 1
        prefix = prefix[:shared]
    return prefix


def _weight(fragment: Fragment) -> int:
    return len(fragment.text) + 12  # the same weight `windows` gives a piece of text


def windows_for_sections(
    sections: Sequence[Section], fragments: Sequence[Fragment], max_tokens: int
) -> tuple[Window, ...]:
    """Windows of consecutive fragments in reading order.

    A section that weighs a quarter window or more is split on its own, by consecutive fragments,
    and never shares a window. Smaller neighbouring sections are packed together up to one window;
    a shared window is labelled with the breadcrumb they have in common."""
    paths = _section_paths(sections)
    quarter = int(max_tokens * CHARS_PER_TOKEN) // 4
    budget = int(max_tokens * CHARS_PER_TOKEN)
    runs: list[tuple[str | None, list[Fragment]]] = []
    for fragment in sorted(fragments, key=lambda f: (f.ordinal, f.id)):
        if runs and runs[-1][0] == fragment.section_id:
            runs[-1][1].append(fragment)
        else:
            runs.append((fragment.section_id, [fragment]))

    out: list[Window] = []
    pending: list[Fragment] = []
    pending_paths: list[tuple[str, ...]] = []
    used = 0

    def flush() -> None:
        nonlocal pending, pending_paths, used
        if pending:
            out.append(Window(_common_prefix(pending_paths), tuple(pending)))
        pending, pending_paths, used = [], [], 0

    for section_id, run in runs:
        path = paths.get(section_id, ()) if section_id else ()
        weight = sum(_weight(f) for f in run)
        if weight >= quarter:
            flush()
            out.extend(Window(path, part) for part in windows(run, max_tokens))
            continue
        if pending and used + weight > budget:
            flush()
        pending.extend(run)
        pending_paths.append(path)
        used += weight
    flush()
    return tuple(out)


def _one_source(fragments: Sequence[Fragment]) -> None:
    if len({f.source_id for f in fragments}) > 1:
        raise ValueError("Fragments of more than one source can not be extracted together")


class ChunkedFragmentExtractor:
    """Windows a document, halves a window the model can not answer, and merges the parts.

    A model that fails on one dense stretch must not cost the whole document: the stretch is split
    and retried. What still fails is left out, and `extract_document` says so (counts and fragment
    ids), because a document is not re-extracted when the same bytes are imported again."""

    def __init__(self, inner: FragmentExtractor, max_tokens: int) -> None:
        self._inner = inner
        self._max_tokens = max_tokens

    def extract(
        self,
        fragments: tuple[Fragment, ...],
        section_path: tuple[str, ...],
        locale: Locale,
        known_decisions: tuple[Entity, ...] = (),
    ) -> Extraction:
        """Fragments sharing one breadcrumb. All or nothing: a part that could not be read raises
        `extraction_partial`, since this form has nowhere to say what was skipped."""
        _one_source(fragments)
        result = self._run(
            [Window(section_path, part) for part in windows(fragments, self._max_tokens)],
            locale,
            known_decisions,
        )
        if result.skipped_windows:
            raise ExtractionError(
                "Part of the fragments could not be extracted", "extraction_partial"
            )
        return result.extraction

    def extract_document(
        self,
        sections: Sequence[Section],
        fragments: Sequence[Fragment],
        locale: Locale,
        known_decisions: tuple[Entity, ...] = (),
    ) -> DocumentExtraction:
        """No fragments gives an empty extraction. Raises only when there were windows and none
        of them could be extracted; a partial result comes back with its skipped counts."""
        _one_source(fragments)
        return self._run(
            list(windows_for_sections(sections, fragments, self._max_tokens)),
            locale,
            known_decisions,
        )

    def _run(
        self, parts: Sequence[Window], locale: Locale, known: tuple[Entity, ...]
    ) -> DocumentExtraction:
        if not parts:
            return DocumentExtraction(Extraction(), 0, 0, ())
        failures: list[str] = []
        skipped: list[str] = []
        done: list[Extraction] = []
        lossy_windows = 0
        for part in parts:
            before = len(skipped)
            result = self._window(
                part.fragments, part.section_path, locale, known, 0, failures, skipped
            )
            if result is not None:
                done.append(result)
            lossy_windows += len(skipped) > before
        if not done:
            code = failures[-1] if failures else "extraction_failed"
            raise ExtractionError("No part of the document could be extracted", code=code)
        merged = done[0] if len(done) == 1 else merge(done)
        return DocumentExtraction(merged, len(parts), lossy_windows, tuple(skipped))

    def _window(
        self,
        fragments: tuple[Fragment, ...],
        path: tuple[str, ...],
        locale: Locale,
        known: tuple[Entity, ...],
        depth: int,
        failures: list[str],
        skipped: list[str],
    ) -> Extraction | None:
        try:
            return self._inner.extract(fragments, path, locale, known)
        except ExtractionError as error:
            failures.append(error.code)
            if len(fragments) < 2 or depth >= MAX_SPLITS:
                logger.warning(
                    "extraction_window_skipped fragments=%s depth=%s code=%s",
                    len(fragments),
                    depth,
                    error.code,
                )
                skipped.extend(f.id for f in fragments)
                return None
        middle = len(fragments) // 2
        halves = [
            self._window(half, path, locale, known, depth + 1, failures, skipped)
            for half in (fragments[:middle], fragments[middle:])
        ]
        done = [h for h in halves if h is not None]
        return merge(done) if done else None
