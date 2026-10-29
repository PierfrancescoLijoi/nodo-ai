"""Receiving a meeting copied from a local engine (ADR-015).

The copy is idempotent by construction: the meeting keeps its id, transcript versions that already
exist are left alone, the search index is an upsert, and the graph contribution replaces the
meeting's previous one while review decisions in the package are applied again. Sending the same
package twice changes nothing; sending a newer one (a second transcript version, a new review) adds
exactly the difference. Nothing is re-transcribed: the cloud keeps the words the owner saw."""

from __future__ import annotations

from dataclasses import replace

from nodo_api.api.v1.sync_schemas import SyncPackage, SyncRef, SyncResult
from nodo_api.domain.graph import (
    EXISTING_PREFIX,
    EntityType,
    ExtractedEntity,
    ExtractedRelation,
    Extraction,
    MeetingRef,
    RelationKind,
    ReviewStatus,
    entity_id,
    meeting_entity_id,
    normalize_label,
    relation_id,
    validate_extraction,
)
from nodo_api.domain.models import Meeting, MeetingState, TranscriptSegment
from nodo_api.errors import ConflictError, NotFoundError, ValidationFailed
from nodo_api.ports import GraphStore, MeetingRepository, Retriever, TranscriptRepository
from nodo_api.services.answer_cache import AnswerCache, invalidate
from nodo_api.services.entity_index import EntityIndexer
from nodo_api.services.meetings import MeetingService, clean_speaker_name

SYSTEM_KINDS = frozenset({RelationKind.MENTIONED_IN, RelationKind.DECIDED_IN})
STATES = {"review": MeetingState.REVIEW, "ready": MeetingState.READY}


class SyncImportService:
    def __init__(
        self,
        meetings: MeetingRepository,
        transcripts: TranscriptRepository,
        retriever: Retriever,
        graph: GraphStore,
        meeting_service: MeetingService,
        entity_indexer: EntityIndexer | None = None,
        answers: AnswerCache | None = None,
    ) -> None:
        self._answers = answers
        self._meetings = meetings
        self._transcripts = transcripts
        self._retriever = retriever
        self._graph = graph
        self._service = meeting_service
        self._entities = entity_indexer

    def import_meeting(self, workspace_id: str, user_id: str, package: SyncPackage) -> SyncResult:
        versions = sorted(package.versions, key=lambda v: v.version)
        if len({v.version for v in versions}) != len(versions):
            raise ValidationFailed
        latest = versions[-1]
        segment_ids = {s.id for s in latest.segments}
        names = {k: clean_speaker_name(v) for k, v in package.speaker_names.items()}
        if not names.keys() <= {s.speaker_id for s in latest.segments}:
            raise ValidationFailed

        meeting, created = self._ensure_meeting(workspace_id, user_id, package, names)
        added = self._add_versions(workspace_id, package, versions)
        if created or added:
            self._retriever.index(workspace_id, user_id, self._segments(package, latest))
        entities, relations = self._import_graph(
            workspace_id, user_id, meeting, package, segment_ids
        )
        invalidate(self._answers, workspace_id, user_id)
        return SyncResult(
            meeting_id=meeting.id,
            created=created,
            versions_added=added,
            segments_indexed=len(latest.segments) if created or added else 0,
            entities=entities,
            relations=relations,
        )

    # ------------------------------------------------------------------ meeting

    def _ensure_meeting(
        self, workspace_id: str, user_id: str, package: SyncPackage, names: dict[str, str]
    ) -> tuple[Meeting, bool]:
        try:
            current = self._meetings.get_for_processing(workspace_id, package.meeting_id)
        except NotFoundError:
            current = None
        if current is not None and current.owner_id != user_id:
            raise ConflictError  # somebody else's meeting has this id
        if current is None:
            try:
                self._meetings.get_owned(
                    workspace_id, user_id, package.meeting_id, include_tombstoned=True
                )
            except NotFoundError:
                pass
            else:
                raise ConflictError  # deleted in the cloud: a later copy must not bring it back
        fields = {
            "state": STATES[package.state],
            "project": package.project,
            "title": package.title,
            "summary": package.summary,
            "duration_ms": package.duration_ms,
            "speaker_names": tuple(sorted(names.items())),
        }
        if current is not None:
            return self._meetings.save(workspace_id, replace(current, **fields)), False
        attestation = self._service.attest_import(
            workspace_id,
            user_id,
            package.meeting_id,
            {
                "notice_version": package.attestation.notice_version,
                "locale": package.attestation.locale.value,
                "retention_policy_id": package.attestation.retention_policy_id,
                "acknowledged_at": package.attestation.acknowledged_at,
            },
            package.attestation.audit_event_id,
            package.attestation.signature,
        )
        meeting = Meeting(
            id=package.meeting_id,
            workspace_id=workspace_id,
            owner_id=user_id,
            created_at=package.created_at,
            attestation=attestation,
            **fields,
        )
        saved = self._meetings.add(workspace_id, meeting, f"sync:{package.meeting_id}", "sync")
        return saved, True

    # --------------------------------------------------------------- transcript

    @staticmethod
    def _segments(package: SyncPackage, version) -> tuple[TranscriptSegment, ...]:
        return tuple(
            TranscriptSegment(
                s.id,
                package.meeting_id,
                s.speaker_id,
                s.start_ms,
                s.end_ms,
                s.text,
                s.confidence,
                version.version,
                s.locale,
                package.project,
            )
            for s in version.segments
        )

    def _add_versions(self, workspace_id: str, package: SyncPackage, versions) -> int:
        added = 0
        for version in versions:
            try:
                self._transcripts.get_version(workspace_id, package.meeting_id, version.version)
            except NotFoundError:
                self._transcripts.add_version(
                    workspace_id, package.meeting_id, self._segments(package, version)
                )
                added += 1
        return added

    # -------------------------------------------------------------------- graph

    def _import_graph(
        self,
        workspace_id: str,
        user_id: str,
        meeting: Meeting,
        package: SyncPackage,
        valid: set[str],
    ) -> tuple[int, int]:
        keys = {
            (e.type, normalize_label(e.label)): f"e{index}"
            for index, e in enumerate(package.entities)
        }
        known: dict[str, EntityType] = {}

        def endpoint(ref: SyncRef) -> str | None:
            if ref.type is None or ref.label is None:
                return None
            normalized = normalize_label(ref.label)
            if (ref.type, normalized) in keys:
                return keys[(ref.type, normalized)]
            eid = entity_id(workspace_id, user_id, ref.type, normalized)
            known[eid] = ref.type  # an older meeting's entity: the store drops it if it is absent
            return f"{EXISTING_PREFIX}{eid}"

        entities = tuple(
            ExtractedEntity(
                keys[(e.type, normalize_label(e.label))],
                e.type,
                e.label,
                e.evidence,
                effective_at=e.effective_at,
                due_at=e.due_at,
            )
            for e in package.entities
        )
        relations = []
        for rel in package.relations:
            source, target = endpoint(rel.source), endpoint(rel.target)
            if rel.kind in SYSTEM_KINDS or source is None or target is None:
                continue
            relations.append(
                ExtractedRelation(source, target, rel.kind, rel.confidence, rel.evidence)
            )
        clean = validate_extraction(
            Extraction(package.summary or "", entities, tuple(relations)), frozenset(valid), known
        )
        ref = MeetingRef(meeting.id, meeting.title or package.meeting_id, meeting.created_at)
        written = self._graph.upsert_extraction(workspace_id, user_id, ref, clean)
        self._apply_reviews(workspace_id, user_id, package)
        if self._entities is not None:
            self._entities.index_pending(
                workspace_id, user_id
            )  # findable by meaning in the cloud too
        return written.entities, written.relations

    def _apply_reviews(self, workspace_id: str, user_id: str, package: SyncPackage) -> None:
        def node(ref: SyncRef) -> str:
            if ref.type is None or ref.label is None:
                return meeting_entity_id(workspace_id, user_id, package.meeting_id)
            return entity_id(workspace_id, user_id, ref.type, normalize_label(ref.label))

        for rel in package.relations:
            if rel.status is ReviewStatus.PROPOSED:
                continue
            rid = relation_id(workspace_id, user_id, node(rel.source), rel.kind, node(rel.target))
            try:
                self._graph.review_relation(workspace_id, user_id, rid, rel.status)
            except NotFoundError:
                continue  # the receiver dropped it (for instance a link to a deleted decision)
