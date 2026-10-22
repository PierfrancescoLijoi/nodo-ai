from __future__ import annotations

import hashlib
import hmac
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from nodo_api.domain.models import Locale, Meeting, MeetingState, RecordingAttestation
from nodo_api.domain.rules import transition_meeting
from nodo_api.errors import ConflictError
from nodo_api.ports import (
    AuditLog,
    BlobStore,
    GraphStore,
    JobQueue,
    MeetingRepository,
    Retriever,
    TranscriptRepository,
)


class MeetingService:
    def __init__(
        self,
        meetings: MeetingRepository,
        transcripts: TranscriptRepository,
        blobs: BlobStore,
        jobs: JobQueue,
        retriever: Retriever,
        graph: GraphStore,
        audit: AuditLog,
        hmac_key: str,
    ) -> None:
        self._meetings = meetings
        self._transcripts = transcripts
        self._blobs = blobs
        self._jobs = jobs
        self._retriever = retriever
        self._graph = graph
        self._audit = audit
        self._hmac_key = hmac_key.encode()

    def create(
        self,
        workspace_id: str,
        user_id: str,
        idempotency_key: str,
        attestation_payload: dict[str, Any],
        project: str | None = None,
    ) -> Meeting:
        existing = self._meetings.get_by_idempotency_key(workspace_id, idempotency_key)
        if existing is not None:
            return existing
        if attestation_payload.get("acknowledged") is not True:
            raise ValueError("Recording attestation acknowledgement is required")
        meeting_id = str(uuid4())
        attestation = self._attestation(workspace_id, user_id, meeting_id, attestation_payload)
        meeting = Meeting(
            id=meeting_id,
            workspace_id=workspace_id,
            owner_id=user_id,
            created_at=datetime.now(UTC),
            state=MeetingState.UPLOADING,
            attestation=attestation,
            project=project,
        )
        return self._meetings.add(workspace_id, meeting, idempotency_key)

    def _attestation(
        self, workspace_id: str, user_id: str, meeting_id: str, payload: dict[str, Any]
    ) -> RecordingAttestation:
        acknowledged_at = datetime.now(UTC)
        audit_id = self._audit.append(
            workspace_id, "recording_attested", {"user_id": user_id, "meeting_id": meeting_id}
        )
        locale = Locale(payload["locale"])
        values = (
            user_id,
            meeting_id,
            str(payload["notice_version"]),
            acknowledged_at.isoformat(),
            str(payload["retention_policy_id"]),
            locale.value,
            audit_id,
        )
        signature = hmac.new(self._hmac_key, "|".join(values).encode(), hashlib.sha256).hexdigest()
        return RecordingAttestation(
            user_id, meeting_id, values[2], acknowledged_at, values[4], locale, audit_id, signature
        )

    def upload_session(self, workspace_id: str, meeting_id: str) -> dict[str, Any]:
        meeting = self._meetings.get(workspace_id, meeting_id)
        if meeting.state is not MeetingState.UPLOADING:
            raise ConflictError
        return self._blobs.create_upload_session(workspace_id, meeting_id)

    def complete(self, workspace_id: str, meeting_id: str) -> Meeting:
        meeting = self._meetings.get(workspace_id, meeting_id)
        if meeting.state is MeetingState.TRANSCRIBING:
            return meeting
        if meeting.state is not MeetingState.UPLOADING:
            raise ConflictError
        updated = transition_meeting(meeting, MeetingState.TRANSCRIBING)
        self._meetings.save(workspace_id, updated)
        self._jobs.publish_once(workspace_id, meeting_id, "transcribe", {"meeting_id": meeting_id})
        return updated

    def review(
        self, workspace_id: str, meeting_id: str, corrections: list[dict[str, str]]
    ) -> tuple:
        self._meetings.get(workspace_id, meeting_id)
        original = self._transcripts.latest(workspace_id, meeting_id)
        changes = {item["segment_id"]: item["text"] for item in corrections}
        next_version = original[0].version + 1
        revised = tuple(
            replace(segment, text=changes.get(segment.id, segment.text), version=next_version)
            for segment in original
        )
        saved = self._transcripts.add_version(workspace_id, meeting_id, revised)
        index = getattr(self._retriever, "index", None)
        if index:
            for segment in saved:
                index(workspace_id, segment)
        return saved

    def delete(self, workspace_id: str, meeting_id: str) -> Meeting:
        meeting = self._meetings.get(workspace_id, meeting_id)
        tombstone = replace(meeting, tombstoned_at=datetime.now(UTC))
        self._meetings.save(workspace_id, tombstone)
        self._retriever.erase(workspace_id, meeting_id)
        self._graph.erase(workspace_id, meeting_id)
        self._blobs.erase(workspace_id, meeting_id)
        self._transcripts.erase(workspace_id, meeting_id)
        self._audit.append(workspace_id, "meeting_deleted", {"meeting_id": meeting_id})
        return tombstone

    def export(self, workspace_id: str, meeting_id: str | None = None) -> str:
        if meeting_id is not None:
            self._meetings.get(workspace_id, meeting_id)
        target = meeting_id or "workspace"
        return self._jobs.publish_once(
            workspace_id, target, "export", {"meeting_id": meeting_id or ""}
        )
