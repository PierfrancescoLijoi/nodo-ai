"""Index maintenance on a computer: the same listing over SQLite, and a background run the app can
start and watch (ADR-021). One person, one archive, so there is no role to separate; the run is
bounded to one at a time because it embeds text and the model is shared with everything else."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.domain.maintenance import IndexReport, MaintenanceMeeting
from nodo_api.domain.models import MeetingState
from nodo_api.services.index_maintenance import IndexMaintenanceService

logger = logging.getLogger("nodo_local_maintenance")


class SqliteMaintenanceStore:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    def list_meetings(
        self, after: tuple[str, str] | None, limit: int
    ) -> tuple[MaintenanceMeeting, ...]:
        floor = after or ("", "")
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT workspace_id, id, owner_id, state, tombstoned_at FROM meetings"
                " WHERE workspace_id > ? OR (workspace_id = ? AND id > ?)"
                " ORDER BY workspace_id, id LIMIT ?",
                (floor[0], floor[0], floor[1], limit),
            ).fetchall()
        return tuple(
            MaintenanceMeeting(
                r["workspace_id"],
                r["id"],
                r["owner_id"],
                MeetingState(r["state"]),
                r["tombstoned_at"] is not None,
            )
            for r in rows
        )


@dataclass
class IndexProgress:
    state: str = "idle"  # idle | running | done | failed
    mode: str = ""  # verify | repair
    checked: int = 0
    skipped: int = 0
    repaired: int = 0
    findings: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    message: str = ""


class LocalIndexService:
    """Runs verify or repair in a thread and keeps the last report for the app to read."""

    def __init__(self, service: IndexMaintenanceService) -> None:
        self._service = service
        self._progress = IndexProgress()
        self._lock = threading.Lock()

    def progress(self) -> IndexProgress:
        with self._lock:
            copy = IndexProgress(**self._progress.__dict__)
            copy.findings, copy.failed = list(copy.findings), list(copy.failed)
            return copy

    def start(self, mode: str) -> bool:
        """Begin a run in the background. False if one is already running."""
        if mode not in ("verify", "repair"):
            raise ValueError("mode must be verify or repair")
        with self._lock:
            if self._progress.state == "running":
                return False
            self._progress = IndexProgress(state="running", mode=mode)
        threading.Thread(target=self.run, args=(mode,), name="nodo-index", daemon=True).start()
        return True

    def run(self, mode: str) -> IndexProgress:
        with self._lock:
            self._progress = IndexProgress(state="running", mode=mode)
        try:
            report = self._service.repair() if mode == "repair" else self._service.verify()
            self._finish(mode, report)
        except Exception as error:  # a run must end in a report, never in a silent thread death
            logger.error("index_run_failed type=%s", type(error).__name__)
            with self._lock:
                self._progress.state = "failed"
                self._progress.message = type(error).__name__
        return self.progress()

    def _finish(self, mode: str, report: IndexReport) -> None:
        with self._lock:
            self._progress = IndexProgress(
                state="done",
                mode=mode,
                checked=report.checked,
                skipped=report.skipped,
                repaired=report.repaired,
                findings=[
                    {"kind": f.kind.value, "meeting_id": f.meeting_id} for f in report.findings
                ],
                failed=[{"meeting_id": f.meeting_id, "reason": f.error} for f in report.failed],
            )
