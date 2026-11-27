"""Behaviour every collection store must show (FR-4, FR-8, ADR-030).

The memory and SQLite stores run this suite. Each test class provides the fixtures `collections`
(built with `max_collections=LIMIT`), `sources`, `ws`, `seed_meeting(ws, owner, meeting_id)` and
`tombstone_meeting(ws, meeting_id)`."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from nodo_api.domain.sources import CLEAR, Source, SourceKind
from nodo_api.errors import (
    CycleRefused,
    DepthRefused,
    DuplicateName,
    NotFoundError,
    TooManyCollections,
)

OWNER = "user-a"
OTHER = "user-b"
LIMIT = 20
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


def add_document(sources, ws: str, sid: str, owner: str = OWNER, minutes: int = 0) -> Source:
    return sources.add(
        ws,
        Source(
            id=sid,
            workspace_id=ws,
            owner_id=owner,
            kind=SourceKind.DOCUMENT,
            title=f"title {sid}",
            state="ready",
            created_at=T0 + timedelta(minutes=minutes),
        ),
    )


def tombstone(sources, ws: str, sid: str, owner: str = OWNER) -> None:
    live = sources.get_owned(ws, owner, sid)
    sources.save(ws, replace(live, tombstoned_at=T0 + timedelta(days=1), state="deleting"))


def chain(collections, ws: str, levels: int) -> list[str]:
    """`levels` nested collections, root first."""
    ids: list[str] = []
    for level in range(levels):
        made = collections.create(
            ws, OWNER, name=f"level {level}", parent_id=ids[-1] if ids else None
        )
        ids.append(made.id)
    return ids


class CollectionContract:
    def test_nested_collections_list_parents_first(self, collections, ws) -> None:
        work = collections.create(ws, OWNER, name="Work", parent_id=None)
        beta = collections.create(ws, OWNER, name="beta", parent_id=work.id)
        alpha = collections.create(ws, OWNER, name="Alpha", parent_id=work.id)
        deep = collections.create(ws, OWNER, name="deep", parent_id=beta.id)
        archive = collections.create(ws, OWNER, name="Archive", parent_id=None)

        listed = collections.list_owned(ws, OWNER)

        assert [c.id for c in listed] == [archive.id, work.id, alpha.id, beta.id, deep.id]
        assert {c.id: c.parent_id for c in listed}[deep.id] == beta.id
        assert all(c.owner_id == OWNER and c.workspace_id == ws for c in listed)
        assert collections.get_owned(ws, OWNER, beta.id).name == "beta"

    def test_one_source_in_two_collections_is_listed_in_both(
        self, collections, sources, ws
    ) -> None:
        add_document(sources, ws, "d1")
        a = collections.create(ws, OWNER, name="a", parent_id=None)
        b = collections.create(ws, OWNER, name="b", parent_id=None)
        collections.add_source(ws, OWNER, a.id, "d1")
        collections.add_source(ws, OWNER, b.id, "d1")
        collections.add_source(ws, OWNER, b.id, "d1")  # idempotent

        assert [s.id for s in collections.list_sources(ws, OWNER, a.id, 10, None)] == ["d1"]
        assert [s.id for s in collections.list_sources(ws, OWNER, b.id, 10, None)] == ["d1"]
        assert sorted(collections.collections_of(ws, OWNER, "d1")) == sorted([a.id, b.id])
        assert collections.get_owned(ws, OWNER, b.id).source_count == 1

    def test_list_sources_is_newest_first_with_a_cursor(self, collections, sources, ws) -> None:
        box = collections.create(ws, OWNER, name="box", parent_id=None)
        for minutes, sid in enumerate(("d1", "d2", "d3")):
            add_document(sources, ws, sid, minutes=minutes)
            collections.add_source(ws, OWNER, box.id, sid)

        first = collections.list_sources(ws, OWNER, box.id, 2, None)
        rest = collections.list_sources(ws, OWNER, box.id, 2, (first[-1].created_at, first[-1].id))

        assert [s.id for s in first] == ["d3", "d2"]
        assert [s.id for s in rest] == ["d1"]

    def test_sibling_names_are_unique_case_and_accent_insensitive(self, collections, ws) -> None:
        collections.create(ws, OWNER, name="Città", parent_id=None)
        with pytest.raises(DuplicateName) as caught:
            collections.create(ws, OWNER, name="  citta ", parent_id=None)
        assert caught.value.code == "duplicate_name"
        other = collections.create(ws, OWNER, name="Other", parent_id=None)
        with pytest.raises(DuplicateName):
            collections.update(ws, OWNER, other.id, name="CITTÀ")

    def test_same_name_under_different_parents_is_allowed(self, collections, ws) -> None:
        a = collections.create(ws, OWNER, name="a", parent_id=None)
        b = collections.create(ws, OWNER, name="b", parent_id=None)
        first = collections.create(ws, OWNER, name="notes", parent_id=a.id)
        second = collections.create(ws, OWNER, name="notes", parent_id=b.id)
        assert first.id != second.id
        with pytest.raises(DuplicateName):  # moving one next to the other clashes
            collections.update(ws, OWNER, second.id, parent_id=a.id)

    def test_move_into_own_subtree_is_refused_as_cycle(self, collections, ws) -> None:
        top, middle, leaf = chain(collections, ws, 3)
        with pytest.raises(CycleRefused):
            collections.update(ws, OWNER, top, parent_id=leaf)
        with pytest.raises(CycleRefused):
            collections.update(ws, OWNER, top, parent_id=top)
        assert collections.get_owned(ws, OWNER, top).parent_id is None
        assert collections.get_owned(ws, OWNER, middle).parent_id == top

    def test_seventh_link_eighth_level_is_refused_on_create_and_on_move(
        self, collections, ws
    ) -> None:
        ids = chain(collections, ws, 7)  # six links, seven levels: accepted
        with pytest.raises(DepthRefused) as caught:
            collections.create(ws, OWNER, name="too deep", parent_id=ids[-1])
        assert caught.value.code == "too_deep"

        pair = chain_named(collections, ws, ("x", "y"))  # a root with one child
        with pytest.raises(DepthRefused):
            collections.update(ws, OWNER, pair[0], parent_id=ids[-2])  # 5 + 1 + 1 links
        collections.update(ws, OWNER, pair[1], parent_id=ids[-2])  # 5 + 1 links: fine
        assert collections.get_owned(ws, OWNER, pair[1]).parent_id == ids[-2]

    def test_concurrent_creates_never_exceed_the_limit_or_duplicate_a_sibling_name(
        self, collections, ws
    ) -> None:
        outcomes: list[str] = []
        guard = threading.Lock()
        start = threading.Barrier(LIMIT + 10)

        def make(name: str) -> None:
            start.wait()
            try:
                collections.create(ws, OWNER, name=name, parent_id=None)
                result = "ok"
            except DuplicateName:
                result = "duplicate"
            except TooManyCollections:
                result = "full"
            with guard:
                outcomes.append(result)

        # Ten threads race for one name, the others for distinct names beyond the limit.
        names = ["same"] * 10 + [f"n{i}" for i in range(LIMIT)]
        threads = [threading.Thread(target=make, args=(name,)) for name in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        listed = collections.list_owned(ws, OWNER)
        assert len(listed) == outcomes.count("ok") == LIMIT
        assert [c.name for c in listed].count("same") <= 1
        assert outcomes.count("duplicate") + outcomes.count("full") == 10

    def test_move_to_root_with_clear(self, collections, ws) -> None:
        top, leaf = chain(collections, ws, 2)
        moved = collections.update(ws, OWNER, leaf, parent_id=CLEAR)
        assert moved.parent_id is None
        renamed = collections.update(ws, OWNER, leaf, name="renamed")
        assert (renamed.name, renamed.parent_id) == ("renamed", None)
        assert collections.update(ws, OWNER, leaf, parent_id=top).parent_id == top

    def test_delete_removes_the_subtree_and_memberships_but_never_sources(
        self, collections, sources, ws
    ) -> None:
        top, middle, leaf = chain(collections, ws, 3)
        kept = collections.create(ws, OWNER, name="kept", parent_id=None)
        add_document(sources, ws, "d1")
        add_document(sources, ws, "d2")
        collections.add_source(ws, OWNER, leaf, "d1")
        collections.add_source(ws, OWNER, middle, "d2")
        collections.add_source(ws, OWNER, kept.id, "d2")

        assert collections.delete(ws, OWNER, top) == 3

        assert [c.id for c in collections.list_owned(ws, OWNER)] == [kept.id]
        for gone in (top, middle, leaf):
            with pytest.raises(NotFoundError):
                collections.get_owned(ws, OWNER, gone)
        assert collections.collections_of(ws, OWNER, "d1") == ()
        assert collections.collections_of(ws, OWNER, "d2") == (kept.id,)
        assert sources.get_owned(ws, OWNER, "d1").tombstoned_at is None
        assert sources.get_owned(ws, OWNER, "d2").tombstoned_at is None
        with pytest.raises(NotFoundError):
            collections.delete(ws, OWNER, top)

    def test_add_source_is_owner_scoped_and_refuses_deleted_sources(
        self, collections, sources, ws
    ) -> None:
        mine = collections.create(ws, OWNER, name="mine", parent_id=None)
        theirs = collections.create(ws, OTHER, name="theirs", parent_id=None)
        add_document(sources, ws, "d-other", owner=OTHER)
        add_document(sources, ws, "d-dead")
        tombstone(sources, ws, "d-dead")

        with pytest.raises(NotFoundError):
            collections.add_source(ws, OWNER, mine.id, "d-other")
        with pytest.raises(NotFoundError):
            collections.add_source(ws, OWNER, mine.id, "d-dead")
        with pytest.raises(NotFoundError):
            collections.add_source(ws, OWNER, mine.id, "missing")
        with pytest.raises(NotFoundError):
            collections.add_source(ws, OWNER, theirs.id, "d-other")
        with pytest.raises(NotFoundError):
            collections.get_owned(ws, OWNER, theirs.id)
        with pytest.raises(NotFoundError):
            collections.update(ws, OWNER, theirs.id, name="x")
        with pytest.raises(NotFoundError):
            collections.create(ws, OWNER, name="under theirs", parent_id=theirs.id)
        with pytest.raises(NotFoundError):
            collections.list_sources(ws, OWNER, theirs.id, 10, None)
        assert collections.list_sources(ws, OWNER, mine.id, 10, None) == ()

    def test_remove_source_is_idempotent(self, collections, sources, ws) -> None:
        box = collections.create(ws, OWNER, name="box", parent_id=None)
        add_document(sources, ws, "d1")
        collections.add_source(ws, OWNER, box.id, "d1")
        collections.remove_source(ws, OWNER, box.id, "d1")
        collections.remove_source(ws, OWNER, box.id, "d1")
        assert collections.collections_of(ws, OWNER, "d1") == ()

    def test_meeting_can_be_a_member(self, collections, seed_meeting, ws) -> None:
        seed_meeting(ws, OWNER, "m1")
        box = collections.create(ws, OWNER, name="calls", parent_id=None)
        collections.add_source(ws, OWNER, box.id, "m1")
        listed = collections.list_sources(ws, OWNER, box.id, 10, None)
        assert [(s.id, s.kind) for s in listed] == [("m1", SourceKind.MEETING)]

    def test_forget_source_removes_every_membership(self, collections, sources, ws) -> None:
        add_document(sources, ws, "d1")
        a = collections.create(ws, OWNER, name="a", parent_id=None)
        b = collections.create(ws, OWNER, name="b", parent_id=a.id)
        collections.add_source(ws, OWNER, a.id, "d1")
        collections.add_source(ws, OWNER, b.id, "d1")
        collections.forget_source(ws, "d1")
        collections.forget_source(ws, "d1")
        assert collections.collections_of(ws, OWNER, "d1") == ()
        assert collections.get_owned(ws, OWNER, a.id).source_count == 0

    def test_collections_of_skips_a_source_deleted_without_forget(
        self, collections, sources, ws
    ) -> None:
        add_document(sources, ws, "d1")
        box = collections.create(ws, OWNER, name="box", parent_id=None)
        collections.add_source(ws, OWNER, box.id, "d1")
        tombstone(sources, ws, "d1")  # a path that never calls forget_source
        assert collections.collections_of(ws, OWNER, "d1") == ()
        assert collections.collections_of(ws, OWNER, "missing") == ()

    def test_source_count_ignores_deleted_sources(
        self, collections, sources, seed_meeting, tombstone_meeting, ws
    ) -> None:
        box = collections.create(ws, OWNER, name="box", parent_id=None)
        add_document(sources, ws, "d1")
        add_document(sources, ws, "d2")
        seed_meeting(ws, OWNER, "m1")
        for sid in ("d1", "d2", "m1"):
            collections.add_source(ws, OWNER, box.id, sid)
        tombstone(sources, ws, "d2")
        tombstone_meeting(ws, "m1")

        assert collections.get_owned(ws, OWNER, box.id).source_count == 1
        assert [c.source_count for c in collections.list_owned(ws, OWNER)] == [1]
        assert [s.id for s in collections.list_sources(ws, OWNER, box.id, 10, None)] == ["d1"]

    def test_collection_limit(self, collections, ws) -> None:
        for i in range(LIMIT):
            collections.create(ws, OWNER, name=f"c{i}", parent_id=None)
        with pytest.raises(TooManyCollections) as caught:
            collections.create(ws, OWNER, name="one more", parent_id=None)
        assert caught.value.code == "too_many_collections"
        collections.create(ws, OTHER, name="another owner", parent_id=None)

    def test_workspace_isolation(self, collections, sources, ws) -> None:
        other_ws = f"{ws}-other"
        here = collections.create(ws, OWNER, name="same", parent_id=None)
        there = collections.create(other_ws, OWNER, name="same", parent_id=None)
        add_document(sources, other_ws, "d1")

        with pytest.raises(NotFoundError):
            collections.add_source(ws, OWNER, here.id, "d1")
        with pytest.raises(NotFoundError):
            collections.get_owned(ws, OWNER, there.id)
        with pytest.raises(NotFoundError):
            collections.delete(ws, OWNER, there.id)
        assert [c.id for c in collections.list_owned(ws, OWNER)] == [here.id]
        collections.add_source(other_ws, OWNER, there.id, "d1")
        collections.forget_source(ws, "d1")  # another workspace: no effect there
        assert collections.collections_of(other_ws, OWNER, "d1") == (there.id,)

    def test_bad_names_are_refused_without_echoing_them(self, collections, ws) -> None:
        box = collections.create(ws, OWNER, name="  padded  ", parent_id=None)
        assert box.name == "padded"
        bidi, zero_width = chr(0x202E), chr(0x200B)
        for bad in (
            "",
            "   ",
            "x" * 121,
            "tab\there",
            "nul\x00",
            f"abc{bidi}fdp.exe",
            f"same{zero_width}name",
        ):
            with pytest.raises(ValueError) as caught:
                collections.create(ws, OWNER, name=bad, parent_id=None)
            assert bad.strip() == "" or bad not in str(caught.value)
        assert len(collections.create(ws, OWNER, name="y" * 120, parent_id=None).name) == 120


def chain_named(collections, ws: str, names: tuple[str, ...]) -> list[str]:
    ids: list[str] = []
    for name in names:
        made = collections.create(ws, OWNER, name=name, parent_id=ids[-1] if ids else None)
        ids.append(made.id)
    return ids
