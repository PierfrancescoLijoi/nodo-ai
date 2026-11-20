"""Parent links between concepts and between collections (ADR-030).

One pure rule decides whether a parent link may exist: the non-rejected links always form an
acyclic graph in which no path has more than `MAX_DEPTH` links. A link that would break this is
refused, never repaired. The stores apply the rule inside their write transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

MAX_DEPTH = 6

LinkRefusal = Literal["self", "cycle", "too_deep"]

Step = Callable[[frozenset[str]], frozenset[str]]


def _levels(start: str, step: Step, max_depth: int, stop: str | None) -> int | None:
    """How many non-empty levels lie beyond `start`, or None when `stop` is among them.

    Level by level, without a visited set, so the count is the longest path; a walk still going
    after `max_depth + 1` levels (only corrupt, cyclic data does that) counts as too deep."""
    level = frozenset({start})
    for depth in range(max_depth + 1):
        level = step(level)
        if not level:
            return depth
        if stop is not None and stop in level:
            return None
    return max_depth + 1


def check_link(
    child: str,
    parent: str,
    parents_of: Step,
    children_of: Step,
    *,
    max_depth: int = MAX_DEPTH,
) -> LinkRefusal | None:
    """Why `child` may not be placed under `parent`, or None when it may."""
    if child == parent:
        return "self"
    up = _levels(parent, parents_of, max_depth, stop=child)
    if up is None:
        return "cycle"
    if up + 1 > max_depth:
        return "too_deep"
    down = _levels(child, children_of, max_depth, stop=None) or 0  # no stop: never None
    if up + 1 + down > max_depth:
        return "too_deep"
    return None
