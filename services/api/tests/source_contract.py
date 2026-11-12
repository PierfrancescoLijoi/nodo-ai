"""Behaviour every source and fragment store must show (ADR-029).

The memory and SQLite stores run this suite. Each test class provides the fixtures `sources`,
`fragments` and `ws`, plus `seed_meeting(ws, owner, meeting_id, segments)`, which stores a meeting
and its transcript through the existing meeting and transcript repositories."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from nodo_api.domain.models import Locale, TranscriptSegment
from nodo_api.domain.sources import Fragment, Locator, Section, Source, SourceKind
from nodo_api.errors import NotFoundError

OWNER = "user-a"
OTHER = "user-b"
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)

K = SourceKind


def source(
    sid: str,
    kind: SourceKind = K.DOCUMENT,
    owner: str = OWNER,
    minutes: int = 0,
    ws: str = "w",
    **extra,
) -> Source:
    return Source(
        id=sid,
        workspace_id=ws,
        owner_id=owner,
        kind=kind,
        title=f"title {sid}",
        state="ready",
        created_at=T0 + timedelta(minutes=minutes),
        **extra,
    )


def fragment(
    fid: str, sid: str, ordinal: int, text: str = "some text", section: str | None = None
) -> Fragment:
    return Fragment(
        id=fid,
        source_id=sid,
        section_id=section,
        ordinal=ordinal,
        text=text,
        locator=Locator(page=ordinal + 1, chars=(0, len(text))),
        version=1,
        speaker_id=None,
        confidence=0.9,
        locale=Locale.EN,
    )


def section(sec: str, sid: str, ordinal: int, parent: str | None = None, level: int = 1) -> Section:
    return Section(sec, sid, parent, ordinal, level, f"heading {sec}")


def segment(meeting_id: str, sid: str, start: int, text: str) -> TranscriptSegment:
    return TranscriptSegment(
        sid, meeting_id, "S1", start, start + 900, text, 0.8, 1, Locale.EN, "NODO"
    )


class SourceContract:
    def test_add_then_get_owned(self, sources, ws) -> None:
        added = sources.add(
            ws,
            source(
                "d1",
                ws=ws,
                origin="handbook.pdf",
                language=Locale.IT,
                content_hash="abc",
                version=1,
            ),
        )
        got = sources.get_owned(ws, OWNER, "d1")
        assert got == added
        assert got.kind is K.DOCUMENT
        assert got.language is Locale.IT
        assert sources.get_for_processing(ws, "d1") == got

    def test_other_user_gets_not_found(self, sources, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        with pytest.raises(NotFoundError) as other:
            sources.get_owned(ws, OTHER, "d1")
        with pytest.raises(NotFoundError) as missing:
            sources.get_owned(ws, OWNER, "nope")
        # a source that exists and one that does not look the same from outside
        assert type(other.value) is type(missing.value)
        assert str(other.value) == str(missing.value)
        assert other.value.public_message == missing.value.public_message

    def test_list_excludes_tombstoned_and_filters_kind(self, sources, ws) -> None:
        sources.add(ws, source("d1", K.DOCUMENT, minutes=1, ws=ws))
        sources.add(ws, source("n1", K.NOTE, minutes=2, ws=ws))
        sources.add(ws, source("w1", K.WEB, minutes=3, ws=ws))
        sources.add(ws, source("other", K.NOTE, owner=OTHER, minutes=4, ws=ws))
        gone = sources.add(ws, source("d2", K.DOCUMENT, minutes=5, ws=ws))
        sources.save(ws, _tombstoned(gone))

        everything = sources.list_owned(ws, OWNER, 10, None)
        assert [s.id for s in everything] == ["w1", "n1", "d1"]
        only_notes = sources.list_owned(ws, OWNER, 10, None, frozenset({K.NOTE}))
        assert [s.id for s in only_notes] == ["n1"]
        two = sources.list_owned(ws, OWNER, 10, None, frozenset({K.NOTE, K.DOCUMENT}))
        assert [s.id for s in two] == ["n1", "d1"]
        # a deleted source is gone for reads, unless the caller asks for it
        with pytest.raises(NotFoundError):
            sources.get_owned(ws, OWNER, "d2")
        with pytest.raises(NotFoundError):
            sources.get_for_processing(ws, "d2")
        kept = sources.get_owned(ws, OWNER, "d2", include_tombstoned=True)
        assert kept.tombstoned_at is not None

    def test_list_pages_with_cursor(self, sources, ws) -> None:
        for n in range(5):
            sources.add(ws, source(f"d{n}", minutes=n, ws=ws))
        # same instant: the id breaks the tie, newest and highest id first
        sources.add(ws, source("tie-a", minutes=4, ws=ws))

        seen: list[str] = []
        cursor = None
        while page := sources.list_owned(ws, OWNER, 2, cursor):
            assert len(page) <= 2
            seen.extend(s.id for s in page)
            cursor = (page[-1].created_at, page[-1].id)
        assert seen == ["tie-a", "d4", "d3", "d2", "d1", "d0"]

    def test_add_rejects_meeting_kind(self, sources, ws) -> None:
        with pytest.raises(ValueError):
            sources.add(ws, source("m1", K.MEETING, ws=ws))
        with pytest.raises(ValueError):
            sources.save(ws, source("m1", K.MEETING, ws=ws))

    def test_add_rejects_duplicate_id(self, sources, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        with pytest.raises(ValueError):
            sources.add(ws, source("d1", ws=ws))

    def test_save_updates_fields_but_not_owner_or_version(self, sources, ws, fragments) -> None:
        sources.add(ws, source("d1", ws=ws))
        fragments.add_version(ws, "d1", (), (fragment("f1", "d1", 0),))
        edited = source("d1", owner=OTHER, ws=ws, version=1)
        sources.save(ws, replace(edited, title="new title", state="failed", content_hash="h2"))
        got = sources.get_owned(ws, OWNER, "d1")
        assert (got.title, got.state, got.content_hash) == ("new title", "failed", "h2")
        assert got.owner_id == OWNER
        with pytest.raises(NotFoundError):
            sources.save(ws, source("missing", ws=ws))

    def test_meetings_appear_as_sources(self, sources, seed_meeting, ws) -> None:
        seed_meeting(ws, OWNER, "m1", (segment("m1", "s1", 0, "hello"),), minutes=10)
        seed_meeting(ws, OTHER, "m2", (), minutes=11)
        sources.add(ws, source("d1", minutes=5, ws=ws))

        got = sources.get_owned(ws, OWNER, "m1")
        assert got.kind is K.MEETING and got.id == "m1" and got.owner_id == OWNER
        assert sources.get_for_processing(ws, "m1") == got
        listed = sources.list_owned(ws, OWNER, 10, None)
        assert [(s.id, s.kind) for s in listed] == [("m1", K.MEETING), ("d1", K.DOCUMENT)]
        assert [s.id for s in sources.list_owned(ws, OWNER, 10, None, frozenset({K.DOCUMENT}))] == [
            "d1"
        ]
        with pytest.raises(NotFoundError):
            sources.get_owned(ws, OWNER, "m2")


class FragmentContract:
    def test_add_version_returns_incrementing_versions(self, sources, fragments, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        assert fragments.add_version(ws, "d1", (), (fragment("f1", "d1", 0),)) == 1
        assert fragments.add_version(ws, "d1", (), (fragment("f1", "d1", 0, "second"),)) == 2
        assert fragments.add_version(ws, "d1", (), (fragment("f1", "d1", 0, "third"),)) == 3
        # the source row follows the newest version
        assert sources.get_owned(ws, OWNER, "d1").version == 3

    def test_latest_returns_newest_version_in_order(self, sources, fragments, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        fragments.add_version(ws, "d1", (), (fragment("old", "d1", 0, "old text"),))
        fragments.add_version(
            ws,
            "d1",
            (),
            (
                fragment("c", "d1", 2, "third"),
                fragment("a", "d1", 0, "first"),
                fragment("b", "d1", 1, "second"),
            ),
        )
        got = fragments.latest(ws, "d1")
        assert [f.id for f in got] == ["a", "b", "c"]
        assert [f.version for f in got] == [2, 2, 2]  # the store stamps the version it assigns
        assert got[0].locator == Locator(page=1, chars=(0, 5))
        assert fragments.latest(ws, "unknown") == ()

    def test_get_fragment_by_id_and_missing_is_none(self, sources, fragments, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        fragments.add_version(
            ws, "d1", (), (fragment("f1", "d1", 0, "one"), fragment("f2", "d1", 1, "two"))
        )
        got = fragments.get_fragment(ws, "d1", "f2")
        assert got is not None and got.text == "two" and got.ordinal == 1
        assert fragments.get_fragment(ws, "d1", "nope") is None
        assert fragments.get_fragment(ws, "unknown", "f1") is None

    def test_outline_is_ordered_tree(self, sources, fragments, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        shuffled = (
            section("s2b", "d1", 1, "s2", 2),
            section("s2", "d1", 1),
            section("s1", "d1", 0),
            section("s2a", "d1", 0, "s2", 2),
            section("s1a", "d1", 0, "s1", 2),
        )
        fragments.add_version(
            ws, "d1", shuffled, (fragment("f1", "d1", 0, section="s1a"), fragment("f2", "d1", 1))
        )
        assert [s.id for s in fragments.outline(ws, "d1")] == ["s1", "s1a", "s2", "s2a", "s2b"]
        assert fragments.outline(ws, "unknown") == ()
        got = fragments.latest(ws, "d1")
        assert got[0].section_id == "s1a" and got[1].section_id is None

    def test_add_version_rejects_inconsistent_input(self, sources, fragments, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        sources.add(ws, source("d2", ws=ws))
        good = fragment("f1", "d1", 0)
        for sections, frags in (
            ((), ()),  # no fragments
            ((), (fragment("f1", "d2", 0),)),  # another source
            ((section("s1", "d2", 0),), (good,)),  # section of another source
            ((), (fragment("f1", "d1", 0, section="nope"),)),  # unknown section
            ((section("s1", "d1", 0, "ghost"),), (good,)),  # parent missing
            ((section("a", "d1", 0, "b"), section("b", "d1", 0, "a")), (good,)),  # loop
            ((section("s1", "d1", 0), section("s1", "d1", 1)), (good,)),  # duplicate section
            ((), (good, fragment("f1", "d1", 1))),  # duplicate fragment
        ):
            with pytest.raises(ValueError):
                fragments.add_version(ws, "d1", sections, frags)
        # nothing was written by the failures
        assert fragments.latest(ws, "d1") == ()
        assert sources.get_owned(ws, OWNER, "d1").version == 1
        with pytest.raises(NotFoundError):
            fragments.add_version(ws, "unknown", (), (fragment("f1", "unknown", 0),))

    def test_erase_removes_fragments_and_sections(self, sources, fragments, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        sources.add(ws, source("d2", ws=ws))
        for sid in ("d1", "d2"):
            fragments.add_version(
                ws, sid, (section("s1", sid, 0),), (fragment("f1", sid, 0, section="s1"),)
            )
        fragments.erase(ws, "d1")
        assert fragments.latest(ws, "d1") == ()
        assert fragments.outline(ws, "d1") == ()
        assert fragments.get_fragment(ws, "d1", "f1") is None
        assert len(fragments.latest(ws, "d2")) == 1  # a neighbour is untouched
        assert sources.get_owned(ws, OWNER, "d1").id == "d1"  # the source itself stays
        fragments.erase(ws, "d1")  # erasing twice is fine

    def test_meeting_source_reads_transcript_as_fragments(
        self, sources, fragments, seed_meeting, ws
    ) -> None:
        seed_meeting(
            ws,
            OWNER,
            "m1",
            (segment("m1", "s1", 0, "good morning"), segment("m1", "s2", 1000, "let us start")),
        )
        got = fragments.latest(ws, "m1")
        assert [f.id for f in got] == ["s1", "s2"]
        assert [f.ordinal for f in got] == [0, 1]
        assert [f.source_id for f in got] == ["m1", "m1"]
        assert [f.locator.time_ms for f in got] == [(0, 900), (1000, 1900)]
        assert got[0].text == "good morning" and got[0].speaker_id == "S1"
        second = fragments.get_fragment(ws, "m1", "s2")
        assert second == got[1]
        assert fragments.get_fragment(ws, "m1", "nope") is None
        assert fragments.outline(ws, "m1") == ()
        assert sources.get_owned(ws, OWNER, "m1").kind is K.MEETING
        # a meeting is written by the transcript port, never through this one
        with pytest.raises(ValueError):
            fragments.add_version(ws, "m1", (), (fragment("x", "m1", 0),))
        with pytest.raises(ValueError):
            fragments.erase(ws, "m1")
        assert len(fragments.latest(ws, "m1")) == 2

    def test_meeting_without_transcript_has_no_fragments(self, fragments, seed_meeting, ws) -> None:
        seed_meeting(ws, OWNER, "m1", ())
        assert fragments.latest(ws, "m1") == ()
        assert fragments.get_fragment(ws, "m1", "s1") is None

    def test_workspace_isolation(self, sources, fragments, seed_meeting, ws) -> None:
        other = f"{ws}-other"
        sources.add(ws, source("d1", ws=ws))
        fragments.add_version(ws, "d1", (section("s1", "d1", 0),), (fragment("f1", "d1", 0),))
        seed_meeting(ws, OWNER, "m1", (segment("m1", "s1", 0, "hello"),))

        with pytest.raises(NotFoundError):
            sources.get_owned(other, OWNER, "d1")
        with pytest.raises(NotFoundError):
            sources.get_for_processing(other, "d1")
        with pytest.raises(NotFoundError):
            sources.get_owned(other, OWNER, "m1")
        assert sources.list_owned(other, OWNER, 10, None) == ()
        assert fragments.latest(other, "d1") == ()
        assert fragments.latest(other, "m1") == ()
        assert fragments.get_fragment(other, "d1", "f1") is None
        assert fragments.outline(other, "d1") == ()
        # the same id can be used again in another workspace
        sources.add(other, source("d1", ws=other))
        assert fragments.add_version(other, "d1", (), (fragment("f1", "d1", 0, "elsewhere"),)) == 1
        assert fragments.latest(ws, "d1")[0].text == "some text"
        fragments.erase(other, "d1")
        assert len(fragments.latest(ws, "d1")) == 1


def _tombstoned(value: Source) -> Source:
    return replace(value, tombstoned_at=T0 + timedelta(days=1))
