"""Extraction over the fragments of a document.

A document is cut into windows of whole fragments that stay inside one section when they can, each
window is extracted on its own, and the results are merged like the windows of a long meeting. A
window the model can not answer is halved and retried; if nothing at all could be extracted the
error is raised, so the import fails with a code instead of finishing empty.

Nothing here reads or logs document text: logs carry counts and error codes only."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from nodo_api.domain.graph import (
    EXISTING_PREFIX,
    MAX_LABEL,
    DocumentExtraction,
    Entity,
    EntityType,
    Extraction,
    ExtractionError,
    normalize_label,
)
from nodo_api.domain.models import Locale
from nodo_api.domain.sources import Fragment, Section
from nodo_api.ports import FragmentExtractor
from nodo_api.services.extraction import CHARS_PER_TOKEN, MAX_SPLITS, merge, windows

logger = logging.getLogger("nodo_extraction")

# Bump it with any change of the document prompt or schema (a test pins both): it is stored in
# `source_versions.extraction_version` so an older extraction can be told from a newer one.
EXTRACTION_VERSION = "doc-3"


def extraction_version(model: str) -> str:
    """The string kept with a source version: the prompt version and the model that answered."""
    return f"{EXTRACTION_VERSION}|{model}"


# Keys the model makes up rather than copies from the text: e1, p2, person, speaker 3, entity_4.
_PLACEHOLDER_KEY = re.compile(
    r"[a-z]{1,3}[ _-]?[0-9]+|(person|people|speaker|entity|name)[ _-]?[0-9]*"
)


def _has_words(words: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(words)}(?!\w)", text) is not None


def repair_keys(extraction: Extraction, fragments: Sequence[Fragment]) -> Extraction:
    """Keys and person labels the document model got wrong, fixed before windows are merged.

    A person labelled with a role ("Project lead") and keyed with the name the text holds takes
    the name as its label; a label found in the cited text is never touched. A key used again
    is made unique (`key#2`, `key#3`, ...) so validation keeps every entity, and a relation on
    such a key is dropped because it can not say which entity it meant; an exact repeat of the
    first holder (same type and label) is not a second entity and is left for validation to drop.
    The key must appear as whole words, and a placeholder key (`e1`, `person`) is never taken as a
    name. `existing:` keys are left as they are."""
    texts = {f.id: normalize_label(f.text) for f in fragments}
    used = {e.key for e in extraction.entities}
    seen: dict[str, tuple[EntityType, str]] = {}
    duplicated: set[str] = set()
    entities = []
    for entity in extraction.entities:
        if entity.type is EntityType.PERSON:
            cited = [texts[i] for i in entity.evidence if i in texts]
            label, key = normalize_label(entity.label), normalize_label(entity.key)
            if (
                key
                and not _PLACEHOLDER_KEY.fullmatch(key)
                and not any(label in t for t in cited)
                and any(_has_words(key, t) for t in cited)
            ):
                entity = replace(entity, label=" ".join(entity.key.split())[:MAX_LABEL])
        identity = (entity.type, normalize_label(entity.label))
        if entity.key.startswith(EXISTING_PREFIX) or seen.get(entity.key) == identity:
            pass
        elif entity.key in seen:
            duplicated.add(entity.key)
            n = 2
            while f"{entity.key}#{n}" in used:
                n += 1
            used.add(f"{entity.key}#{n}")
            entity = replace(entity, key=f"{entity.key}#{n}")
        else:
            seen[entity.key] = identity
        entities.append(entity)
    relations = tuple(
        r for r in extraction.relations if r.source not in duplicated and r.target not in duplicated
    )
    return replace(extraction, entities=tuple(entities), relations=relations)


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
            return repair_keys(self._inner.extract(fragments, path, locale, known), fragments)
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
