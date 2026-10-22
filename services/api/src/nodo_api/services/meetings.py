from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from nodo_api.domain.models import (
    Locale,
    Meeting,
    MeetingState,
    RecordingAttestation,
    TranscriptSegment,
)
from nodo_api.domain.rules import transition_meeting
from nodo_api.errors import ConflictError, ValidationFailed
from nodo_api.ports import (
    AuditLog,
    BlobStore,
    GraphStore,
    JobQueue,
    MeetingRepository,
    Retriever,
    TranscriptRepository,
)

SIGNATURE_VERSION = 1


class MeetingService:
    """Every operation is scoped by workspace and by the owning user."""

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
        known_notice_versions: tuple[str, ...],
        known_retention_policies: tuple[str, ...],
    ) -> None:
        self._meetings = meetings
        self._transcripts = transcripts
        self._blobs = blobs
        self._jobs = jobs
        self._retriever = retriever
        self._graph = graph
        self._audit = audit
        self._hmac_key = hmac_key.encode()
        self._notice_versions = frozenset(known_notice_versions)
        self._retention_policies = frozenset(known_retention_policies)

    def get(self, workspace_id: str, user_id: str, meeting_id: str) -> Meeting:
        return self._meetings.get_owned(workspace_id, user_id, meeting_id)

    def create(
        self,
        workspace_id: str,
        user_id: str,
        idempotency_key: str,
        attestation_payload: dict[str, Any],
        project: str | None = None,
    ) -> Meeting:
        request_hash = _request_hash(attestation_payload, project)
        existing = self._meetings.get_by_idempotency_key(workspace_id, user_id, idempotency_key)
        if existing is not None:
            meeting, stored_hash = existing
            if stored_hash != request_hash:
                raise ConflictError  # same key, different request: never answer with the old record
            return meeting
        self._validate_attestation(attestation_payload)
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
        return self._meetings.add(workspace_id, meeting, idempotency_key, request_hash)

    def _validate_attestation(self, payload: dict[str, Any]) -> None:
        # The server vouches for what it signs: only known notice texts and retention policies.
        if payload.get("acknowledged") is not True:
            raise ValidationFailed
        if payload.get("notice_version") not in self._notice_versions:
            raise ValidationFailed
        if payload.get("retention_policy_id") not in self._retention_policies:
            raise ValidationFailed

    def _attestation(
        self, workspace_id: str, user_id: str, meeting_id: str, payload: dict[str, Any]
    ) -> RecordingAttestation:
        acknowledged_at = datetime.now(UTC)
        audit_id = self._audit.append(
            workspace_id, "recording_attested", {"user_id": user_id, "meeting_id": meeting_id}
        )
        locale = Locale(payload["locale"])
        signed = {
            "v": SIGNATURE_VERSION,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "meeting_id": meeting_id,
            "notice_version": str(payload["notice_version"]),
            "locale": locale.value,
            "retention_policy_id": str(payload["retention_policy_id"]),
            "acknowledged_at": acknowledged_at.isoformat(),
            "audit_event_id": audit_id,
        }
        canonical = json.dumps(signed, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self._hmac_key, canonical, hashlib.sha256).hexdigest()
        return RecordingAttestation(
            user_id,
            meeting_id,
            signed["notice_version"],
            acknowledged_at,
            signed["retention_policy_id"],
            locale,
            audit_id,
            signature,
        )

    def upload_session(self, workspace_id: str, user_id: str, meeting_id: str) -> dict[str, Any]:
        meeting = self.get(workspace_id, user_id, meeting_id)
        if meeting.state is not MeetingState.UPLOADING:
            raise ConflictError
        return self._blobs.create_upload_session(workspace_id, meeting_id)

    def complete(self, workspace_id: str, user_id: str, meeting_id: str) -> Meeting:
        meeting = self.get(workspace_id, user_id, meeting_id)
        if meeting.state is MeetingState.TRANSCRIBING:
            return meeting
        if meeting.state is not MeetingState.UPLOADING:
            raise ConflictError
        updated = transition_meeting(meeting, MeetingState.TRANSCRIBING)
        self._meetings.save(workspace_id, updated)
        self._jobs.publish_once(workspace_id, meeting_id, "transcribe", {"meeting_id": meeting_id})
        return updated

    def review(
        self,
        workspace_id: str,
        user_id: str,
        meeting_id: str,
        corrections: list[dict[str, str]],
        base_version: int | None = None,
    ) -> tuple[TranscriptSegment, ...]:
        self.get(workspace_id, user_id, meeting_id)
        original = self._transcripts.latest(workspace_id, meeting_id)
        if base_version is not None and base_version != original[0].version:
            raise ConflictError  # someone else revised the transcript first
        changes = {item["segment_id"]: item["text"] for item in corrections}
        if not changes.keys() <= {segment.id for segment in original}:
            raise ValidationFailed
        next_version = original[0].version + 1
        revised = tuple(
            replace(segment, text=changes.get(segment.id, segment.text), version=next_version)
            for segment in original
        )
        saved = self._transcripts.add_version(workspace_id, meeting_id, revised)
        for segment in saved:
            self._retriever.index(workspace_id, segment)
        return saved

    def delete(self, workspace_id: str, user_id: str, meeting_id: str) -> Meeting:
        """Tombstone, then erase every derived copy. Safe to repeat after a partial failure."""
        meeting = self._meetings.get_owned(
            workspace_id, user_id, meeting_id, include_tombstoned=True
        )
        first_time = meeting.tombstoned_at is None
        if first_time:
            meeting = replace(meeting, tombstoned_at=datetime.now(UTC))
            self._meetings.save(workspace_id, meeting)
        self._retriever.erase(workspace_id, meeting_id)
        self._graph.erase(workspace_id, meeting_id)
        self._blobs.erase(workspace_id, meeting_id)
        self._transcripts.erase(workspace_id, meeting_id)
        if first_time:
            self._audit.append(workspace_id, "meeting_deleted", {"meeting_id": meeting_id})
        return meeting

    def export(self, workspace_id: str, user_id: str, meeting_id: str | None = None) -> str:
        if meeting_id is not None:
            self.get(workspace_id, user_id, meeting_id)
        payload = {"meeting_id": meeting_id or "", "user_id": user_id}
        # Each request is its own job: a later export must not return an earlier export.
        return self._jobs.publish_once(workspace_id, f"{user_id}:{uuid4()}", "export", payload)


def _request_hash(attestation_payload: dict[str, Any], project: str | None) -> str:
    canonical = json.dumps(
        {"attestation": attestation_payload, "project": project}, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
