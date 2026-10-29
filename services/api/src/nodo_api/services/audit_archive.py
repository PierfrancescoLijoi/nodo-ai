"""Copying the audit trail to storage nobody can change (ADR-022; GDPR art. 5(2) and 32).

The database keeps the audit events so the application can add them; a person with database access
could still alter them. Every hour this job moves them, in order, into an account with a time-based
retention policy: once written, a file can neither be altered nor deleted before the period ends.

Each file is JSON lines. The first line is a header that carries the SHA-256 of the previous one, so
reading the files in order proves none was removed or changed (`verify_chain`). Writing is
create-only and the name and content of a batch are fully determined by where the previous batch
ended, so a run that crashed after writing and before moving its cursor does the same thing again
and finds the file already there. Events from the last few minutes wait: a transaction that began
earlier may still commit with an earlier timestamp. Events hold identifiers and event types only;
the archive never sees a transcript, a title or a name."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

logger = logging.getLogger("nodo_audit_archive")

FORMAT = "nodo-audit-v1"
LAG_SECONDS = 300
BATCH = 1000


@dataclass(frozen=True, slots=True)
class ArchivedEvent:
    workspace_id: str
    id: str
    event_type: str
    metadata: dict[str, Any]
    occurred_at: datetime

    @property
    def key(self) -> tuple[datetime, str, str]:
        return (self.occurred_at, self.workspace_id, self.id)


@dataclass(frozen=True, slots=True)
class ArchiveCursor:
    occurred_at: datetime
    workspace_id: str
    event_id: str

    @property
    def key(self) -> tuple[datetime, str, str]:
        return (self.occurred_at, self.workspace_id, self.event_id)


class AuditSource(Protocol):
    def read_after(
        self, cursor: ArchiveCursor | None, before: datetime, limit: int
    ) -> Sequence[ArchivedEvent]: ...

    def load(self) -> tuple[ArchiveCursor | None, str]:
        """Where the last batch ended, and the SHA-256 of the file that ended there."""
        ...

    def save(self, cursor: ArchiveCursor, sha256: str, count: int) -> None: ...


class AuditSink(Protocol):
    def put(self, name: str, data: bytes) -> bool:
        """Create the file; False if it already exists (nothing is overwritten, ever)."""
        ...


@dataclass(slots=True)
class ArchiveReport:
    archived: int = 0
    files: int = 0


def _line(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _batch_name(first: ArchivedEvent, last: ArchivedEvent) -> str:
    tail = hashlib.sha256("|".join(map(str, last.key)).encode()).hexdigest()[:16]
    return f"{first.occurred_at:%Y/%m/%d}/{first.occurred_at:%H%M%S%f}-{tail}.jsonl"


def build_batch(events: Sequence[ArchivedEvent], previous_sha256: str) -> bytes:
    first, last = events[0], events[-1]
    header = {
        "format": FORMAT,
        "previous_sha256": previous_sha256,
        "count": len(events),
        "first": f"{first.occurred_at.astimezone(UTC).isoformat()}|{first.workspace_id}|{first.id}",
        "last": f"{last.occurred_at.astimezone(UTC).isoformat()}|{last.workspace_id}|{last.id}",
    }
    lines = [_line(header)] + [
        _line(
            {
                "workspace_id": e.workspace_id,
                "id": e.id,
                "event_type": e.event_type,
                "metadata": e.metadata,
                "occurred_at": e.occurred_at.astimezone(UTC).isoformat(),
            }
        )
        for e in events
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def verify_chain(files: Sequence[bytes], first_previous: str = "") -> bool:
    """True when each file names the hash of the one before it, from `first_previous` on."""
    expected = first_previous
    for data in files:
        try:
            header = json.loads(data.decode("utf-8").split("\n", 1)[0])
        except ValueError:
            return False
        if header.get("format") != FORMAT or header.get("previous_sha256") != expected:
            return False
        expected = hashlib.sha256(data).hexdigest()
    return True


class AuditArchiveService:
    def __init__(
        self,
        source: AuditSource,
        sink: AuditSink,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        lag_seconds: int = LAG_SECONDS,
        batch: int = BATCH,
    ) -> None:
        self._source = source
        self._sink = sink
        self._clock = clock
        self._lag = lag_seconds
        self._batch = batch

    def run(self) -> ArchiveReport:
        report = ArchiveReport()
        before = self._clock() - timedelta(seconds=self._lag)
        cursor, previous = self._source.load()
        while True:
            events = self._source.read_after(cursor, before, self._batch)
            if not events:
                break
            data = build_batch(events, previous)
            self._sink.put(_batch_name(events[0], events[-1]), data)
            digest = hashlib.sha256(data).hexdigest()
            last = events[-1]
            cursor = ArchiveCursor(last.occurred_at, last.workspace_id, last.id)
            self._source.save(cursor, digest, len(events))
            previous = digest
            report.archived += len(events)
            report.files += 1
            if len(events) < self._batch:
                break
        logger.info("audit_archive archived=%s files=%s", report.archived, report.files)
        return report
