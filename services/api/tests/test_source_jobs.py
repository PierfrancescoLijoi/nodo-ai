"""Source import jobs through the queue and the worker: dispatch, delivery caps, crash release,
meeting priority and cancellation (Task 7). Meeting jobs must behave as before."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nodo_api import telemetry
from nodo_api.adapters.local.bootstrap import EngineAlreadyRunning, EngineLock, LocalRuntime
from nodo_api.adapters.local.db import LocalDatabase, to_text
from nodo_api.adapters.local.queue import LocalWorkerLoop, SqliteJobQueue
from nodo_api.adapters.memory import MemoryJobQueue
from nodo_api.domain.documents import ParseError
from nodo_api.worker import IMPORT_SOURCE, Worker

WS = "local"
SECRET = "Quarterly-salaries-of-Anna.pdf"


@pytest.fixture
def db(tmp_path):
    database = LocalDatabase(tmp_path / "nodo.db")
    yield database
    database.close()


class Meetings:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []
        self.failed: list[str] = []

    def process(self, workspace_id, meeting_id):
        self.seen.append(("transcribe", meeting_id))

    def reanalyze(self, workspace_id, meeting_id):
        self.seen.append(("reanalyze", meeting_id))

    def mark_failed(self, workspace_id, meeting_id):
        self.failed.append(meeting_id)


class Imports:
    def __init__(self, error: Exception | None = None) -> None:
        self.seen: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.error = error

    def process(self, workspace_id, source_id):
        self.seen.append(source_id)
        if self.error is not None:
            raise self.error

    def mark_failed(self, workspace_id, source_id, reason):
        self.failed.append((source_id, reason))


class Idle:
    """Stands in for the worker loop and the retention service of a LocalRuntime."""

    started = False

    def start(self):
        self.started = True

    def stop(self, timeout: float = 10.0):
        return None

    def sweep_all(self):
        return None


def _runtime(db, queue, idle, lock=None) -> LocalRuntime:
    return LocalRuntime(db, None, queue, idle, None, None, None, None, None, idle, None, lock=lock)


def _rows(db):
    with db.transaction(write=False) as conn:
        return {
            r["job_key"]: dict(r)
            for r in conn.execute("SELECT job_key, attempts, claimed_at, failed_reason FROM jobs")
        }


def _make_due(db):
    with db.transaction() as conn:
        conn.execute("UPDATE jobs SET run_after = '2000-01-01T00:00:00.000000+00:00'")


def test_worker_dispatches_import_source_by_source_id(db) -> None:
    queue, imports = SqliteJobQueue(db), Imports()
    queue.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"})
    assert LocalWorkerLoop(queue, Worker(Meetings(), imports=imports)).run_pending() == 1
    assert imports.seen == ["s1"] and queue.pending() == 0
    assert _rows(db)["import:s1"]["failed_reason"] is None


def test_import_job_without_an_imports_service_is_an_unknown_type(db) -> None:
    queue = SqliteJobQueue(db)
    queue.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"})
    LocalWorkerLoop(queue, Worker(Meetings())).run_pending()
    assert _rows(db)["import:s1"]["failed_reason"] == "unknown_job_type"


def test_body_without_any_subject_id_is_dead_lettered(db) -> None:
    queue, imports = SqliteJobQueue(db), Imports()
    queue.publish_once(WS, "import:x", IMPORT_SOURCE, {})
    queue.publish_once(WS, "import:empty", IMPORT_SOURCE, {"source_id": ""})
    queue.publish_once(WS, "import:meeting", IMPORT_SOURCE, {"meeting_id": "s1"})
    queue.publish_once(WS, "m:x", "transcribe", {"source_id": None})
    LocalWorkerLoop(queue, Worker(Meetings(), imports=imports)).run_pending()
    rows = _rows(db)
    for key in ("import:x", "import:empty", "import:meeting", "m:x"):
        assert rows[key]["failed_reason"] == "malformed", key
    assert imports.seen == []


def test_meeting_job_does_not_need_source_id_and_still_works(db) -> None:
    queue, meetings = SqliteJobQueue(db), Meetings()
    queue.publish_once(WS, "m1", "transcribe", {"meeting_id": "m1"})
    queue.publish_once(WS, "m1:speakers:x", "reanalyze", {"meeting_id": "m1"})
    loop = LocalWorkerLoop(queue, Worker(meetings, imports=Imports()))
    assert loop.run_pending() == 2
    assert sorted(meetings.seen) == [("reanalyze", "m1"), ("transcribe", "m1")]


def test_import_jobs_get_four_deliveries_meeting_jobs_five(db) -> None:
    worker = Worker(Meetings(), imports=Imports())
    assert worker.max_deliveries_for(IMPORT_SOURCE) == 4
    assert worker.max_deliveries_for("transcribe") == 5
    assert worker.max_deliveries_for("reanalyze") == 5
    assert Worker(Meetings(), max_deliveries=2).max_deliveries_for("transcribe") == 2

    # A poison import: every delivery fails; the fourth ends it with the parser error code.
    queue, imports = SqliteJobQueue(db), Imports(ParseError("encrypted"))
    queue.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"})
    loop = LocalWorkerLoop(queue, Worker(Meetings(), imports=imports))
    for _ in range(3):
        assert loop.run_pending() == 1 and imports.failed == []
        _make_due(db)
    assert loop.run_pending() == 1
    assert imports.seen == ["s1"] * 4
    assert imports.failed == [("s1", "encrypted")]
    assert _rows(db)["import:s1"]["failed_reason"] == "max_deliveries"
    _make_due(db)
    assert loop.run_pending() == 0  # terminal: never delivered again


def test_a_failed_import_logs_no_document_detail_and_reports_a_code(db, caplog) -> None:
    queue, imports = SqliteJobQueue(db), Imports(RuntimeError(f"cannot read {SECRET}: secret text"))
    queue.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"})
    loop = LocalWorkerLoop(queue, Worker(Meetings(), imports=imports))
    caplog.set_level(logging.DEBUG)
    for _ in range(4):
        loop.run_pending()
        _make_due(db)
    assert imports.failed == [("s1", "import_failed")]
    assert SECRET not in caplog.text and "secret text" not in caplog.text


def test_a_failed_import_leaves_no_document_detail_in_spans(db, monkeypatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "tracer", lambda: provider.get_tracer("test"))
    queue = SqliteJobQueue(db)
    queue.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"})
    error = RuntimeError(f"cannot read {SECRET}: secret text")
    LocalWorkerLoop(queue, Worker(Meetings(), imports=Imports(error))).run_pending()
    spans = exporter.get_finished_spans()
    assert [s.name for s in spans] == ["worker.import_source"]
    dumped = spans[0].to_json()
    assert SECRET not in dumped and "secret text" not in dumped
    assert spans[0].events == () or not spans[0].events
    assert spans[0].attributes["error.type"] == "RuntimeError"
    assert not spans[0].status.is_ok and spans[0].status.description == "RuntimeError"


def test_a_failing_mark_failed_still_dead_letters_the_job(db, caplog) -> None:
    class Broken(Imports):
        def mark_failed(self, workspace_id, source_id, reason):
            raise RuntimeError(f"database gone while failing {SECRET}")

    queue = SqliteJobQueue(db)
    queue.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"})
    worker = Worker(Meetings(), imports=Broken(RuntimeError("x")))
    caplog.set_level(logging.DEBUG)
    for _ in range(4):
        LocalWorkerLoop(queue, worker).run_pending()
        _make_due(db)
    assert _rows(db)["import:s1"]["failed_reason"] == "mark_failed_failed"
    lines = [r.getMessage() for r in caplog.records if "mark_failed_failed" in r.getMessage()]
    assert lines == ["mark_failed_failed type=RuntimeError"]
    assert SECRET not in caplog.text


def test_release_claims_frees_a_claimed_job_and_keeps_its_attempts(db) -> None:
    crashed = SqliteJobQueue(db)
    crashed.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"})
    assert crashed.claim_next().delivery_count == 1  # the engine dies while processing it
    assert crashed.claim_next() is None
    restarted = SqliteJobQueue(db)
    assert restarted.release_claims() == 1
    row = _rows(db)["import:s1"]
    assert row["claimed_at"] is None and row["attempts"] == 1
    assert restarted.claim_next().delivery_count == 2  # the crash cost a delivery


@pytest.mark.parametrize(
    ("job_type", "body", "cap"),
    [(IMPORT_SOURCE, {"source_id": "s1"}, 4), ("transcribe", {"meeting_id": "s1"}, 5)],
)
def test_a_job_that_crashes_the_engine_every_time_ends_failed(db, job_type, body, cap) -> None:
    SqliteJobQueue(db).publish_once(WS, "s1", job_type, body)
    for delivery in range(1, cap + 1):  # each start releases the claim and the job crashes again
        restarted = SqliteJobQueue(db)
        restarted.release_claims()
        assert restarted.claim_next().delivery_count == delivery
    imports, meetings = Imports(), Meetings()
    restarted = SqliteJobQueue(db)
    restarted.release_claims()
    assert LocalWorkerLoop(restarted, Worker(meetings, imports=imports)).run_pending() == 1
    assert imports.seen == [] and meetings.seen == []  # not run a further time
    assert imports.failed == ([("s1", "engine_crashed")] if job_type == IMPORT_SOURCE else [])
    assert meetings.failed == ([] if job_type == IMPORT_SOURCE else ["s1"])
    assert _rows(db)["s1"]["failed_reason"] == "max_deliveries"
    restarted = SqliteJobQueue(db)
    assert restarted.release_claims() == 0 and restarted.claim_next() is None


def test_release_claims_does_not_free_a_live_claim(db) -> None:
    queue = SqliteJobQueue(db)
    queue.publish_once(WS, "m1", "transcribe", {"meeting_id": "m1"})
    assert queue.claim_next() is not None
    assert queue.release_claims() == 0
    assert _rows(db)["m1"]["claimed_at"] is not None and queue.claim_next() is None


def test_cancel_unclaimed_job_marks_it_failed_cancelled(db) -> None:
    queue, imports = SqliteJobQueue(db), Imports()
    queue.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"})
    assert queue.cancel(WS, "import:s1", IMPORT_SOURCE) is True
    assert _rows(db)["import:s1"]["failed_reason"] == "cancelled"
    assert LocalWorkerLoop(queue, Worker(Meetings(), imports=imports)).run_pending() == 0
    assert imports.seen == [] and queue.pending() == 0


def test_cancel_leaves_a_claimed_job_to_the_import_service(db) -> None:
    queue = SqliteJobQueue(db)
    queue.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"})
    message = queue.claim_next()
    assert queue.cancel(WS, "import:s1", IMPORT_SOURCE) is False
    assert _rows(db)["import:s1"]["failed_reason"] is None
    queue.complete_message(message)
    assert queue.cancel(WS, "import:s1", IMPORT_SOURCE) is False  # done jobs stay done


# --- engine start --------------------------------------------------------------------------------


def test_local_runtime_releases_claims_on_start(db) -> None:
    crashed = SqliteJobQueue(db)
    crashed.publish_once(WS, "m1", "transcribe", {"meeting_id": "m1"})
    crashed.claim_next()
    queue, idle = SqliteJobQueue(db), Idle()
    runtime = _runtime(db, queue, idle)
    runtime.start()
    runtime._stop.set()
    assert idle.started and _rows(db)["m1"]["claimed_at"] is None
    assert queue.claim_next().delivery_count == 2


def test_a_failing_release_does_not_stop_the_engine_from_starting(db, caplog) -> None:
    class Busy(SqliteJobQueue):
        def release_claims(self):
            raise RuntimeError(f"database is locked {SECRET}")

    idle = Idle()
    runtime = _runtime(db, Busy(db), idle)
    runtime.start()
    runtime._stop.set()
    assert idle.started
    assert "release_claims_failed type=RuntimeError" in caplog.text and SECRET not in caplog.text


def test_a_second_engine_on_the_same_folder_fails_before_releasing_claims(db, tmp_path) -> None:
    class Spy(SqliteJobQueue):
        released = 0

        def release_claims(self):
            self.released += 1
            return 0

    first_idle, second_idle = Idle(), Idle()
    first = _runtime(db, Spy(db), first_idle, EngineLock(tmp_path / "engine.lock"))
    first.start()
    first._stop.set()
    second_queue = Spy(db)
    second = _runtime(db, second_queue, second_idle, EngineLock(tmp_path / "engine.lock"))
    with pytest.raises(EngineAlreadyRunning):
        second.start()
    assert second_queue.released == 0 and not second_idle.started
    first.lock.release()  # the first engine stops: the folder is free again
    third = EngineLock(tmp_path / "engine.lock")
    third.acquire()
    third.release()


# --- claiming ------------------------------------------------------------------------------------


def test_two_engines_racing_never_claim_the_same_job(tmp_path) -> None:
    databases = [LocalDatabase(tmp_path / "nodo.db") for _ in range(3)]
    try:
        publisher = SqliteJobQueue(databases[0])
        total = 600
        for n in range(total):
            publisher.publish_once(WS, f"m{n}", "transcribe", {"meeting_id": f"m{n}"})
        claimed: list[str] = []
        lock = threading.Lock()
        start = threading.Barrier(6)

        def drain(queue: SqliteJobQueue) -> None:
            start.wait()
            while (message := queue.claim_next()) is not None:
                with lock:
                    claimed.append(message.job_key)

        threads = [
            threading.Thread(target=drain, args=(SqliteJobQueue(databases[n % 3]),))
            for n in range(6)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        assert len(claimed) == total and len(set(claimed)) == total
    finally:
        for database in databases:
            database.close()


def test_the_worker_thread_shuts_down_cleanly(db) -> None:
    loop = LocalWorkerLoop(SqliteJobQueue(db), Worker(Meetings()))
    loop.start()
    loop.stop(timeout=5)
    assert not loop._thread.is_alive()


def test_the_worker_thread_survives_a_job_that_raises(db, caplog) -> None:
    class Raising:
        def __init__(self) -> None:
            self.handled: list[str] = []

        def handle(self, receiver, message):
            self.handled.append(message.job_key)
            if message.job_key == "m1":
                raise RuntimeError(f"settling failed for {SECRET}")
            receiver.complete_message(message)

    queue, worker = SqliteJobQueue(db), Raising()
    queue.publish_once(WS, "m1", "transcribe", {"meeting_id": "m1"})
    loop = LocalWorkerLoop(queue, worker)
    loop.start()
    try:
        queue.publish_once(WS, "m2", "transcribe", {"meeting_id": "m2"})
        for _ in range(200):
            if "m2" in worker.handled:
                break
            threading.Event().wait(0.025)
        assert loop._thread.is_alive()
    finally:
        loop.stop()
    assert worker.handled == ["m1", "m2"]
    assert "handle_failed type=RuntimeError" in caplog.text and SECRET not in caplog.text


# --- the same JobControl and priority contract on the memory and the SQLite queue ---------------


@pytest.fixture(params=["memory", "sqlite"])
def control(request, tmp_path):
    if request.param == "memory":
        yield MemoryJobQueue()
        return
    database = LocalDatabase(tmp_path / "nodo.db")
    yield SqliteJobQueue(database)
    database.close()


def _later(seconds: float) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


def test_job_control_contract(control) -> None:
    assert control.release_claims() == 0
    assert control.cancel(WS, "import:s1", IMPORT_SOURCE) is False  # nothing to cancel
    job_id = control.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"})
    assert control.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"}) == job_id
    assert control.publish_count(WS, "import:s1", IMPORT_SOURCE) == 1
    assert control.cancel(WS, "import:s1", "transcribe") is False  # type is part of the key
    assert control.cancel(WS, "import:s1", IMPORT_SOURCE) is True
    assert control.cancel(WS, "import:s1", IMPORT_SOURCE) is False  # already cancelled
    assert control.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"}) == job_id
    assert control.cancel(WS, "import:s1", IMPORT_SOURCE) is False  # a cancel is final
    assert control.claim_next() is None  # a cancelled job is never delivered
    control.publish_once(WS, "m1", "transcribe", {"meeting_id": "m1"})
    assert control.claim_next().job_key == "m1"
    assert control.cancel(WS, "m1", "transcribe") is False  # claimed: cooperative cancel only
    assert control.release_claims() == 0  # a live claim is kept


def test_meeting_job_is_claimed_before_an_older_import_job(control) -> None:
    control.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"})
    control.publish_once(WS, "m1", "transcribe", {"meeting_id": "m1"})
    assert control.claim_next(now=_later(60)).job_key == "m1"
    assert control.claim_next(now=_later(60)).job_key == "import:s1"


@pytest.mark.parametrize(("waited", "first"), [(599, "m1"), (601, "import:s1")])
def test_an_import_ranks_with_meetings_after_ten_minutes(control, waited, first) -> None:
    control.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"})
    control.publish_once(WS, "m1", "transcribe", {"meeting_id": "m1"})
    assert control.claim_next(now=_later(waited)).job_key == first


def test_a_backlog_of_aged_imports_lets_a_new_meeting_through_at_once(control) -> None:
    for n in range(5):
        control.publish_once(WS, f"import:{n}", IMPORT_SOURCE, {"source_id": f"s{n}"})
    control.publish_once(WS, "m1", "transcribe", {"meeting_id": "m1"})
    order = [control.claim_next(now=_later(900)).job_key for _ in range(6)]
    assert order.index("m1") <= 1
    assert sorted(order) == sorted(["m1", *(f"import:{n}" for n in range(5))])


def test_continuous_meetings_still_let_an_aged_import_through(control) -> None:
    control.publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"})
    claimed = []
    for n in range(4):  # a new meeting arrives before every claim
        control.publish_once(WS, f"m{n}", "transcribe", {"meeting_id": f"m{n}"})
        claimed.append(control.claim_next(now=_later(5)).job_key)
    assert "import:s1" not in claimed  # young: meetings first
    for n in range(4, 8):
        control.publish_once(WS, f"m{n}", "transcribe", {"meeting_id": f"m{n}"})
        claimed.append(control.claim_next(now=_later(900)).job_key)
    assert "import:s1" in claimed[4:6]


def test_jobs_with_the_same_time_are_claimed_in_publish_order(control, monkeypatch) -> None:
    from nodo_api.adapters.local import queue as local_queue
    from nodo_api.adapters.memory import adapters as memory_adapters

    frozen = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(memory_adapters, "datetime", Frozen)
    monkeypatch.setattr(local_queue, "now_text", lambda: to_text(frozen))
    for key in ("b", "a", "c"):
        control.publish_once(WS, key, "transcribe", {"meeting_id": key})
    later = frozen + timedelta(seconds=1)
    assert [control.claim_next(now=later).job_key for _ in range(3)] == ["b", "a", "c"]


# --- graceful stop -------------------------------------------------------------------------------


class Long:
    """A job that runs until told to finish, then returns without settling (like a cut-off run)."""

    def __init__(self) -> None:
        self.running = threading.Event()
        self.finish = threading.Event()
        self.deliveries: list[int] = []

    def handle(self, receiver, message):
        self.deliveries.append(message.delivery_count)
        self.running.set()
        self.finish.wait(10)


def test_a_graceful_stop_during_a_long_job_does_not_cost_a_delivery(db) -> None:
    SqliteJobQueue(db).publish_once(WS, "import:s1", IMPORT_SOURCE, {"source_id": "s1"})
    jobs = []
    try:
        for _ in range(6):  # more stops than the import cap of 4
            queue, job = SqliteJobQueue(db), Long()
            jobs.append(job)
            queue.release_claims()
            loop = LocalWorkerLoop(queue, job)
            loop.start()
            assert job.running.wait(5)
            loop.stop(timeout=0.05)  # the job is still running when the app closes
            row = _rows(db)["import:s1"]
            assert row["claimed_at"] is None and row["attempts"] == 0
            assert row["failed_reason"] is None
        assert [job.deliveries for job in jobs] == [[1]] * 6
    finally:
        for job in jobs:
            job.finish.set()
    imports = Imports()
    assert LocalWorkerLoop(SqliteJobQueue(db), Worker(Meetings(), imports=imports)).run_pending()
    assert imports.seen == ["s1"] and imports.failed == []


def test_release_claim_never_puts_a_settled_job_back(db) -> None:
    queue = SqliteJobQueue(db)
    for key in ("done", "retry", "dead"):
        queue.publish_once(WS, key, "transcribe", {"meeting_id": key})
    messages = {m.job_key: m for m in (queue.claim_next() for _ in range(3))}
    queue.complete_message(messages["done"])
    queue.abandon_message(messages["retry"])
    queue.dead_letter_message(messages["dead"], reason="max_deliveries")
    for message in messages.values():  # the worker settled first: stop must change nothing
        assert queue.release_claim(message) is False
    rows = _rows(db)
    assert [rows[k]["attempts"] for k in ("done", "retry", "dead")] == [1, 1, 1]
    assert rows["dead"]["failed_reason"] == "max_deliveries"
    assert queue.claim_next() is None  # done stays done, the retry waits for its time


def test_release_claim_only_matches_its_own_claim(db) -> None:
    crashed = SqliteJobQueue(db)
    crashed.publish_once(WS, "m1", "transcribe", {"meeting_id": "m1"})
    old = crashed.claim_next()
    restarted = SqliteJobQueue(db)
    restarted.release_claims()
    current = restarted.claim_next()
    assert current.delivery_count == 2
    assert crashed.release_claim(old) is False  # an older claim token does not match
    assert restarted.release_claim(current) is True
    assert _rows(db)["m1"]["attempts"] == 1 and restarted.release_claim(current) is False


def test_release_claim_contract(control) -> None:
    control.publish_once(WS, "m1", "transcribe", {"meeting_id": "m1"})
    first = control.claim_next()
    assert control.release_claim(first) is True
    assert control.release_claim(first) is False
    again = control.claim_next()
    assert again.job_key == "m1" and again.delivery_count == first.delivery_count == 1
