"""A person's data, handed back (ADR-019; GDPR articles 15 and 20).

The request is answered asynchronously: the API records it and queues a job; the worker gathers the
data, stores one JSON file and marks the request ready; the person downloads it through the API
(which checks, again, that it is theirs and that it has not expired). Nothing about the content is
logged or put in telemetry. A copy lives for 24 hours in storage and is then removed by the storage
lifecycle rule and refused by the API."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from nodo_api.domain.exports import FORMAT, Export, ExportState
from nodo_api.errors import ConflictError, NotFoundError
from nodo_api.ports import (
    AuditLog,
    BlobStore,
    ExportStore,
    GraphStore,
    JobQueue,
    MeetingRepository,
    TranscriptRepository,
)
from nodo_api.telemetry import instruments, span

logger = logging.getLogger("nodo_exports")
PAGE = 200
MAX_MEETINGS = 5_000  # beyond this an export is a project, not a request


def _plain(value: Any) -> Any:
    """Dataclasses, enums and datetimes to JSON types."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_plain(v) for v in value]
    return value


class ExportService:
    def __init__(
        self,
        exports: ExportStore,
        meetings: MeetingRepository,
        transcripts: TranscriptRepository,
        graph: GraphStore,
        blobs: BlobStore,
        jobs: JobQueue,
        audit: AuditLog,
    ) -> None:
        self._exports = exports
        self._meetings = meetings
        self._transcripts = transcripts
        self._graph = graph
        self._blobs = blobs
        self._jobs = jobs
        self._audit = audit

    # --------------------------------------------------------------------------- the request

    def request(self, workspace_id: str, user_id: str, meeting_id: str | None) -> Export:
        if meeting_id is not None:
            self._meetings.get_owned(workspace_id, user_id, meeting_id)  # raises NotFoundError
        export = self._exports.create(workspace_id, user_id, meeting_id, datetime.now(UTC))
        self._audit.append(workspace_id, "data_export_requested", {"export_id": export.id})
        self._jobs.publish_once(
            workspace_id,
            f"export:{export.id}",
            "export",
            {"export_id": export.id, "meeting_id": meeting_id or ""},
        )
        return export

    def status(self, workspace_id: str, user_id: str, export_id: str) -> Export:
        return self._exports.get(workspace_id, user_id, export_id)

    def download(self, workspace_id: str, user_id: str, export_id: str) -> bytes:
        export = self._exports.get(workspace_id, user_id, export_id)
        if export.state is not ExportState.READY or export.blob_name is None:
            raise ConflictError
        if export.expires_at <= datetime.now(UTC):
            raise NotFoundError  # expired: as good as gone
        self._audit.append(workspace_id, "data_export_downloaded", {"export_id": export.id})
        return self._blobs.get_export(workspace_id, export.blob_name)

    # ----------------------------------------------------------------------------- the job

    def build(self, workspace_id: str, export_id: str) -> None:
        """Gather, store, mark ready. Safe to repeat: the file name is the export id."""
        export = self._exports.get_for_processing(workspace_id, export_id)
        if export.state is ExportState.READY:
            return
        self._exports.set_state(workspace_id, export_id, ExportState.RUNNING)
        with span("export.build"):
            document = self._document(workspace_id, export)
            data = json.dumps(document, ensure_ascii=False, indent=1).encode("utf-8")
            name = self._blobs.put_export(workspace_id, f"{export_id}.json", data)
        self._exports.set_state(workspace_id, export_id, ExportState.READY, name, len(data))
        instruments.exports.add(1)

    def fail(self, workspace_id: str, export_id: str) -> None:
        try:
            self._exports.set_state(workspace_id, export_id, ExportState.FAILED)
        except Exception as error:  # a failing marker must not hide the original failure
            logger.error("export_fail_mark_failed type=%s", type(error).__name__)

    def _document(self, workspace_id: str, export: Export) -> dict[str, Any]:
        meetings = self._owned_meetings(workspace_id, export)
        digests = self._graph.meeting_digests(
            workspace_id, export.owner_id, [m.id for m in meetings]
        )
        return {
            "format": FORMAT,
            "generated_at": _plain(datetime.now(UTC)),
            "owner": export.owner_id,
            "meetings": [self._meeting(workspace_id, m, digests.get(m.id)) for m in meetings],
        }

    def _owned_meetings(self, workspace_id: str, export: Export) -> list[Any]:
        if export.meeting_id is not None:
            return [self._meetings.get_owned(workspace_id, export.owner_id, export.meeting_id)]
        found: list[Any] = []
        cursor = None
        while len(found) < MAX_MEETINGS:
            page = self._meetings.list_owned(workspace_id, export.owner_id, PAGE, cursor)
            found += page
            if len(page) < PAGE:
                break
            cursor = (page[-1].created_at, page[-1].id)
        return found

    def _meeting(self, workspace_id: str, meeting: Any, digest: Any) -> dict[str, Any]:
        try:
            latest = self._transcripts.latest(workspace_id, meeting.id)
            versions = [
                {
                    "version": v,
                    "segments": _plain(self._transcripts.get_version(workspace_id, meeting.id, v)),
                }
                for v in range(1, latest[0].version + 1)
            ]
        except NotFoundError:
            versions = []  # a recording that never produced a transcript
        attestation = meeting.attestation
        return {
            "id": meeting.id,
            "title": meeting.title,
            "summary": meeting.summary,
            "state": meeting.state.value,
            "project": meeting.project,
            "created_at": _plain(meeting.created_at),
            "duration_ms": meeting.duration_ms,
            "speaker_names": dict(meeting.speaker_names),
            "recording_attestation": {
                "notice_version": attestation.notice_version,
                "acknowledged_at": _plain(attestation.acknowledged_at),
                "retention_policy_id": attestation.retention_policy_id,
                "locale": attestation.locale.value,
            },
            "transcript_versions": versions,
            "insights": _plain(digest) if digest is not None else None,
        }
