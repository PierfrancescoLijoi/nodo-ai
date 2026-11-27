"""Ingestion worker: a Container Apps job started by KEDA when the queue has messages.

It drains the queue and exits once the queue stays empty, so the job scales back to zero. A message
is completed on success, abandoned for a retry on failure, and on the last delivery the meeting is
marked failed and the message is dead-lettered (Service Bus keeps it for inspection)."""

from __future__ import annotations

import json
import logging
import re
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
IMPORT_SOURCE = "import_source"
IMPORT_MAX_DELIVERIES = 4  # the first attempt plus three retries (FR-3)
IMPORT_AGING_SECONDS = 600  # an import waiting this long may run between two meeting jobs
ENGINE_CRASHED = "engine_crashed"  # every delivery ended with the engine gone, not an error
_ERROR_CODE = re.compile(r"[a-z0-9_]{1,64}")


def _error_code(error: Exception) -> str:
    """A stable code for the failure: never the message, which may quote the document."""
    code = getattr(error, "code", None)
    return code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else "import_failed"


class Worker:
    def __init__(
        self,
        ingestion: IngestionService,
        max_deliveries: int = MAX_DELIVERIES,
        exports: Any = None,
        imports: Any = None,
    ) -> None:
        self._ingestion = ingestion
        self._max = max_deliveries
        self._exports = exports
        self._imports = imports

    def max_deliveries_for(self, job_type: str) -> int:
        return IMPORT_MAX_DELIVERIES if job_type == IMPORT_SOURCE else self._max

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
            # Source jobs carry source_id; meeting jobs carry meeting_id (the same id for meetings).
            # An import must name its source; exports may carry an empty meeting_id.
            subject = body.get("source_id") or body.get("meeting_id")
            if job_type == IMPORT_SOURCE:
                subject = body.get("source_id")
            if not isinstance(subject, str) or (job_type == IMPORT_SOURCE and not subject):
                raise KeyError("subject")
        except (ValueError, KeyError, TypeError, AttributeError):
            receiver.dead_letter_message(message, reason="malformed", error_description="bad body")
            return "dead_lettered"
        known = job_type in (TRANSCRIBE, REANALYZE)
        known |= job_type == EXPORT and bool(self._exports)
        known |= job_type == IMPORT_SOURCE and bool(self._imports)
        if not known:
            receiver.dead_letter_message(message, reason="unknown_job_type")
            return "dead_lettered"
        if message.delivery_count > self.max_deliveries_for(job_type):
            # Every allowed delivery was used and the engine died during each one: do not run
            # the job again, end it (local engine; Service Bus never delivers past its cap).
            return self._give_up(receiver, message, body, subject, ENGINE_CRASHED)
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
                    self._ingestion.process(workspace_id, subject)
                elif job_type == EXPORT:
                    self._exports.build(workspace_id, body["export_id"])
                elif job_type == IMPORT_SOURCE:
                    self._imports.process(workspace_id, subject)
                else:
                    self._ingestion.reanalyze(workspace_id, subject)
        except Exception as error:
            # Log the error type only: messages and transcripts never go to the logs.
            logger.error(
                "ingestion_failed type=%s deliveries=%s",
                type(error).__name__,
                message.delivery_count,
            )
            if message.delivery_count >= self.max_deliveries_for(job_type):
                return self._give_up(receiver, message, body, subject, _error_code(error))
            receiver.abandon_message(message)
            return "retry"
        receiver.complete_message(message)
        return "completed"

    def _give_up(self, receiver: Any, message: Any, body: dict, subject: str, code: str) -> str:
        """Record the failure on the job subject and dead-letter the message."""
        job_type, workspace_id = body["job_type"], body["workspace_id"]
        reason = "max_deliveries"
        try:
            if job_type == EXPORT:
                self._exports.fail(workspace_id, body["export_id"])
            elif job_type == IMPORT_SOURCE:
                self._imports.mark_failed(workspace_id, subject, code)
            else:
                self._ingestion.mark_failed(workspace_id, subject)
        except Exception as fail_error:
            # The job still ends here, marked so a sweep can find the subject left running.
            logger.error("mark_failed_failed type=%s", type(fail_error).__name__)
            reason = "mark_failed_failed"
        receiver.dead_letter_message(message, reason=reason)
        return "dead_lettered"


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
