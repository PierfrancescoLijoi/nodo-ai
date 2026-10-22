"""Ingestion worker: a Container Apps job started by KEDA when the queue has messages.

It drains the queue and exits once the queue stays empty, so the job scales back to zero. A message
is completed on success, abandoned for a retry on failure, and on the last delivery the meeting is
marked failed and the message is dead-lettered (Service Bus keeps it for inspection)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from nodo_api.services.ingestion import IngestionService
from nodo_api.telemetry import continued, instruments, pseudonym, span

logger = logging.getLogger("nodo_worker")

MAX_DELIVERIES = 5
IDLE_SECONDS = 30
TRANSCRIBE = "transcribe"
REANALYZE = "reanalyze"
EXPORT = "export"


class Worker:
    def __init__(
        self,
        ingestion: IngestionService,
        max_deliveries: int = MAX_DELIVERIES,
        exports: Any = None,
    ) -> None:
        self._ingestion = ingestion
        self._max = max_deliveries
        self._exports = exports

    @staticmethod
    def _record_queue_age(message: Any) -> None:
        """How long the message waited: the number the queue's autoscaling has to keep small."""
        enqueued = getattr(message, "enqueued_time_utc", None)
        if enqueued is not None:
            age = (datetime.now(UTC) - enqueued).total_seconds()
            instruments.queue_age.record(max(age, 0.0))

    def handle(self, receiver: Any, message: Any) -> str:
        """Process one message and settle it. Returns completed, retry or dead_lettered."""
        try:
            body = json.loads(b"".join(message.body).decode())
            job_type, workspace_id = body["job_type"], body["workspace_id"]
            meeting_id = body["meeting_id"]
        except (ValueError, KeyError, TypeError):
            receiver.dead_letter_message(message, reason="malformed", error_description="bad body")
            return "dead_lettered"
        if job_type not in (TRANSCRIBE, REANALYZE) and not (job_type == EXPORT and self._exports):
            receiver.dead_letter_message(message, reason="unknown_job_type")
            return "dead_lettered"
        self._record_queue_age(message)
        try:
            # Continue the trace of the request that queued this job: one trace from tap to graph.
            with (
                continued(body.get("trace")),
                span(
                    f"worker.{job_type}",
                    **{
                        "nodo.delivery": message.delivery_count,
                        "nodo.workspace": pseudonym(workspace_id),
                    },
                ),
            ):
                if job_type == TRANSCRIBE:
                    self._ingestion.process(workspace_id, meeting_id)
                elif job_type == EXPORT:
                    self._exports.build(workspace_id, body["export_id"])
                else:
                    self._ingestion.reanalyze(workspace_id, meeting_id)
        except Exception as error:
            # Log the error type only: messages and transcripts never go to the logs.
            logger.error(
                "ingestion_failed type=%s deliveries=%s",
                type(error).__name__,
                message.delivery_count,
            )
            if message.delivery_count >= self._max:
                if job_type == EXPORT:
                    self._exports.fail(workspace_id, body["export_id"])
                else:
                    self._ingestion.mark_failed(workspace_id, meeting_id)
                receiver.dead_letter_message(message, reason="max_deliveries")
                return "dead_lettered"
            receiver.abandon_message(message)
            return "retry"
        receiver.complete_message(message)
        return "completed"


def drain(worker: Worker, receiver: Any) -> int:
    """Handle messages until the receiver has been idle for its max wait. Returns the count."""
    handled = 0
    for message in receiver:
        worker.handle(receiver, message)
        handled += 1
    return handled


def main() -> None:
    from nodo_api.bootstrap import build_worker_runtime
    from nodo_api.config import Settings

    logging.basicConfig(level=logging.INFO)
    runtime = build_worker_runtime(Settings(service="worker"))
    with runtime.receiver_factory(IDLE_SECONDS) as receiver:
        handled = drain(Worker(runtime.ingestion, exports=runtime.exports), receiver)
    logger.info("worker_done handled=%s", handled)


if __name__ == "__main__":
    main()
