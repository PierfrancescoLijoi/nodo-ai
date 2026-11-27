"""Behaviour every fragment index must show (Task 8).

The memory and SQLite indexes run this suite. Each test class provides the fixtures `index` (the
fragment index), `sources` and `fragments` (the repositories the index consults), `retriever` (the
meeting retriever that lives beside it) and `ws`."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from nodo_api.domain.models import Locale, TranscriptSegment
from nodo_api.domain.sources import MAX_EMBEDDING_TOKENS, Fragment, IndexResult, Locator
from tests.source_contract import source

OWNER = "user-a"
OTHER = "user-b"

HOSTILE = (
    '"',
    '""" OR 1=1 --',
    "budget NEAR/2 forecast",
    "NEAR(budget forecast, 1)",
    "text:budget",
    "{text}: budget",
    "- budget",
    "budget*",
    "*",
    "^budget",
    "budget AND NOT forecast",
    "budget OR",
    "(budget",
    "'; DROP TABLE search_fragments; --",
    "Zażółć gęślą jaźń résumé 日本語 ✓",
    "",
    "   ",
)


def frag(fid: str, source_id: str, ordinal: int, text: str, version: int = 1) -> Fragment:
    return Fragment(
        id=fid,
        source_id=source_id,
        section_id=None,
        ordinal=ordinal,
        text=text,
        locator=Locator(page=ordinal + 1, chars=(0, len(text))),
        version=version,
        speaker_id=None,
        confidence=0.9,
        locale=Locale.EN,
    )


BUDGET = "quarterly budget forecast approved by finance"
KITCHEN = "office kitchen cleaning rota for december"
SERVER = "server migration schedule delayed again"


def ids(hits) -> list[str]:
    return [h.fragment.id for h in hits]


def add_source(sources, ws: str, source_id: str, owner: str = OWNER):
    return sources.add(ws, source(source_id, ws=ws, owner=owner))


def set_version(fragments, ws: str, source_id: str, version: int) -> None:
    """Bring a fresh source to `version` the way imports do: by storing versions."""
    for _ in range(version):
        fragments.add_version(
            ws, source_id, (), (frag("placeholder", source_id, 0, "placeholder"),)
        )


class FragmentIndexContract:
    def seed(self, index, sources, ws: str) -> None:
        add_source(sources, ws, "d1")
        index.index(
            ws,
            OWNER,
            "d1",
            [
                frag("f1", "d1", 0, BUDGET),
                frag("f2", "d1", 1, KITCHEN),
                frag("f3", "d1", 2, SERVER),
            ],
        )

    def test_index_then_search_finds_the_fragment(self, index, sources, ws) -> None:
        add_source(sources, ws, "d1")
        result = index.index(
            ws,
            OWNER,
            "d1",
            [
                frag("f1", "d1", 0, BUDGET),
                frag("f2", "d1", 1, KITCHEN),
                frag("f3", "d1", 2, SERVER),
            ],
        )
        assert result == IndexResult(indexed=3, truncated=0, skipped=None)
        hits = index.search(ws, OWNER, "budget forecast", 5)
        assert ids(hits)[0] == "f1"
        assert hits[0].score > 0
        assert hits[0].fragment == frag("f1", "d1", 0, BUDGET)  # every field comes back
        assert index.count_source(ws, "d1") == 3

    def test_results_are_best_first_and_limited(self, index, sources, ws) -> None:
        self.seed(index, sources, ws)
        hits = index.search(ws, OWNER, "budget forecast finance", 5)
        assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
        assert len(index.search(ws, OWNER, "budget forecast finance", 1)) == 1
        assert index.search(ws, OWNER, "budget forecast finance", 0) == ()

    def test_search_is_owner_and_workspace_scoped(self, index, sources, ws) -> None:
        self.seed(index, sources, ws)
        add_source(sources, ws, "d2", OTHER)
        add_source(sources, "elsewhere", "d3")
        index.index(ws, OTHER, "d2", [frag("g1", "d2", 0, "budget forecast of someone else")])
        index.index("elsewhere", OWNER, "d3", [frag("h1", "d3", 0, "budget forecast elsewhere")])
        assert set(ids(index.search(ws, OWNER, "budget forecast", 10))) <= {"f1", "f2", "f3"}
        assert ids(index.search(ws, OTHER, "budget forecast", 10)) == ["g1"]
        assert ids(index.search("elsewhere", OWNER, "budget forecast", 10)) == ["h1"]
        assert index.search("nowhere", OWNER, "budget forecast", 10) == ()
        assert index.search(ws, "nobody", "budget forecast", 10) == ()
        assert index.count_source(ws, "d2") == 1
        assert index.count_source("elsewhere", "d1") == 0

    def test_a_source_id_in_another_workspace_is_a_different_source(
        self, index, sources, ws
    ) -> None:
        add_source(sources, ws, "d1")
        add_source(sources, "elsewhere", "d1")
        index.index(ws, OWNER, "d1", [frag("f1", "d1", 0, BUDGET)])
        index.index("elsewhere", OWNER, "d1", [frag("f1", "d1", 0, KITCHEN)])
        index.erase_source("elsewhere", "d1")
        assert ids(index.search(ws, OWNER, "budget forecast", 5)) == ["f1"]

    def test_reindex_replaces_the_previous_version_rows(
        self, index, sources, fragments, ws
    ) -> None:
        self.seed(index, sources, ws)
        set_version(fragments, ws, "d1", 2)
        result = index.index(
            ws,
            OWNER,
            "d1",
            [
                frag("f1", "d1", 0, BUDGET, version=2),
                frag("f4", "d1", 1, "brand new paragraph about gardening", version=2),
            ],
        )
        assert result.indexed == 2 and result.skipped is None
        assert index.count_source(ws, "d1") == 2
        # f2 and f3 are gone (the SQLite index may still return its nearest neighbours: f1, f4)
        assert not {"f2", "f3"} & set(ids(index.search(ws, OWNER, "kitchen cleaning rota", 5)))
        assert not {"f2", "f3"} & set(ids(index.search(ws, OWNER, "server migration", 5)))
        assert ids(index.search(ws, OWNER, "gardening", 5)) == ["f4"]
        kept = index.search(ws, OWNER, "budget forecast", 5)
        assert [h.fragment.version for h in kept] == [2]  # one row for f1, the new one

    def test_reindexing_the_same_version_twice_leaves_no_duplicate(
        self, index, sources, ws
    ) -> None:
        self.seed(index, sources, ws)
        again = [
            frag("f1", "d1", 0, BUDGET),
            frag("f2", "d1", 1, KITCHEN),
            frag("f3", "d1", 2, SERVER),
        ]
        index.index(ws, OWNER, "d1", again)
        assert index.count_source(ws, "d1") == 3
        assert ids(index.search(ws, OWNER, "budget forecast", 10)).count("f1") == 1

    def test_reindexing_with_nothing_clears_the_source(self, index, sources, ws) -> None:
        self.seed(index, sources, ws)
        result = index.index(ws, OWNER, "d1", [])
        assert result == IndexResult(indexed=0, truncated=0, skipped=None)
        assert index.count_source(ws, "d1") == 0
        assert index.search(ws, OWNER, "budget forecast", 5) == ()

    def test_a_source_that_changes_owner_is_no_longer_found_by_the_old_one(
        self, index, sources, ws
    ) -> None:
        self.seed(index, sources, ws)
        index.index(ws, OTHER, "d1", [frag("f1", "d1", 0, BUDGET)])
        assert index.search(ws, OWNER, "budget forecast", 5) == ()
        assert ids(index.search(ws, OTHER, "budget forecast", 5)) == ["f1"]
        assert index.count_source(ws, "d1") == 1

    def test_erase_source_removes_text_and_vectors(self, index, sources, ws) -> None:
        self.seed(index, sources, ws)
        add_source(sources, ws, "d2")
        index.index(ws, OWNER, "d2", [frag("g1", "d2", 0, "budget forecast in another file")])
        index.erase_source(ws, "d1")
        assert index.count_source(ws, "d1") == 0
        assert ids(index.search(ws, OWNER, "budget forecast", 10)) == ["g1"]
        assert "f2" not in ids(index.search(ws, OWNER, "kitchen cleaning", 10))
        index.erase_source(ws, "d1")  # erasing twice, or an unknown source, is not an error
        index.erase_source(ws, "never-indexed")

    def test_source_filter_limits_results(self, index, sources, ws) -> None:
        for sid in ("d1", "d2", "d3"):
            add_source(sources, ws, sid)
        index.index(ws, OWNER, "d1", [frag("f1", "d1", 0, BUDGET)])
        index.index(ws, OWNER, "d2", [frag("g1", "d2", 0, "budget forecast second file")])
        index.index(ws, OWNER, "d3", [frag("h1", "d3", 0, "budget forecast third file")])
        assert set(ids(index.search(ws, OWNER, "budget forecast", 10))) == {"f1", "g1", "h1"}
        assert ids(
            index.search(ws, OWNER, "budget forecast", 10, source_ids=frozenset({"d2"}))
        ) == ["g1"]
        both = index.search(ws, OWNER, "budget forecast", 10, source_ids=frozenset({"d1", "d3"}))
        assert set(ids(both)) == {"f1", "h1"}
        assert index.search(ws, OWNER, "budget forecast", 10, source_ids=frozenset()) == ()
        assert index.search(ws, OWNER, "budget forecast", 10, source_ids=frozenset({"zz"})) == ()

    def test_source_filter_cannot_reach_another_owners_source(self, index, sources, ws) -> None:
        add_source(sources, ws, "d9", OTHER)
        index.index(ws, OTHER, "d9", [frag("x1", "d9", 0, "budget forecast not yours")])
        hits = index.search(ws, OWNER, "budget forecast", 10, source_ids=frozenset({"d9"}))
        assert hits == ()

    # --- a source that is gone, deleted or superseded takes no rows ---------------------------

    def test_a_tombstoned_source_takes_no_rows(self, index, sources, ws) -> None:
        created = add_source(sources, ws, "d1")
        sources.save(ws, replace(created, tombstoned_at=datetime.now(UTC)))
        result = index.index(ws, OWNER, "d1", [frag("f1", "d1", 0, BUDGET)])
        assert result == IndexResult(indexed=0, truncated=0, skipped="source_gone")
        assert index.count_source(ws, "d1") == 0
        assert index.search(ws, OWNER, "budget forecast", 5) == ()

    def test_a_source_that_does_not_exist_takes_no_rows(self, index, ws) -> None:
        result = index.index(ws, OWNER, "ghost", [frag("f1", "ghost", 0, BUDGET)])
        assert result.skipped == "source_gone" and result.indexed == 0
        assert index.count_source(ws, "ghost") == 0

    def test_a_late_index_after_the_tombstone_leaves_the_earlier_state(
        self, index, sources, ws
    ) -> None:
        created = add_source(sources, ws, "d1")
        index.index(ws, OWNER, "d1", [frag("f1", "d1", 0, BUDGET)])
        sources.save(ws, replace(created, tombstoned_at=datetime.now(UTC)))
        index.erase_source(ws, "d1")  # what the delete service does
        index.index(ws, OWNER, "d1", [frag("f1", "d1", 0, BUDGET)])  # the worker finishing late
        assert index.count_source(ws, "d1") == 0
        assert index.search(ws, OWNER, "budget forecast", 5) == ()

    def test_a_version_that_is_not_the_current_one_is_not_indexed(
        self, index, sources, fragments, ws
    ) -> None:
        add_source(sources, ws, "d1")
        set_version(fragments, ws, "d1", 2)
        index.index(ws, OWNER, "d1", [frag("f1", "d1", 0, BUDGET, version=2)])
        late = index.index(ws, OWNER, "d1", [frag("old", "d1", 0, KITCHEN, version=1)])
        assert late == IndexResult(indexed=0, truncated=0, skipped="stale_version")
        assert index.count_source(ws, "d1") == 1  # the current version stays as it was
        assert ids(index.search(ws, OWNER, "budget forecast", 5)) == ["f1"]
        newer = index.index(ws, OWNER, "d1", [frag("f9", "d1", 0, SERVER, version=3)])
        assert newer.skipped == "stale_version"  # a version the source does not have yet

    # --- the embedding ceiling truncates the vector, never the text and never the import ----

    def test_a_fragment_over_the_ceiling_is_indexed_and_counted_as_truncated(
        self, index, sources, ws
    ) -> None:
        add_source(sources, ws, "d1")
        long_text = " ".join(["word"] * (MAX_EMBEDDING_TOKENS + 100)) + " zebracrossing"
        result = index.index(
            ws, OWNER, "d1", [frag("n1", "d1", 0, "fine text"), frag("n2", "d1", 1, long_text)]
        )
        assert result == IndexResult(indexed=2, truncated=1, skipped=None)
        assert index.count_source(ws, "d1") == 2
        hits = index.search(ws, OWNER, "zebracrossing", 5)
        assert ids(hits)[0] == "n2" and hits[0].fragment.text == long_text  # the tail is searchable

    def test_a_fragment_at_the_ceiling_is_not_truncated(self, index, sources, ws) -> None:
        add_source(sources, ws, "d1")
        result = index.index(ws, OWNER, "d1", [frag("n1", "d1", 0, " ".join(["word"] * 500))])
        assert result == IndexResult(indexed=1, truncated=0, skipped=None)

    def test_rules_and_dot_leaders_do_not_count_as_hundreds_of_tokens(
        self, index, sources, ws
    ) -> None:
        add_source(sources, ws, "d1")
        rule = "-" * 1000
        leaders = "\n".join(f"Chapter {n} " + "." * 40 + f" {n}" for n in range(25))
        result = index.index(
            ws, OWNER, "d1", [frag("r", "d1", 0, rule), frag("t", "d1", 1, leaders)]
        )
        assert result == IndexResult(indexed=2, truncated=0, skipped=None)

    def test_inconsistent_input_is_rejected_and_changes_nothing(self, index, sources, ws) -> None:
        self.seed(index, sources, ws)
        with pytest.raises(ValueError):
            index.index(ws, OWNER, "d1", [frag("n1", "other-source", 0, "text of another source")])
        with pytest.raises(ValueError):
            index.index(ws, OWNER, "d1", [frag("n1", "d1", 0, "one"), frag("n1", "d1", 1, "two")])
        assert index.count_source(ws, "d1") == 3

    # --- queries ----------------------------------------------------------------------------

    @pytest.mark.parametrize("query", HOSTILE)
    def test_hostile_queries_are_plain_text(self, index, sources, ws, query) -> None:
        self.seed(index, sources, ws)
        add_source(sources, ws, "d2", OTHER)
        index.index(ws, OTHER, "d2", [frag("g1", "d2", 0, "budget forecast secret of another")])
        hits = index.search(ws, OWNER, query, 10)
        assert isinstance(hits, tuple)
        assert all(h.fragment.source_id == "d1" for h in hits)  # never the other owner's
        assert index.count_source(ws, "d1") == 3  # and nothing was dropped or changed

    def test_a_query_that_cannot_be_encoded_finds_nothing(self, index, sources, ws) -> None:
        self.seed(index, sources, ws)
        lone_surrogate = chr(0xD800)  # not spelled as an escape: the editing tools rewrite those
        assert index.search(ws, OWNER, f"budget {lone_surrogate} forecast", 5) == ()
        assert index.search(ws, OWNER, lone_surrogate, 5) == ()

    def test_query_operators_do_not_widen_the_search(self, index, sources, ws) -> None:
        self.seed(index, sources, ws)
        hits = index.search(ws, OWNER, "kitchen NOT budget", 10)
        assert "f2" in ids(hits)  # NOT is a word here, not an operator

    def test_indexing_documents_does_not_change_meeting_search_results(
        self, index, sources, retriever, ws
    ) -> None:
        segments = [
            TranscriptSegment(
                "s1", "m1", "S1", 0, 900, "meeting budget forecast review", 0.9, 1, Locale.EN, None
            ),
            TranscriptSegment(
                "s2", "m1", "S1", 900, 1800, "kitchen plans for the team", 0.9, 1, Locale.EN, None
            ),
        ]
        retriever.index(ws, OWNER, segments)
        before = retriever.search(ws, OWNER, "budget forecast", None, 10)
        self.seed(index, sources, ws)
        add_source(sources, ws, "d7")
        index.index(ws, OWNER, "d7", [frag("s1", "d7", 0, "budget forecast shadow copy")])
        after = retriever.search(ws, OWNER, "budget forecast", None, 10)
        assert after == before and [s.id for s in before][:1] == ["s1"]
        assert retriever.count(ws, "m1") == 2
        # and the other way: meeting segments are not fragments of the document index
        assert "s2" not in ids(index.search(ws, OWNER, "kitchen plans", 10))
        index.erase_source(ws, "d7")
        assert retriever.search(ws, OWNER, "budget forecast", None, 10) == before
