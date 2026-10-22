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
from nodo_api.worker import Worker

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


class SqliteJobQueue:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db
        self.wake = threading.Event()

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

    def publish_count(self, workspace_id: str, job_key: str, job_type: str) -> int:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                "SELECT count(*) AS n FROM jobs WHERE workspace_id = ? AND job_key = ? AND job_type = ?",
                (workspace_id, job_key, job_type),
            ).fetchone()
        return int(row["n"])

    def claim_next(self) -> LocalMessage | None:
        now = datetime.now(UTC)
        stale = to_text(now - timedelta(seconds=CLAIM_TIMEOUT_SECONDS))
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE done_at IS NULL AND failed_reason IS NULL"
                " AND run_after <= ? AND (claimed_at IS NULL OR claimed_at < ?)"
                " ORDER BY run_after, created_at LIMIT 1",
                (to_text(now), stale),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE jobs SET claimed_at = ?, attempts = attempts + 1"
                " WHERE workspace_id = ? AND job_key = ? AND job_type = ?",
                (to_text(now), row["workspace_id"], row["job_key"], row["job_type"]),
            )
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
            row["attempts"] + 1,
        )

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

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._queue.wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

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
            self._worker.handle(self._queue, message)
