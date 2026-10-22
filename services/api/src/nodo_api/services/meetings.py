from __future__ import annotations

import hashlib
import hmac
import json
import unicodedata
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from nodo_api.domain.models import (
    Locale,
    Meeting,
    MeetingState,
    RecordingAttestation,
    SpeakerSummary,
    TranscriptSegment,
)
from nodo_api.domain.rules import transition_meeting
from nodo_api.errors import ConflictError, NotFoundError, ValidationFailed
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
MAX_SPEAKER_NAME = 80
MAX_NAME_WORDS = 5
NAMEABLE = (MeetingState.REVIEW, MeetingState.READY)


def clean_speaker_name(raw: str) -> str:
    """A name is data that later reaches a model prompt, so only letters, digits, marks, spaces and
    a little punctuation are allowed: no line breaks, brackets or quotes."""
    if any(unicodedata.category(c).startswith("C") for c in raw):
        raise ValidationFailed  # line breaks and other control characters, checked before trimming
    name = " ".join(unicodedata.normalize("NFC", raw).split())
    allowed = all(
        c.isalnum() or c in " .'-\u2019" or unicodedata.category(c).startswith("M") for c in name
    )
    if (
        not name
        or len(name) > MAX_SPEAKER_NAME
        or len(name.split()) > MAX_NAME_WORDS
        or not allowed
    ):
        raise ValidationFailed
    return name


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
        title: str | None = None,
    ) -> Meeting:
        request_hash = _request_hash(attestation_payload, project, title)
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
            title=title,
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

    def attest_import(
        self,
        workspace_id: str,
        user_id: str,
        meeting_id: str,
        original: dict[str, Any],
        source_event_id: str,
        source_signature: str,
    ) -> RecordingAttestation:
        """Sign, with this deployment's key, the attestation a local copy carried. The original
        acknowledgement time is kept; the original signature (made with another key that this
        deployment can not check) is recorded by hash in the audit event, not trusted."""
        self._validate_attestation({**original, "acknowledged": True})
        return self._attestation(
            workspace_id,
            user_id,
            meeting_id,
            original,
            acknowledged_at=original["acknowledged_at"],
            event="meeting_imported",
            extra={
                "source_event_id": source_event_id[:128],
                "source_signature_sha256": hashlib.sha256(source_signature.encode()).hexdigest(),
            },
        )

    def _attestation(
        self,
        workspace_id: str,
        user_id: str,
        meeting_id: str,
        payload: dict[str, Any],
        *,
        acknowledged_at: datetime | None = None,
        event: str = "recording_attested",
        extra: dict[str, str] | None = None,
    ) -> RecordingAttestation:
        acknowledged_at = acknowledged_at or datetime.now(UTC)
        audit_id = self._audit.append(
            workspace_id, event, {"user_id": user_id, "meeting_id": meeting_id, **(extra or {})}
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
        self._retriever.index(workspace_id, user_id, saved)
        return saved

    def speakers(
        self, workspace_id: str, user_id: str, meeting: Meeting
    ) -> tuple[SpeakerSummary, ...]:
        """Who spoke, how much, and the name the owner gave them (if any)."""
        try:
            segments = self._transcripts.latest(workspace_id, meeting.id)
        except NotFoundError:
            return ()
        names = dict(meeting.speaker_names)
        totals: dict[str, list[int]] = {}
        for segment in segments:
            count, talk = totals.setdefault(segment.speaker_id, [0, 0])
            totals[segment.speaker_id] = [count + 1, talk + segment.end_ms - segment.start_ms]
        return tuple(
            SpeakerSummary(speaker, names.get(speaker), count, talk)
            for speaker, (count, talk) in sorted(totals.items(), key=lambda kv: (len(kv[0]), kv[0]))
        )

    def name_speaker(
        self, workspace_id: str, user_id: str, meeting_id: str, speaker_id: str, name: str | None
    ) -> Meeting:
        """Give an anonymous label a name, or clear it with None. The graph is rebuilt in the
        background so the person shows up in it. No voice data is kept (ADR-013)."""
        meeting = self.get(workspace_id, user_id, meeting_id)
        if meeting.state not in NAMEABLE:
            raise ConflictError
        if speaker_id not in {s.id for s in self.speakers(workspace_id, user_id, meeting)}:
            raise NotFoundError
        names = dict(meeting.speaker_names)
        if name is None:
            names.pop(speaker_id, None)
        else:
            names[speaker_id] = clean_speaker_name(name)
        updated = replace(meeting, speaker_names=tuple(sorted(names.items())))
        if updated.speaker_names == meeting.speaker_names:
            return meeting
        self._meetings.save(workspace_id, updated)
        self._audit.append(
            workspace_id, "speaker_named", {"meeting_id": meeting_id, "speaker_id": speaker_id}
        )
        # A fresh key per change: renaming back to an earlier name set must still rebuild the graph,
        # which a key derived from the names would suppress as a duplicate of the old job.
        self._jobs.publish_once(
            workspace_id,
            f"{meeting_id}:speakers:{uuid4().hex}",
            "reanalyze",
            {"meeting_id": meeting_id},
        )
        return updated

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


def _request_hash(
    attestation_payload: dict[str, Any], project: str | None, title: str | None
) -> str:
    canonical = json.dumps(
        {"attestation": attestation_payload, "project": project, "title": title},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
