"""Erasing audio when its retention period ends (ADR-019).

Idempotent and safe to run at any time and any number of times: a meeting is marked only after its
audio is gone, so a crash in between is repeated, never skipped. One failing meeting does not stop
the others. Every erasure leaves an audit event (ids only) and a metric.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from nodo_api.ports import AuditLog, BlobStore, RetentionStore
from nodo_api.telemetry import instruments, span

logger = logging.getLogger("nodo_retention")
BATCH = 500


@dataclass(frozen=True, slots=True)
class SweepResult:
    erased: int = 0
    failed: int = 0
    more: bool = False  # a full batch was found: there may be more waiting


class RetentionService:
    def __init__(self, store: RetentionStore, blobs: BlobStore, audit: AuditLog) -> None:
        self._store = store
        self._blobs = blobs
        self._audit = audit

    def sweep(self, now: datetime | None = None, limit: int = BATCH) -> SweepResult:
        moment = now or datetime.now(UTC)
        with span("retention.sweep") as current:
            due = self._store.audio_due(moment, limit)
            erased = failed = 0
            for item in due:
                try:
                    self._blobs.erase(item.workspace_id, item.meeting_id)
                    self._store.mark_audio_erased(item.workspace_id, item.meeting_id, moment)
                    self._audit.append(
                        item.workspace_id,
                        "audio_expired",
                        {"meeting_id": item.meeting_id, "policy": item.policy_id},
                    )
                    erased += 1
                except Exception as error:  # one bad meeting must not block the rest
                    failed += 1
                    logger.error("retention_failed type=%s", type(error).__name__)
            current.set_attribute("nodo.erased", erased)
            current.set_attribute("nodo.failed", failed)
        if erased:
            instruments.retention_deleted.add(erased)
        return SweepResult(erased, failed, more=len(due) == limit)

    def sweep_all(self, now: datetime | None = None) -> SweepResult:
        """Keep sweeping while full batches come back (a long outage leaves a backlog)."""
        total = SweepResult()
        while True:
            batch = self.sweep(now)
            total = SweepResult(total.erased + batch.erased, total.failed + batch.failed)
            if not batch.more or batch.erased == 0:
                return total
