"""Deterministic small-to-big context selection with one hard token budget."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from nodo_api.domain.sources import Fragment, SectionSummary, estimate_tokens


@dataclass(frozen=True, slots=True)
class Context:
    fragments: tuple[Fragment, ...]
    summaries: tuple[SectionSummary, ...]
    token_count: int


def expand_context(
    matches: Sequence[Fragment],
    source_fragments: Sequence[Fragment],
    summaries: Sequence[SectionSummary],
    budget: int,
) -> Context:
    """Keep each matched quote whole, then its section summary, then nearby siblings in order."""
    if budget < 1:
        raise ValueError("Context budget starts at 1")
    selected: list[Fragment] = []
    selected_keys: set[tuple[str, str]] = set()
    held_summaries: list[SectionSummary] = []
    used = 0

    def add(fragment: Fragment) -> bool:
        nonlocal used
        key = (fragment.source_id, fragment.id)
        tokens = estimate_tokens(fragment.text)
        if key in selected_keys or used + tokens > budget:
            return False
        selected.append(fragment)
        selected_keys.add(key)
        used += tokens
        return True

    # A matched fragment is indivisible: omit it when it alone exceeds the budget rather than
    # manufacture a shortened quote that cannot be cited and validated.
    for fragment in matches:
        add(fragment)
    by_source = sorted(source_fragments, key=lambda f: (f.source_id, f.ordinal, f.id))
    known_summaries = {(s.source_id, s.section_id, s.source_version): s for s in summaries}
    for match in matches:
        if match.section_id is None:
            continue
        summary = known_summaries.get((match.source_id, match.section_id, match.version))
        if (
            summary is not None
            and summary not in held_summaries
            and used + summary.token_count <= budget
        ):
            held_summaries.append(summary)
            used += summary.token_count
        for sibling in by_source:
            if sibling.source_id == match.source_id and sibling.section_id == match.section_id:
                add(sibling)
    return Context(tuple(selected), tuple(held_summaries), used)
