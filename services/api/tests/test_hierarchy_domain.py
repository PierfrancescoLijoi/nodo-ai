"""The one rule that decides whether a parent link may exist (ADR-030)."""

from __future__ import annotations

from nodo_api.domain.hierarchy import MAX_DEPTH, check_link


def graph(*links: tuple[str, str]):
    """Callbacks over (child, parent) pairs."""

    def parents_of(ids: frozenset[str]) -> frozenset[str]:
        return frozenset(p for c, p in links if c in ids)

    def children_of(ids: frozenset[str]) -> frozenset[str]:
        return frozenset(c for c, p in links if p in ids)

    return parents_of, children_of


def chain(prefix: str, length: int) -> list[tuple[str, str]]:
    """`length` links: prefix0 under prefix1 under ... under prefix<length>."""
    return [(f"{prefix}{i}", f"{prefix}{i + 1}") for i in range(length)]


def test_max_depth_is_six() -> None:
    assert MAX_DEPTH == 6


def test_self_link_is_refused() -> None:
    assert check_link("a", "a", *graph()) == "self"


def test_link_closing_a_cycle_is_refused() -> None:
    walk = graph(("a", "b"), ("b", "c"))
    assert check_link("c", "a", *walk) == "cycle"
    assert check_link("b", "a", *walk) == "cycle"


def test_chain_of_six_links_is_accepted_and_a_seventh_is_too_deep() -> None:
    walk = graph(*chain("n", 5))  # n0 .. n5, five links
    assert check_link("n5", "n6", *walk) is None  # the sixth
    walk = graph(*chain("n", 6))
    assert check_link("n6", "n7", *walk) == "too_deep"
    assert check_link("x", "n0", *walk) == "too_deep"  # the same rule from below


def test_joining_chains_counts_both_sides() -> None:
    # u0 under u1 under u2 under u3: three levels above u0; d0 has d1, d2 below it (two levels).
    above = chain("u", 3)
    below = [("d1", "d0"), ("d2", "d1")]
    assert check_link("d0", "u0", *graph(*above, *below)) is None  # 3 + 1 + 2
    deeper = [*below, ("d3", "d2")]
    assert check_link("d0", "u0", *graph(*above, *deeper)) == "too_deep"  # 3 + 1 + 3


def test_several_parents_are_allowed() -> None:
    walk = graph(("a", "b"), ("a", "c"), ("c", "d"))
    assert check_link("a", "e", *walk) is None
    assert check_link("b", "d", *walk) is None  # a diamond, not a cycle


def test_corrupt_cyclic_data_fails_closed() -> None:
    def loop(_ids: frozenset[str]) -> frozenset[str]:
        return frozenset({"x", "y"})  # every node has a parent and a child, forever

    assert check_link("a", "b", loop, loop) == "too_deep"
    calls = []

    def counting(ids: frozenset[str]) -> frozenset[str]:
        calls.append(ids)
        return frozenset({"x"})

    assert check_link("a", "b", counting, counting) is not None
    assert len(calls) <= 2 * (MAX_DEPTH + 1)
