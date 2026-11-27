"""The local job queue: a table, a claiming query and a worker thread.

Same semantics the Azure worker relies on: a job exists once per (workspace, key, type), a message
is completed, abandoned for a retry later, or dead-lettered after too many deliveries. The existing
`Worker` class handles the messages unchanged; this module only supplies messages and a receiver."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from nodo_api.adapters.local.db import LocalDatabase, now_text, to_text
from nodo_api.worker import IMPORT_AGING_SECONDS, IMPORT_SOURCE, Worker

logger = logging.getLogger("nodo_local_queue")

CLAIM_TIMEOUT_SECONDS = 3600  # a claim older than this was left by a crash and is handed out again
RETRY_BASE_SECONDS = 5
POLL_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class LocalMessage:
    workspace_id: str
    job_key: str
    job_type: str
    body: list[bytes]
    delivery_count: int
    claimed_at: str | None = None  # the claim token: release_claim matches only this claim


class SqliteJobQueue:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db
        self.wake = threading.Event()
        self._started = now_text()
        self._last_was_import = False  # the priority rule below alternates on this

    def publish_once(
        self, workspace_id: str, job_key: str, job_type: str, payload: dict[str, Any]
    ) -> str:
        stamp = now_text()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (workspace_id, job_key, job_type, id, payload, created_at,"
                " run_after) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (workspace_id, job_key, job_type, str(uuid4()), json.dumps(payload), stamp, stamp),
            )
            row = conn.execute(
                "SELECT id FROM jobs WHERE workspace_id = ? AND job_key = ? AND job_type = ?",
                (workspace_id, job_key, job_type),
            ).fetchone()
        self.wake.set()
        return row["id"]

    def publish_later(
        self,
        workspace_id: str,
        job_key: str,
        job_type: str,
        payload: dict[str, Any],
        delay_seconds: int,
    ) -> None:
        now = datetime.now(UTC)
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (workspace_id, job_key, job_type, id, payload, created_at,"
                " run_after) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    job_key,
                    job_type,
                    str(uuid4()),
                    json.dumps(payload),
                    to_text(now),
                    to_text(now + timedelta(seconds=delay_seconds)),
                ),
            )
        self.wake.set()

    def publish_count(self, workspace_id: str, job_key: str, job_type: str) -> int:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                "SELECT count(*) AS n FROM jobs WHERE workspace_id = ? AND job_key = ? AND job_type = ?",
                (workspace_id, job_key, job_type),
            ).fetchone()
        return int(row["n"])

    def claim_next(self, now: datetime | None = None) -> LocalMessage | None:
        """Claim the next ready job in one statement, so two claimers never get the same job.

        Meeting jobs go first. An import that has waited IMPORT_AGING_SECONDS ranks with them by
        age, but only when the previous claim was not an import: at most one aged import runs
        between meeting jobs, so imports are never starved and a backlog of them never holds a
        meeting back by more than one import."""
        now = now or datetime.now(UTC)
        stale = to_text(now - timedelta(seconds=CLAIM_TIMEOUT_SECONDS))
        aged = to_text(now - timedelta(seconds=IMPORT_AGING_SECONDS))
        with self._db.transaction() as conn:
            rows = conn.execute(
                "UPDATE jobs SET claimed_at = ?, attempts = attempts + 1 WHERE rowid = ("
                " SELECT rowid FROM jobs WHERE done_at IS NULL AND failed_reason IS NULL"
                " AND run_after <= ? AND (claimed_at IS NULL OR claimed_at < ?)"
                " ORDER BY (job_type = ? AND NOT (? AND run_after <= ?)),"
                " run_after, created_at, rowid LIMIT 1"
                ") RETURNING *",
                (
                    to_text(now),
                    to_text(now),
                    stale,
                    IMPORT_SOURCE,
                    not self._last_was_import,
                    aged,
                ),
            ).fetchall()
        if not rows:
            return None
        row = rows[0]
        self._last_was_import = row["job_type"] == IMPORT_SOURCE
        body = {
            "job_id": row["id"],
            "workspace_id": row["workspace_id"],
            "job_key": row["job_key"],
            "job_type": row["job_type"],
            **json.loads(row["payload"]),
        }
        return LocalMessage(
            row["workspace_id"],
            row["job_key"],
            row["job_type"],
            [json.dumps(body).encode()],
            row["attempts"],  # RETURNING gives the row after the increment
            row["claimed_at"],
        )

    def release_claim(self, message: LocalMessage) -> bool:
        """Put back the job of a graceful stop and refund its delivery: closing the app is not a
        failure. Matches only this claim of an open job, so a job the worker settled meanwhile
        (done, retry scheduled, dead-lettered) or claimed again is never touched."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET claimed_at = NULL, attempts = max(attempts - 1, 0)"
                " WHERE workspace_id = ? AND job_key = ? AND job_type = ? AND claimed_at = ?"
                " AND done_at IS NULL AND failed_reason IS NULL",
                (message.workspace_id, message.job_key, message.job_type, message.claimed_at),
            )
        return cursor.rowcount == 1

    def release_claims(self) -> int:
        """Hand back jobs a crashed engine left claimed. The crash keeps its delivery, so a job
        that kills the engine every time still reaches its cap and ends failed.

        Only claims older than this queue are released: a claim this engine made is live."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET claimed_at = NULL"
                " WHERE claimed_at IS NOT NULL AND claimed_at < ?"
                " AND done_at IS NULL AND failed_reason IS NULL",
                (self._started,),
            )
        return cursor.rowcount

    def cancel(self, workspace_id: str, job_key: str, job_type: str) -> bool:
        """Cancel a job nobody has claimed. False otherwise: a running import is cancelled
        cooperatively through ImportJobRepository.request_cancel."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET failed_reason = 'cancelled' WHERE workspace_id = ? AND job_key = ?"
                " AND job_type = ? AND claimed_at IS NULL AND done_at IS NULL"
                " AND failed_reason IS NULL",
                (workspace_id, job_key, job_type),
            )
        return cursor.rowcount == 1

    def discard(self, workspace_id: str, job_type: str, key_prefix: str) -> int:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM jobs WHERE workspace_id = ? AND job_type = ?"
                " AND substr(job_key, 1, ?) = ?",
                (workspace_id, job_type, len(key_prefix), key_prefix),
            )
        return cursor.rowcount

    def _update(self, message: LocalMessage, sql: str, params: tuple) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE jobs SET {sql} WHERE workspace_id = ? AND job_key = ? AND job_type = ?",
                (*params, message.workspace_id, message.job_key, message.job_type),
            )

    def complete_message(self, message: LocalMessage) -> None:
        self._update(message, "done_at = ?", (now_text(),))

    def abandon_message(self, message: LocalMessage) -> None:
        delay = timedelta(seconds=RETRY_BASE_SECONDS * message.delivery_count)
        self._update(
            message, "claimed_at = NULL, run_after = ?", (to_text(datetime.now(UTC) + delay),)
        )

    def dead_letter_message(
        self, message: LocalMessage, reason: str | None = None, error_description: str | None = None
    ) -> None:
        self._update(message, "failed_reason = ?", (reason or "dead_lettered",))

    def pending(self) -> int:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                "SELECT count(*) AS n FROM jobs WHERE done_at IS NULL AND failed_reason IS NULL"
            ).fetchone()
        return int(row["n"])


class LocalWorkerLoop:
    """Runs queued jobs on a background thread, one at a time (models do not share the GPU)."""

    def __init__(self, queue: SqliteJobQueue, worker: Worker) -> None:
        self._queue = queue
        self._worker = worker
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="nodo-worker", daemon=True)
        self._current: LocalMessage | None = None  # the job the thread is handling

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._queue.wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout)
        current = self._current
        if current is not None and self._thread.is_alive():
            # Still busy after the wait: the app is closing, not crashing, so the job goes back
            # to the queue without costing a delivery. Only a real crash (no stop) costs one.
            try:
                if self._queue.release_claim(current):
                    logger.warning("released_running_job_on_stop")
            except Exception as error:
                logger.error("release_on_stop_failed type=%s", type(error).__name__)

    def run_pending(self) -> int:
        """Handle everything that is ready now, on the calling thread. Used by tests."""
        handled = 0
        while (message := self._queue.claim_next()) is not None:
            self._worker.handle(self._queue, message)
            handled += 1
        return handled

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                message = self._queue.claim_next()
            except Exception:
                logger.exception("claim_failed")
                self._stop.wait(POLL_SECONDS)
                continue
            if message is None:
                self._queue.wake.wait(POLL_SECONDS)
                self._queue.wake.clear()
                continue
            self._current = message
            try:
                self._worker.handle(self._queue, message)
            except Exception as error:  # keep the thread alive; the type only, never the text
                logger.error("handle_failed type=%s", type(error).__name__)
            finally:
                self._current = None
