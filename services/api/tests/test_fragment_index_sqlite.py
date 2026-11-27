from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pytest

from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.embedder import LocalEmbedder
from nodo_api.adapters.local.fragment_index import SqliteFragmentIndex
from nodo_api.adapters.local.models import LocalModelPaths
from nodo_api.adapters.local.search import SqliteRetriever
from nodo_api.adapters.local.sources import SqliteFragmentRepository, SqliteSourceRepository
from nodo_api.domain.models import Locale, TranscriptSegment
from nodo_api.domain.sources import IndexResult
from tests.fragment_index_contract import (
    BUDGET,
    KITCHEN,
    OTHER,
    OWNER,
    SERVER,
    FragmentIndexContract,
    frag,
    ids,
    set_version,
)
from tests.source_contract import source
from tests.test_local_adapters import HashEmbedder
from tests.test_local_migrations import _insert_document, _insert_fragment, _meeting_row, migrate_to


@pytest.fixture
def db(tmp_path):
    db = LocalDatabase(tmp_path / "nodo.db")
    yield db
    db.close()


@pytest.fixture
def ws():
    return f"ws-{uuid.uuid4().hex[:8]}"


class TestSqliteFragmentIndex(FragmentIndexContract):
    @pytest.fixture
    def sources(self, db):
        return SqliteSourceRepository(db)

    @pytest.fixture
    def fragments(self, db):
        return SqliteFragmentRepository(db)

    @pytest.fixture
    def index(self, db):
        return SqliteFragmentIndex(db, HashEmbedder())

    @pytest.fixture
    def retriever(self, db):
        return SqliteRetriever(db, HashEmbedder())


def counts(db) -> tuple[int, int]:
    """Rows of the table and stored FTS document sizes (one per indexed text)."""
    with db.transaction(write=False) as conn:
        return (
            conn.execute("SELECT count(*) FROM search_fragments").fetchone()[0],
            conn.execute("SELECT count(*) FROM fragment_fts_docsize").fetchone()[0],
        )


def fts_is_consistent(db) -> bool:
    with db.transaction() as conn:
        conn.execute("INSERT INTO fragment_fts (fragment_fts, rank) VALUES ('integrity-check', 1)")
    return True


def make(db, ws, *source_ids, embedder=None) -> SqliteFragmentIndex:
    """An index over a database where these (live, version 1) document sources exist."""
    sources = SqliteSourceRepository(db)
    for source_id in source_ids:
        sources.add(ws, source(source_id, ws=ws))
    return SqliteFragmentIndex(db, embedder or HashEmbedder())


def seed(index, ws) -> None:
    index.index(
        ws,
        OWNER,
        "d1",
        [frag("f1", "d1", 0, BUDGET), frag("f2", "d1", 1, KITCHEN), frag("f3", "d1", 2, SERVER)],
    )


# --- the rows are exactly the fragments ---------------------------------------------------------


def test_erase_leaves_no_row_vector_or_text_entry(db, ws) -> None:
    index = make(db, ws, "d1")
    seed(index, ws)
    assert counts(db)[0] == 3 and counts(db)[1] == 3
    index.erase_source(ws, "d1")
    assert counts(db) == (0, 0)
    assert fts_is_consistent(db)


def test_reindex_leaves_the_text_index_in_step_with_the_table(db, ws) -> None:
    index = make(db, ws, "d1")
    seed(index, ws)
    set_version(SqliteFragmentRepository(db), ws, "d1", 2)
    index.index(ws, OWNER, "d1", [frag("f1", "d1", 0, "only this remains", version=2)])
    rows, docsize = counts(db)
    assert (rows, docsize) == (1, 1)
    assert fts_is_consistent(db)
    with db.transaction(write=False) as conn:
        gone = conn.execute(
            "SELECT count(*) FROM fragment_fts WHERE fragment_fts MATCH 'kitchen'"
        ).fetchone()[0]
    assert gone == 0


def test_vectors_are_float32_unit_blobs(db, ws) -> None:
    index = make(db, ws, "d1")
    seed(index, ws)
    with db.transaction(write=False) as conn:
        blobs = [r[0] for r in conn.execute("SELECT vector FROM search_fragments")]
    assert len(blobs) == 3
    for blob in blobs:
        vector = np.frombuffer(blob, dtype=np.float32)
        assert vector.shape == (64,) and abs(float(np.linalg.norm(vector)) - 1.0) < 1e-5


def test_embedding_happens_outside_the_write_transaction(db, ws) -> None:
    states = []

    class Probe(HashEmbedder):
        def embed(self, texts):
            states.append(db.connection().in_transaction)
            return super().embed(texts)

    index = make(db, ws, "d1", embedder=Probe())
    seed(index, ws)
    assert states == [False]


def test_a_failing_embedder_changes_nothing(db, ws) -> None:
    class Failing(HashEmbedder):
        broken = False

        def embed(self, texts):
            if self.broken:
                raise RuntimeError("model unavailable")
            return super().embed(texts)

    embedder = Failing()
    index = make(db, ws, "d1", embedder=embedder)
    seed(index, ws)
    embedder.broken = True
    with pytest.raises(RuntimeError):
        index.index(ws, OWNER, "d1", [frag("n1", "d1", 0, "new text")])
    embedder.broken = False
    assert index.count_source(ws, "d1") == 3
    assert ids(index.search(ws, OWNER, "budget forecast", 3))[0] == "f1"


def test_a_failed_write_rolls_back_the_delete(db, ws) -> None:
    index = make(db, ws, "d1")
    seed(index, ws)
    broken = replace(
        frag("n2", "d1", 1, "second new text"), speaker_id=object()
    )  # cannot be stored
    with pytest.raises(Exception):  # noqa: B017 - sqlite3.InterfaceError, whatever the driver says
        index.index(ws, OWNER, "d1", [frag("n1", "d1", 0, "first new text"), broken])
    assert index.count_source(ws, "d1") == 3
    assert counts(db) == (3, 3)
    assert ids(index.search(ws, OWNER, "budget forecast", 3))[0] == "f1"


def test_embeddings_are_batched(db, ws) -> None:
    embedder = HashEmbedder()
    index = make(db, ws, "d1", embedder=embedder)
    many = [frag(f"f{n}", "d1", n, f"paragraph number {n} about topic{n}") for n in range(150)]
    index.index(ws, OWNER, "d1", many)
    assert embedder.calls == [64, 64, 22]
    assert index.count_source(ws, "d1") == 150


# --- tenancy is part of the SQL -----------------------------------------------------------------


def test_every_search_statement_filters_workspace_and_owner(db, ws) -> None:
    index = make(db, ws, "d1", "d2")
    seed(index, ws)
    index.index(ws, OTHER, "d2", [frag("g1", "d2", 0, "budget forecast of someone else")])
    statements: list[str] = []
    db.connection().set_trace_callback(statements.append)
    index.search(ws, OWNER, "budget forecast", 5, source_ids=frozenset({"d1", "d2"}))
    db.connection().set_trace_callback(None)
    reads = [
        s for s in statements if s.lstrip().upper().startswith("SELECT") and "search_fragments" in s
    ]
    assert len(reads) >= 4  # text hits, matrix token, matrix rows, allowed rows, load
    for statement in reads:
        assert f"workspace_id = '{ws}'" in statement, statement
        assert f"owner_id = '{OWNER}'" in statement, statement


def test_a_source_filter_of_thousands_of_ids_still_works(db, ws) -> None:
    index = make(db, ws, "d1")
    seed(index, ws)
    wanted = frozenset({"d1", *(f"x{n}" for n in range(5000))})
    assert ids(index.search(ws, OWNER, "budget forecast", 3, source_ids=wanted))[0] == "f1"


# --- text side: tokenisation and escaping --------------------------------------------------------


def test_accents_and_case_are_folded_and_prefixes_match(db, ws) -> None:
    index = make(db, ws, "d1")
    index.index(
        ws,
        OWNER,
        "d1",
        [
            frag("f1", "d1", 0, "Zażółć gęślą jaźń: résumé de la réunion"),
            frag("f2", "d1", 1, "Kolejka zadań i harmonogram wdrożenia"),
            frag("f3", "d1", 2, "unrelated gardening notes about tulips"),
        ],
    )
    assert ids(index.search(ws, OWNER, "zazolc", 3))[0] == "f1"
    assert ids(index.search(ws, OWNER, "RESUME", 3))[0] == "f1"
    assert ids(index.search(ws, OWNER, "kolejki", 3))[0] == "f2"  # the crude prefix rule


def test_fts_syntax_in_a_query_is_not_interpreted(db, ws) -> None:
    index = make(db, ws, "d1", "d2")
    seed(index, ws)
    index.index(ws, OTHER, "d2", [frag("g1", "d2", 0, "budget forecast secret of another")])
    for query in (
        'budget" OR text:"secret',
        "NEAR(budget secret)",
        "budget*",
        "text : secret",
        "*",
    ):
        assert all(h.fragment.source_id == "d1" for h in index.search(ws, OWNER, query, 10))
    assert counts(db)[0] == 4


# --- the vector side ----------------------------------------------------------------------------


def test_vector_side_ranks_by_cosine_over_all_the_owners_rows(db, ws) -> None:
    index = make(db, ws, "d1", "d2")
    index.index(
        ws,
        OWNER,
        "d1",
        [frag(f"f{n}", "d1", n, f"alpha{n} beta{n} gamma{n}") for n in range(8)]
        + [frag("target", "d1", 9, "migration strategy rollback plan")],
    )
    index.index(ws, OTHER, "d2", [frag("theirs", "d2", 0, "migration rollback of someone else")])
    first = index._vector_hits(ws, OWNER, "migration rollback", None)
    with db.transaction(write=False) as conn:
        target = conn.execute("SELECT rowid FROM search_fragments WHERE fragment_id = 'target'")
        target = target.fetchone()[0]
        theirs = conn.execute("SELECT rowid FROM search_fragments WHERE fragment_id = 'theirs'")
        theirs = theirs.fetchone()[0]
    assert first[0] == target and theirs not in first
    assert index._vector_hits(ws, OWNER, "migration rollback", json_ids("d2")) == []


def test_the_vector_matrix_is_rebuilt_after_a_write_and_after_a_trigger_delete(db, ws) -> None:
    index = make(db, ws, "d1", "d2")
    seed(index, ws)
    index.search(ws, OWNER, "budget forecast", 3)
    token, rowids, _ = index._cache[(ws, OWNER)]
    assert len(rowids) == 3
    index.index(ws, OWNER, "d2", [frag("g1", "d2", 0, "budget forecast addendum")])
    index.search(ws, OWNER, "budget forecast", 3)
    assert len(index._cache[(ws, OWNER)][1]) == 4 and index._cache[(ws, OWNER)][0] != token
    # a delete made outside the index (what a trigger or another process does)
    with db.transaction() as conn:
        conn.execute("DELETE FROM search_fragments WHERE source_id = 'd1'")
    hits = index.search(ws, OWNER, "budget forecast", 3)
    assert ids(hits) == ["g1"] and len(index._cache[(ws, OWNER)][1]) == 1


def test_a_replacement_with_the_same_row_count_is_noticed(db, ws) -> None:
    index = make(db, ws, "d1")
    index.index(ws, OWNER, "d1", [frag("f1", "d1", 0, "first wording about apples")])
    assert ids(index.search(ws, OWNER, "apples", 3)) == ["f1"]
    set_version(SqliteFragmentRepository(db), ws, "d1", 2)
    index.index(ws, OWNER, "d1", [frag("f1", "d1", 0, "second wording about oranges", version=2)])
    assert [h.fragment.text for h in index.search(ws, OWNER, "apples", 3)] == [
        "second wording about oranges"
    ]
    assert ids(index.search(ws, OWNER, "oranges", 3)) == ["f1"]


def test_the_meeting_cache_and_the_fragment_cache_do_not_touch_each_other(db, ws) -> None:
    retriever = SqliteRetriever(db, HashEmbedder())
    index = make(db, ws, "d1")
    seed(index, ws)
    index.search(ws, OWNER, "budget forecast", 3)
    cached = index._cache[(ws, OWNER)]
    retriever.index(
        ws,
        OWNER,
        [TranscriptSegment("s1", "m1", "S1", 0, 900, "meeting words", 0.9, 1, Locale.EN, None)],
    )
    retriever.search(ws, OWNER, "meeting words", None, 3)
    assert index._cache[(ws, OWNER)] is cached
    assert (ws, OWNER) in retriever._cache and retriever._cache[(ws, OWNER)] is not cached


# --- the index follows the fragments and the sources --------------------------------------------


def json_ids(*source_ids: str) -> str:
    import json

    return json.dumps(sorted(source_ids))


def make_document(db, ws, sid="d1", text=BUDGET):
    sources, fragments = SqliteSourceRepository(db), SqliteFragmentRepository(db)
    created = sources.add(ws, source(sid, ws=ws))
    fragment = replace(frag("f1", sid, 0, text), version=1)
    fragments.add_version(ws, sid, (), (fragment,))
    return sources, fragments, created, fragment


def test_tombstoning_a_source_drops_its_index_rows(db, ws) -> None:
    index = SqliteFragmentIndex(db, HashEmbedder())
    sources, _, created, fragment = make_document(db, ws)
    index.index(ws, OWNER, "d1", [fragment])
    index.search(ws, OWNER, "budget forecast", 3)
    sources.save(ws, replace(created, tombstoned_at=datetime.now(UTC)))
    assert index.count_source(ws, "d1") == 0
    assert index.search(ws, OWNER, "budget forecast", 3) == ()
    assert counts(db) == (0, 0)
    assert fts_is_consistent(db)


def test_erasing_the_fragments_drops_their_index_rows(db, ws) -> None:
    index = SqliteFragmentIndex(db, HashEmbedder())
    _, fragments, _, fragment = make_document(db, ws)
    index.index(ws, OWNER, "d1", [fragment])
    fragments.erase(ws, "d1")
    assert index.count_source(ws, "d1") == 0
    assert counts(db) == (0, 0)


def test_deleting_the_source_row_drops_its_index_rows(db, ws) -> None:
    index = SqliteFragmentIndex(db, HashEmbedder())
    _, _, _, fragment = make_document(db, ws)
    index.index(ws, OWNER, "d1", [fragment])
    with db.transaction() as conn:
        conn.execute("DELETE FROM sources WHERE workspace_id = ? AND id = 'd1'", (ws,))
    assert counts(db) == (0, 0)


def test_a_new_version_in_the_fragment_table_leaves_the_index_alone_until_it_is_indexed(
    db, ws
) -> None:
    index = SqliteFragmentIndex(db, HashEmbedder())
    _, fragments, _, fragment = make_document(db, ws)
    index.index(ws, OWNER, "d1", [fragment])
    fragments.add_version(ws, "d1", (), (replace(frag("f2", "d1", 0, KITCHEN), version=2),))
    assert ids(index.search(ws, OWNER, "budget forecast", 3)) == ["f1"]
    index.index(ws, OWNER, "d1", fragments.latest(ws, "d1"))
    assert ids(index.search(ws, OWNER, "kitchen cleaning", 3)) == ["f2"]
    assert ids(index.search(ws, OWNER, "budget forecast", 3)) == ["f2"]  # f1 is gone


def test_a_tombstoned_meeting_source_drops_rows_indexed_under_its_id(db, ws) -> None:
    index = SqliteFragmentIndex(db, HashEmbedder())
    with db.transaction() as conn:
        _meeting_row(conn, "m1", ws=ws)
    index.index(ws, OWNER, "m1", [frag("s1", "m1", 0, BUDGET)])
    with db.transaction() as conn:
        conn.execute(
            "UPDATE meetings SET tombstoned_at = '2026-10-05T00:00:00.000000+00:00'"
            " WHERE workspace_id = ? AND id = 'm1'",
            (ws,),
        )
    assert index.count_source(ws, "m1") == 0


# --- a delete that arrives while an import is embedding ------------------------------------------


def test_a_tombstone_during_embedding_leaves_no_rows_and_no_hit(db, ws) -> None:
    sources = SqliteSourceRepository(db)
    created = sources.add(ws, source("d1", ws=ws))

    class TombstoneWhileEmbedding(HashEmbedder):
        def embed(self, texts):
            vectors = super().embed(texts)  # the slow part is done ...
            sources.save(ws, replace(created, tombstoned_at=datetime.now(UTC)))  # ... then delete
            return vectors

    index = SqliteFragmentIndex(db, TombstoneWhileEmbedding())
    result = index.index(ws, OWNER, "d1", [frag("f1", "d1", 0, BUDGET)])
    assert result == IndexResult(indexed=0, truncated=0, skipped="source_gone")
    assert counts(db) == (0, 0)
    assert index.search(ws, OWNER, "budget forecast", 5) == ()
    assert fts_is_consistent(db)


def test_a_new_version_during_embedding_makes_the_older_index_stale(db, ws) -> None:
    make(db, ws, "d1")
    fragments = SqliteFragmentRepository(db)

    class NewVersionWhileEmbedding(HashEmbedder):
        def embed(self, texts):
            vectors = super().embed(texts)
            set_version(fragments, ws, "d1", 2)  # a newer import finished meanwhile
            return vectors

    index = SqliteFragmentIndex(db, NewVersionWhileEmbedding())
    result = index.index(ws, OWNER, "d1", [frag("f1", "d1", 0, BUDGET, version=1)])
    assert result.skipped == "stale_version" and counts(db) == (0, 0)


# --- the embedding ceiling truncates the vector, never the text ---------------------------------


def stored_flags(db) -> dict[str, int]:
    with db.transaction(write=False) as conn:
        return {
            r[0]: r[1] for r in conn.execute("SELECT fragment_id, truncated FROM search_fragments")
        }


def test_the_truncated_flag_is_stored_with_the_row(db, ws) -> None:
    index = make(db, ws, "d1")
    long_text = " ".join(["word"] * 600)
    index.index(ws, OWNER, "d1", [frag("short", "d1", 0, "fine"), frag("long", "d1", 1, long_text)])
    assert stored_flags(db) == {"short": 0, "long": 1}
    with db.transaction(write=False) as conn:  # the full text is in the text index
        text = conn.execute("SELECT text FROM search_fragments WHERE fragment_id = 'long'")
        assert text.fetchone()[0] == long_text


MODEL = LocalModelPaths.discover().embedding_dir
needs_model = pytest.mark.skipif(
    MODEL is None, reason="the E5 ONNX model is not installed on this machine"
)


def cjk(count: int) -> str:
    """Distinct four-character words, each ended by a full stop, so every one is its own token."""
    stop = chr(0x3002)
    return "".join("".join(chr(0x4E00 + n + k) for k in range(4)) + stop for n in range(count))


@needs_model
def test_the_real_tokenizer_decides_what_fits(db, ws) -> None:
    embedder = LocalEmbedder(MODEL)
    fits = " ".join(["word"] * 500)  # 504 tokens with the prefix and the special tokens
    over = " ".join(["word"] * 520)
    assert embedder.within_ceiling([fits, over, "short"]) == [True, False, True]
    index = make(db, ws, "d1", embedder=embedder)
    result = index.index(ws, OWNER, "d1", [frag("a", "d1", 0, fits), frag("b", "d1", 1, over)])
    assert result == IndexResult(indexed=2, truncated=1, skipped=None)
    assert stored_flags(db) == {"a": 0, "b": 1}


@needs_model
def test_rules_and_dot_leaders_index_fine_under_the_real_tokenizer(db, ws) -> None:
    embedder = LocalEmbedder(MODEL)
    rule = "-" * 1000
    leaders = "\n".join(f"Chapter {n} " + "." * 40 + f" {n}" for n in range(25))
    assert embedder.within_ceiling([rule, leaders]) == [True, True]  # 68 and 204 real tokens
    index = make(db, ws, "d1", embedder=embedder)
    result = index.index(ws, OWNER, "d1", [frag("r", "d1", 0, rule), frag("t", "d1", 1, leaders)])
    assert result == IndexResult(indexed=2, truncated=0, skipped=None)


@needs_model
def test_cjk_and_pipe_table_fragments_over_the_ceiling_are_truncated_and_findable(db, ws) -> None:
    embedder = LocalEmbedder(MODEL)
    tail_word = chr(0x6A21) + chr(0x578B) + chr(0x5C3E) + chr(0x90E8)
    japanese = cjk(300) + tail_word
    rows = "\n".join(f"| item{n} | value{n} | note{n} |\n|---|---|---|" for n in range(60))
    table = rows + "\n| tailmarker | x | y |"
    assert embedder.within_ceiling([japanese, table]) == [False, False]  # both really are too long
    index = make(db, ws, "d1", embedder=embedder)
    result = index.index(
        ws, OWNER, "d1", [frag("jp", "d1", 0, japanese), frag("tb", "d1", 1, table)]
    )
    assert result == IndexResult(indexed=2, truncated=2, skipped=None)
    assert stored_flags(db) == {"jp": 1, "tb": 1}
    # the text index keeps the whole text: a word from the tail finds the fragment through FTS alone
    for fragment_id, word in (("jp", tail_word), ("tb", "tailmarker")):
        from nodo_api.adapters.local.search import match_expression

        rowids = index._text_hits(ws, OWNER, match_expression(word), None)
        assert len(rowids) == 1
        assert ids(index.search(ws, OWNER, word, 3))[0] == fragment_id


# --- migration 0011 ------------------------------------------------------------------------------


def test_0011_adds_the_index_tables_and_keeps_existing_data(tmp_path) -> None:
    path = tmp_path / "nodo.db"
    old = migrate_to(path, 10)
    _meeting_row(old, "m1", title="Kickoff")
    _insert_document(old, "d1")
    _insert_fragment(old, "f1", "Some text", "d1")
    old.execute(
        "INSERT INTO search_segments (workspace_id, owner_id, meeting_id, segment_id, speaker_id,"
        " start_ms, end_ms, version, confidence, project, locale, text, vector)"
        " VALUES ('w', 'u', 'm1', 's1', 'S1', 0, 1, 1, 0.9, 'NODO', 'en', 'meeting words', x'00')"
    )
    old.execute("INSERT INTO search_fts (rowid, text) VALUES (1, 'meeting words')")
    before = {
        table: [tuple(r) for r in old.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3")]
        for table in ("meetings", "sources", "fragments", "search_segments")
    }
    old.close()

    db = LocalDatabase(path)
    try:
        with db.transaction(write=False) as conn:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            applied = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
            after = {
                table: [
                    tuple(r)[: len(before[table][0])]
                    for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3")
                ]
                for table in before
            }
            empty = conn.execute("SELECT count(*) FROM search_fragments").fetchone()[0]
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(search_fragments)")}
            check = conn.execute("PRAGMA foreign_key_check").fetchall()
        assert {"search_fragments", "fragment_fts"} <= tables
        assert "truncated" in columns
        assert applied == sorted(applied) and 11 in applied
        assert after == before and empty == 0 and check == []
        # an index built after the upgrade works, and the old meeting search still does
        index = SqliteFragmentIndex(db, HashEmbedder())
        index.index("w", "u", "d1", [frag("f1", "d1", 0, BUDGET)])
        assert ids(index.search("w", "u", "budget forecast", 3)) == ["f1"]
        retriever = SqliteRetriever(db, HashEmbedder())
        assert retriever.count("w", "m1") == 1
    finally:
        db.close()


def test_0011_applies_on_a_fresh_database_and_is_not_rerun(tmp_path) -> None:
    path = tmp_path / "nodo.db"
    LocalDatabase(path).close()
    db = LocalDatabase(path)  # opening again must not apply it a second time
    try:
        with db.transaction(write=False) as conn:
            rows = conn.execute("SELECT name FROM schema_migrations WHERE version = 11").fetchall()
            triggers = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            }
    finally:
        db.close()
    assert [r[0] for r in rows] == ["0011_fragment_index.sql"]
    assert {
        "search_fragments_fts_insert",
        "search_fragments_fts_delete",
        "fragments_drop_index_rows",
        "sources_tombstone_drop_index_rows",
        "sources_delete_drop_index_rows",
    } <= triggers
