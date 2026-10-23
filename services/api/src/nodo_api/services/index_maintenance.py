"""Keeping the search index and the graph honest against the store (ADR-007, ADR-021).

The store (PostgreSQL, or SQLite on a computer) is the source of truth; the search index and the
graph's evidence are derived from it. They drift: a search outage during indexing, a cascade that
stopped half way after a delete, a transcript corrected after it was indexed, a standby region whose
index never existed. This service compares them with the store, meeting by meeting, and in repair
mode makes them match again.

`verify` only reads. `repair` rebuilds what is missing or stale, removes what should be gone, and
asks for a re-analysis (the normal reanalyze job, which keeps reviewed decisions) where the graph
cites text that does not exist. A meeting that fails is reported and skipped; the run goes on.
Reports carry ids, kinds and counts, never text."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from nodo_api.domain.maintenance import (
    Failure,
    Finding,
    FindingKind,
    IndexReport,
    MaintenanceMeeting,
)
from nodo_api.domain.models import MeetingState, TranscriptSegment
from nodo_api.errors import NotFoundError
from nodo_api.ports import AuditLog, GraphStore, JobQueue, Retriever, TranscriptRepository
from nodo_api.ports.protocols import MaintenanceStore
from nodo_api.telemetry import instruments, span

logger = logging.getLogger("nodo_index_maintenance")

DEFAULT_BATCH = 100
HAS_TRANSCRIPT = (MeetingState.EXTRACTING, MeetingState.REVIEW, MeetingState.READY)
REANALYZABLE = (MeetingState.REVIEW, MeetingState.READY)
REINDEX_KINDS = (
    FindingKind.MISSING_FROM_INDEX,
    FindingKind.PARTIAL_INDEX,
    FindingKind.STALE_INDEX,
)


class IndexMaintenanceService:
    def __init__(
        self,
        store: MaintenanceStore,
        transcripts: TranscriptRepository,
        retriever: Retriever,
        graph: GraphStore,
        jobs: JobQueue,
        audit: AuditLog | None = None,
    ) -> None:
        self._store = store
        self._transcripts = transcripts
        self._retriever = retriever
        self._graph = graph
        self._jobs = jobs
        self._audit = audit

    def verify(self, batch: int = DEFAULT_BATCH) -> IndexReport:
        return self._run(repair=False, batch=batch)

    def repair(self, batch: int = DEFAULT_BATCH) -> IndexReport:
        return self._run(repair=True, batch=batch)

    # --------------------------------------------------------------------------------- the run

    def _run(self, *, repair: bool, batch: int) -> IndexReport:
        report = IndexReport()
        after: tuple[str, str] | None = None
        with span("index_maintenance.run", **{"nodo.repair": repair}):
            while True:
                page = self._store.list_meetings(after, batch)
                for meeting in page:
                    self._one(meeting, report, repair)
                if len(page) < batch:
                    break
                last = page[-1]
                after = (last.workspace_id, last.meeting_id)
        for finding in report.findings:
            instruments.index_findings.add(1, {"kind": finding.kind.value})
        logger.info(
            "index_%s checked=%s skipped=%s findings=%s repaired=%s failed=%s",
            "repair" if repair else "verify",
            report.checked,
            report.skipped,
            len(report.findings),
            report.repaired,
            len(report.failed),
        )
        return report

    def _one(self, meeting: MaintenanceMeeting, report: IndexReport, repair: bool) -> None:
        try:
            found = self._inspect(meeting)
            if found is None:
                report.skipped += 1
                return
            report.checked += 1
            segments, findings = found
            for kind in findings:
                report.findings.append(Finding(kind, meeting.workspace_id, meeting.meeting_id))
            if repair and findings:
                self._fix(meeting, segments, findings)
                report.repaired += len(findings)
        except Exception as error:  # one meeting must not stop the run
            logger.error("index_maintenance_failed type=%s", type(error).__name__)
            report.failed.append(
                Failure(meeting.workspace_id, meeting.meeting_id, type(error).__name__)
            )

    # ----------------------------------------------------------------------------- inspection

    def _inspect(
        self, meeting: MaintenanceMeeting
    ) -> tuple[tuple[TranscriptSegment, ...], list[FindingKind]] | None:
        ws, mid = meeting.workspace_id, meeting.meeting_id
        if meeting.tombstoned:
            findings = []
            if self._retriever.count(ws, mid) > 0:
                findings.append(FindingKind.LEFTOVER_INDEX)
            if self._graph.evidence_segment_ids(ws, meeting.owner_id, mid):
                findings.append(FindingKind.LEFTOVER_GRAPH)
            return (), findings
        if meeting.state not in HAS_TRANSCRIPT:
            return None  # nothing to compare until the speech stage has produced a transcript
        try:
            segments = self._transcripts.latest(ws, mid)
        except NotFoundError:
            return None
        findings = []
        indexed, expected = self._retriever.count(ws, mid), len(segments)
        if indexed == 0:
            findings.append(FindingKind.MISSING_FROM_INDEX)
        elif indexed < expected:
            findings.append(FindingKind.PARTIAL_INDEX)
        elif indexed > expected:
            findings.append(FindingKind.STALE_INDEX)
        cited = self._graph.evidence_segment_ids(ws, meeting.owner_id, mid)
        if cited - {s.id for s in segments}:
            findings.append(FindingKind.DANGLING_EVIDENCE)
        return segments, findings

    # ---------------------------------------------------------------------------------- repair

    def _fix(
        self,
        meeting: MaintenanceMeeting,
        segments: tuple[TranscriptSegment, ...],
        findings: list[FindingKind],
    ) -> None:
        ws, mid = meeting.workspace_id, meeting.meeting_id
        if FindingKind.LEFTOVER_INDEX in findings:
            self._retriever.erase(ws, mid)
        if FindingKind.LEFTOVER_GRAPH in findings:
            self._graph.erase(ws, mid)
        if FindingKind.STALE_INDEX in findings:
            self._retriever.erase(ws, mid)  # extra documents can not be told apart: start clean
        if any(kind in REINDEX_KINDS for kind in findings):
            self._retriever.index(ws, meeting.owner_id, segments)
        if FindingKind.DANGLING_EVIDENCE in findings and meeting.state in REANALYZABLE:
            # One request per day: asking again before it ran changes nothing, and a later day may.
            day = datetime.now(UTC).strftime("%Y%m%d")
            self._jobs.publish_once(
                ws, f"{mid}:repair-graph:{day}", "reanalyze", {"meeting_id": mid}
            )
        for kind in findings:
            self._record(ws, mid, kind)

    def _record(self, workspace_id: str, meeting_id: str, kind: FindingKind) -> None:
        if self._audit is not None:
            meta: dict[str, Any] = {"meeting_id": meeting_id, "finding": kind.value}
            self._audit.append(workspace_id, "index_repaired", meta)
