"""The audit trail, copied to storage nobody can change (ADR-022).

Events leave the database in order, in batches, as JSON lines; every batch names the hash of the one
before it, so a deleted or altered file shows when the chain is read. Writing is create-only and
repeating a run changes nothing."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from nodo_api.services.audit_archive import (
    ArchiveCursor,
    ArchivedEvent,
    AuditArchiveService,
    verify_chain,
)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


class Source:
    def __init__(self, events: list[ArchivedEvent]) -> None:
        self.events = sorted(events, key=lambda e: e.key)
        self.cursor: ArchiveCursor | None = None
        self.last_hash = ""
        self.saved = 0

    def read_after(self, cursor, before, limit):
        rest = [
            e
            for e in self.events
            if (cursor is None or e.key > cursor.key) and e.occurred_at < before
        ]
        return tuple(rest[:limit])

    def load(self):
        return self.cursor, self.last_hash

    def save(self, cursor, sha256, count):
        self.cursor, self.last_hash = cursor, sha256
        self.saved += count


class Sink:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.fail = False

    def put(self, name: str, data: bytes) -> bool:
        if self.fail:
            raise ConnectionError("storage unavailable")
        if name in self.files:
            return False
        self.files[name] = data
        return True


def event(n: int, minutes_ago: int = 60) -> ArchivedEvent:
    when = NOW - timedelta(minutes=minutes_ago, seconds=100 - n)
    return ArchivedEvent("ws-a", f"e{n:03d}", "recording_attested", {"user_id": f"u{n}"}, when)


def service(source, sink, batch=1000):
    return AuditArchiveService(source, sink, clock=lambda: NOW, lag_seconds=300, batch=batch)


def test_events_become_json_lines_with_a_header_and_the_cursor_moves_on() -> None:
    source, sink = Source([event(1), event(2), event(3)]), Sink()
    report = service(source, sink).run()
    assert report.archived == 3 and report.files == 1
    ((name, data),) = sink.files.items()
    assert name.endswith(".jsonl") and name.startswith("2026/10/23/")
    header, *lines = [json.loads(line) for line in data.decode().splitlines()]
    assert header["format"] == "nodo-audit-v1" and header["count"] == 3
    assert header["previous_sha256"] == ""
    assert [line["id"] for line in lines] == ["e001", "e002", "e003"]
    assert set(lines[0]) == {"workspace_id", "id", "event_type", "metadata", "occurred_at"}
    assert source.cursor.event_id == "e003" and source.last_hash == hashlib.sha256(data).hexdigest()


def test_a_second_run_with_nothing_new_writes_nothing() -> None:
    source, sink = Source([event(1)]), Sink()
    service(source, sink).run()
    again = service(source, sink).run()
    assert again.archived == 0 and again.files == 0 and len(sink.files) == 1


def test_events_from_the_last_minutes_wait_because_a_slower_transaction_may_still_commit() -> None:
    source, sink = Source([event(1, minutes_ago=2), event(2, minutes_ago=60)]), Sink()
    report = service(source, sink).run()
    assert report.archived == 1
    assert source.cursor.event_id == "e002"


def test_a_long_backlog_is_written_in_batches_that_each_name_the_hash_before() -> None:
    source, sink = Source([event(n) for n in range(1, 6)]), Sink()
    report = service(source, sink, batch=2).run()
    assert report.archived == 5 and report.files == 3
    ordered = [sink.files[name] for name in sorted(sink.files)]
    assert verify_chain(ordered)
    previous = [json.loads(data.decode().splitlines()[0])["previous_sha256"] for data in ordered]
    assert previous[0] == "" and previous[1] == hashlib.sha256(ordered[0]).hexdigest()


def test_the_chain_notices_a_missing_or_changed_file() -> None:
    source, sink = Source([event(n) for n in range(1, 6)]), Sink()
    service(source, sink, batch=2).run()
    ordered = [sink.files[name] for name in sorted(sink.files)]
    assert not verify_chain([ordered[0], ordered[2]])  # one removed
    tampered = ordered[1].replace(b"u3", b"xx")
    assert not verify_chain([ordered[0], tampered, ordered[2]])  # one altered
    assert verify_chain([])


def test_a_crash_after_the_file_was_written_but_before_the_cursor_moved_adds_no_duplicate() -> None:
    source, sink = Source([event(1), event(2)]), Sink()
    original_save = source.save
    source.save = lambda *a: (_ for _ in ()).throw(RuntimeError("crash"))
    with pytest.raises(RuntimeError):
        service(source, sink).run()
    assert len(sink.files) == 1 and source.cursor is None
    source.save = original_save
    report = service(source, sink).run()
    assert len(sink.files) == 1  # the same bytes under the same name: skipped
    assert report.archived == 2 and source.cursor.event_id == "e002"
    assert source.last_hash == hashlib.sha256(next(iter(sink.files.values()))).hexdigest()


def test_a_storage_failure_stops_the_run_and_leaves_the_cursor_alone() -> None:
    source, sink = Source([event(1)]), Sink()
    sink.fail = True
    with pytest.raises(ConnectionError):
        service(source, sink).run()
    assert source.cursor is None
    sink.fail = False
    assert service(source, sink).run().archived == 1
