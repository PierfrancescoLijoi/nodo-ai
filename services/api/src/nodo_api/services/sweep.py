"""Finding the work a failure left behind (ADR-022).

Two things get lost in a system of queues. A job can be recorded and never sent (the process died
between the two, the queue was unreachable): the outbox still has it. A message can be lost after
it was sent (a purge, a misconfigured dead-letter, a region failover that does not replicate
messages in flight): the meeting just stays in processing. This service looks for both, on a
schedule, and asks again. Asking again is always safe: the job identity is the key, and processing
is idempotent at every stage. Ids and counts only."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from nodo_api.domain.maintenance import Failure, SweepReport
from nodo_api.ports import AuditLog, JobQueue
from nodo_api.ports.protocols import MaintenanceStore

logger = logging.getLogger("nodo_sweep")

OUTBOX_GRACE_SECONDS = 600  # a job recorded this recently is probably being sent right now
STUCK_AFTER_SECONDS = 10_800  # longer than the speech budget (2 h) plus extraction
PAGE = 500


class SweepService:
    def __init__(
        self,
        store: MaintenanceStore,
        jobs: JobQueue,
        audit: AuditLog | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        outbox_grace_seconds: int = OUTBOX_GRACE_SECONDS,
        stuck_after_seconds: int = STUCK_AFTER_SECONDS,
    ) -> None:
        self._store = store
        self._jobs = jobs
        self._audit = audit
        self._clock = clock
        self._grace = outbox_grace_seconds
        self._stuck = stuck_after_seconds

    def run(self) -> SweepReport:
        report = SweepReport()
        now = self._clock()
        for job in self._store.unpublished_jobs(now - timedelta(seconds=self._grace), PAGE):
            try:
                self._jobs.publish_once(job.workspace_id, job.job_key, job.job_type, job.payload)
                report.republished += 1
            except Exception as error:
                logger.error("sweep_republish_failed type=%s", type(error).__name__)
                report.failed.append(Failure(job.workspace_id, job.job_key, type(error).__name__))
        day = now.strftime("%Y%m%d")
        for meeting in self._store.stuck_meetings(now - timedelta(seconds=self._stuck), PAGE):
            try:
                # One request per day per meeting: asking twice before it ran changes nothing.
                self._jobs.publish_once(
                    meeting.workspace_id,
                    f"{meeting.meeting_id}:sweep:{day}",
                    "transcribe",
                    {"meeting_id": meeting.meeting_id},
                )
                if self._audit is not None:
                    self._audit.append(
                        meeting.workspace_id,
                        "meeting_restarted",
                        {"meeting_id": meeting.meeting_id},
                    )
                report.restarted += 1
            except Exception as error:
                logger.error("sweep_restart_failed type=%s", type(error).__name__)
                report.failed.append(
                    Failure(meeting.workspace_id, meeting.meeting_id, type(error).__name__)
                )
        logger.info(
            "sweep republished=%s restarted=%s failed=%s",
            report.republished,
            report.restarted,
            len(report.failed),
        )
        return report
