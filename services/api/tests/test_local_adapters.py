"""Behaviour of the SQLite, file system and queue adapters of the local backend."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from nodo_api.adapters.local.blob import LocalBlobStore, UploadRejected
from nodo_api.adapters.local.db import LocalDatabase, from_text, to_text
from nodo_api.adapters.local.queue import LocalWorkerLoop, SqliteJobQueue
from nodo_api.adapters.local.repos import (
    SqliteAuditLog,
    SqliteMeetingRepository,
    SqliteTranscriptRepository,
)
from nodo_api.adapters.local.search import SqliteRetriever, match_expression
from nodo_api.domain.models import (
    Locale,
    Meeting,
    MeetingState,
    RecordingAttestation,
    TranscriptSegment,
)
from nodo_api.errors import NotFoundError
from nodo_api.worker import Worker

WS, ALICE, BOB = "local", "alice", "bob"


@pytest.fixture
def db(tmp_path):
    database = LocalDatabase(tmp_path / "nodo.db")
    yield database
    database.close()


def _meeting(meeting_id: str, owner: str = ALICE, minutes: int = 0) -> Meeting:
    created = datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minutes)
    return Meeting(
        id=meeting_id,
        workspace_id=WS,
        owner_id=owner,
        created_at=created,
        state=MeetingState.UPLOADING,
        attestation=RecordingAttestation(
            owner, meeting_id, "2026-10-v1", created, "retention-30d", Locale.EN, "audit-1", "sig"
        ),
        project="NODO",
    )


def _segment(meeting_id: str, segment_id: str, text: str, version: int = 1) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id, meeting_id, "S1", 0, 1000, text, 0.9, version, Locale.EN, "NODO"
    )


class HashEmbedder:
    """Words hashed into 64 buckets: texts sharing words get similar vectors, with no model."""

    calls: list[int]

    def __init__(self) -> None:
        self.calls = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        out = []
        for text in texts:
            vector = np.zeros(64)
            for word in text.lower().split():
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1
            out.append(vector.tolist())
        return out


# --- time ----------------------------------------------------------------------------------------


def test_timestamps_round_trip_and_sort_as_text() -> None:
    early = datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC)
    late = early + timedelta(microseconds=1)
    assert from_text(to_text(early)) == early
    assert to_text(early) < to_text(late)
    with pytest.raises(ValueError):
        to_text(datetime(2026, 1, 1))


# --- meetings ------------------------------------------------------------------------------------


def test_meeting_round_trip_ownership_and_speaker_names(db) -> None:
    repo = SqliteMeetingRepository(db)
    created = repo.add(WS, _meeting("m1"), "key-0001", "hash")
    fetched = repo.get_owned(WS, ALICE, "m1")
    assert fetched == created and fetched.attestation.locale is Locale.EN
    with pytest.raises(NotFoundError):
        repo.get_owned(WS, BOB, "m1")
    from dataclasses import replace

    repo.save(WS, replace(fetched, speaker_names=(("S1", "Zoë"), ("S2", "Łukasz"))))
    assert repo.get_owned(WS, ALICE, "m1").speaker_names == (("S1", "Zoë"), ("S2", "Łukasz"))


def test_idempotency_is_per_user_and_returns_the_stored_hash(db) -> None:
    repo = SqliteMeetingRepository(db)
    first = repo.add(WS, _meeting("a1"), "same-key", "h-a")
    again = repo.add(WS, _meeting("a2"), "same-key", "h-a")
    assert again.id == first.id
    assert repo.add(WS, _meeting("b1", BOB), "same-key", "h-b").id == "b1"
    found, stored = repo.get_by_idempotency_key(WS, ALICE, "same-key")
    assert (found.id, stored) == ("a1", "h-a")


def test_listing_is_newest_first_keyset_paged_and_hides_deleted(db) -> None:
    repo = SqliteMeetingRepository(db)
    for index in range(5):
        repo.add(WS, _meeting(f"m{index}", minutes=index), f"k{index}", "h")
    repo.add(WS, _meeting("other", BOB, minutes=9), "kb", "h")
    first = repo.list_owned(WS, ALICE, 2, None)
    assert [m.id for m in first] == ["m4", "m3"]
    second = repo.list_owned(WS, ALICE, 2, (first[-1].created_at, first[-1].id))
    assert [m.id for m in second] == ["m2", "m1"]
    from dataclasses import replace

    repo.save(WS, replace(repo.get_owned(WS, ALICE, "m4"), tombstoned_at=datetime.now(UTC)))
    assert [m.id for m in repo.list_owned(WS, ALICE, 10, None)] == ["m3", "m2", "m1", "m0"]
    with pytest.raises(NotFoundError):
        repo.get_owned(WS, ALICE, "m4")
    assert repo.get_owned(WS, ALICE, "m4", include_tombstoned=True).tombstoned_at is not None


def test_saving_an_unknown_meeting_fails(db) -> None:
    with pytest.raises(NotFoundError):
        SqliteMeetingRepository(db).save(WS, _meeting("ghost"))


def test_transcript_versions_are_immutable_and_the_latest_wins(db) -> None:
    SqliteMeetingRepository(db).add(WS, _meeting("m1"), "k", "h")
    repo = SqliteTranscriptRepository(db)
    repo.add_version(WS, "m1", (_segment("m1", "seg-1", "first"),))
    with pytest.raises(ValueError, match="immutable"):
        repo.add_version(WS, "m1", (_segment("m1", "seg-1", "again"),))
    repo.add_version(WS, "m1", (_segment("m1", "seg-1", "second", version=2),))
    assert repo.latest(WS, "m1")[0].text == "second"
    assert repo.get_version(WS, "m1", 1)[0].text == "first"
    assert repo.get_segment(WS, "m1", "seg-1").text == "second"
    assert repo.versions(WS, "m1") == (1, 2)
    repo.erase(WS, "m1")
    with pytest.raises(NotFoundError):
        repo.latest(WS, "m1")


def test_audit_events_get_distinct_ids(db) -> None:
    log = SqliteAuditLog(db)
    assert log.append(WS, "a", {"k": "v"}) != log.append(WS, "a", {"k": "v"})


def test_a_failed_transaction_leaves_nothing_behind(db) -> None:
    repo = SqliteMeetingRepository(db)
    with pytest.raises(RuntimeError), db.transaction():
        repo.add(WS, _meeting("m1"), "k", "h")
        raise RuntimeError
    with pytest.raises(NotFoundError):
        repo.get_owned(WS, ALICE, "m1")


# --- search --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("async queue", '"asyn"* OR "queu"*'),
        ("a b", None),
        ('inject" OR "x', '"inje"* OR "or"'),
        ("Kolejka", '"kolej"*'),
    ],
)
def test_match_expression_never_exposes_fts_syntax(query, expected) -> None:
    assert match_expression(query) == expected


def _retriever(db):
    embedder = HashEmbedder()
    return SqliteRetriever(db, embedder, min_similarity=0.2), embedder


def test_search_finds_by_words_by_prefix_and_by_meaning_of_shared_words(db) -> None:
    retriever, _ = _retriever(db)
    retriever.index(
        WS,
        ALICE,
        (
            _segment("m1", "s1", "We move transcription to an asynchronous queue"),
            _segment("m1", "s2", "The budget review is on Friday"),
            _segment("m2", "s1", "Kolejka asynchroniczna działa dobrze"),
        ),
    )
    hits = retriever.search(WS, ALICE, "asynchronous queue", None, 5)
    assert hits[0].id == "s1" and hits[0].meeting_id == "m1"
    assert {h.meeting_id for h in retriever.search(WS, ALICE, "kolejki", None, 5)} >= {"m2"}
    assert retriever.search(WS, ALICE, "???", None, 5) is not None


def test_search_is_scoped_to_the_owner_and_the_project(db) -> None:
    retriever, _ = _retriever(db)
    mine = _segment("m1", "s1", "secret roadmap")
    theirs = _segment("m2", "s1", "secret roadmap")
    retriever.index(WS, ALICE, (mine,))
    retriever.index(WS, BOB, (theirs,))
    assert [h.meeting_id for h in retriever.search(WS, ALICE, "secret roadmap", None, 5)] == ["m1"]
    assert [h.meeting_id for h in retriever.search(WS, BOB, "secret roadmap", None, 5)] == ["m2"]
    assert retriever.search(WS, ALICE, "secret roadmap", {"project": "OTHER"}, 5) == ()
    assert retriever.search("elsewhere", ALICE, "secret roadmap", None, 5) == ()


def test_reindexing_replaces_the_text_and_erase_removes_everything(db) -> None:
    retriever, _ = _retriever(db)
    retriever.index(WS, ALICE, (_segment("m1", "s1", "apples and oranges"),))
    retriever.index(WS, ALICE, (_segment("m1", "s1", "bananas only", version=2),))
    assert retriever.search(WS, ALICE, "oranges", None, 5) == ()
    assert retriever.search(WS, ALICE, "bananas", None, 5)[0].version == 2
    retriever.erase(WS, "m1")
    assert retriever.search(WS, ALICE, "bananas", None, 5) == ()
    with db.transaction(write=False) as conn:
        assert conn.execute("SELECT count(*) FROM search_fts_docsize").fetchone()[0] == 0


def test_embedding_is_batched(db) -> None:
    retriever, embedder = _retriever(db)
    retriever.index(WS, ALICE, tuple(_segment("m1", f"s{i}", f"sentence {i}") for i in range(150)))
    assert embedder.calls == [64, 64, 22]


# --- audio ---------------------------------------------------------------------------------------


def _blobs(tmp_path, db):
    return LocalBlobStore(tmp_path / "audio", db, lambda: "http://127.0.0.1:9999")


def test_an_upload_link_works_once_and_stores_the_audio(tmp_path, db) -> None:
    blobs = _blobs(tmp_path, db)
    session = blobs.create_upload_session(WS, "m1")
    assert session["upload_url"].startswith("http://127.0.0.1:9999/v1/local/uploads/")
    token = session["upload_url"].rsplit("/", 1)[1]
    assert blobs.accept_upload(token, [b"abc", b"def"]) == (WS, "m1")
    assert Path(blobs.create_read_url(WS, "m1")).read_bytes() == b"abcdef"
    with pytest.raises(UploadRejected):
        blobs.accept_upload(token, [b"again"])
    with pytest.raises(UploadRejected):
        blobs.accept_upload("not-a-token", [b"x"])


def test_an_expired_link_and_a_failed_upload_are_handled(tmp_path, db) -> None:
    blobs = LocalBlobStore(tmp_path / "audio", db, lambda: "http://x", upload_seconds=60)
    token = blobs.create_upload_session(WS, "m1")["upload_url"].rsplit("/", 1)[1]
    with db.transaction() as conn:
        conn.execute("UPDATE upload_sessions SET expires_at = '2000-01-01T00:00:00.000000+00:00'")
    with pytest.raises(UploadRejected):
        blobs.accept_upload(token, [b"x"])
    fresh = blobs.create_upload_session(WS, "m2")["upload_url"].rsplit("/", 1)[1]

    def broken():
        yield b"half"
        raise OSError("disk full")

    with pytest.raises(OSError):
        blobs.accept_upload(fresh, broken())
    assert blobs.accept_upload(fresh, [b"whole"]) == (WS, "m2")  # the link survived the failure


def test_paths_are_never_built_from_unsafe_ids_and_erase_removes_audio(tmp_path, db) -> None:
    blobs = _blobs(tmp_path, db)
    with pytest.raises(ValueError):
        blobs.create_read_url(WS, "../escape")
    token = blobs.create_upload_session(WS, "m1")["upload_url"].rsplit("/", 1)[1]
    blobs.accept_upload(token, [b"x"])
    blobs.erase(WS, "m1")
    assert blobs.was_erased(WS, "m1")
    with pytest.raises(NotFoundError):
        blobs.create_read_url(WS, "m1")


# --- queue ---------------------------------------------------------------------------------------


class Recorder:
    def __init__(self, fail_times: int = 0) -> None:
        self.seen: list[tuple[str, str]] = []
        self.failed: list[str] = []
        self.fail_times = fail_times

    def process(self, workspace_id, meeting_id):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("boom")
        self.seen.append(("transcribe", meeting_id))

    def reanalyze(self, workspace_id, meeting_id):
        self.seen.append(("reanalyze", meeting_id))

    def mark_failed(self, workspace_id, meeting_id):
        self.failed.append(meeting_id)


def test_a_job_exists_once_and_runs_through_the_existing_worker(db) -> None:
    queue, recorder = SqliteJobQueue(db), Recorder()
    first = queue.publish_once(WS, "m1", "transcribe", {"meeting_id": "m1"})
    assert queue.publish_once(WS, "m1", "transcribe", {"meeting_id": "m1"}) == first
    queue.publish_once(WS, "m1:speakers:x", "reanalyze", {"meeting_id": "m1"})
    loop = LocalWorkerLoop(queue, Worker(recorder))
    assert loop.run_pending() == 2
    assert sorted(recorder.seen) == [("reanalyze", "m1"), ("transcribe", "m1")]
    assert queue.pending() == 0 and loop.run_pending() == 0


def test_a_failing_job_is_retried_later_and_dead_lettered_at_the_end(db) -> None:
    queue, recorder = SqliteJobQueue(db), Recorder(fail_times=99)
    queue.publish_once(WS, "m1", "transcribe", {"meeting_id": "m1"})
    loop = LocalWorkerLoop(queue, Worker(recorder, max_deliveries=2))
    assert loop.run_pending() == 1
    assert queue.pending() == 1  # abandoned: waits for its retry time
    with db.transaction() as conn:
        conn.execute("UPDATE jobs SET run_after = '2000-01-01T00:00:00.000000+00:00'")
    assert loop.run_pending() == 1
    assert queue.pending() == 0 and recorder.failed == ["m1"]
    with db.transaction(write=False) as conn:
        reason = conn.execute("SELECT failed_reason FROM jobs").fetchone()[0]
    assert reason == "max_deliveries"


def test_a_malformed_job_body_is_dead_lettered(db) -> None:
    queue = SqliteJobQueue(db)
    queue.publish_once(WS, "x", "mystery", {"meeting_id": "m1"})
    LocalWorkerLoop(queue, Worker(Recorder())).run_pending()
    with db.transaction(write=False) as conn:
        assert conn.execute("SELECT failed_reason FROM jobs").fetchone()[0] == "unknown_job_type"


def test_the_worker_thread_picks_up_a_job_by_itself(db) -> None:
    queue, recorder = SqliteJobQueue(db), Recorder()
    loop = LocalWorkerLoop(queue, Worker(recorder))
    loop.start()
    try:
        queue.publish_once(WS, "m1", "transcribe", {"meeting_id": "m1"})
        for _ in range(100):
            if recorder.seen:
                break
            import time

            time.sleep(0.05)
    finally:
        loop.stop()
    assert recorder.seen == [("transcribe", "m1")]
    assert json.loads(json.dumps({"ok": True}))["ok"]
