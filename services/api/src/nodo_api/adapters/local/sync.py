"""Copying the local archive to an Azure deployment (ADR-015).

One direction, on demand: each local meeting becomes a package (transcript versions, speaker names,
graph contribution with review decisions, original attestation) and is sent to the cloud API with the
owner's own access token. The token comes from the app for one run and is never stored. What was
sent is remembered by content hash, so the next run sends only what changed, and a meeting deleted
locally is deleted in the cloud too."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from nodo_api.adapters.local.db import LocalDatabase, now_text
from nodo_api.adapters.local.graph import SqliteGraphStore
from nodo_api.adapters.local.repos import SqliteMeetingRepository, SqliteTranscriptRepository
from nodo_api.api.v1.sync_schemas import (
    SyncAttestation,
    SyncEntity,
    SyncPackage,
    SyncRef,
    SyncRelation,
    SyncSegment,
    SyncSource,
    SyncVersion,
)
from nodo_api.domain.graph import ExportedRef
from nodo_api.domain.models import Meeting, MeetingState

logger = logging.getLogger("nodo_sync")

PAGE = 200
REQUEST_TIMEOUT_SECONDS = 180
SYNCABLE = (MeetingState.REVIEW, MeetingState.READY)


def _ref(ref: ExportedRef) -> SyncRef:
    return SyncRef(type=ref.type, label=ref.label)


def _reason(error: Exception) -> str:
    """What to tell the owner about a failed meeting: a status code or an error name, never a body."""
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return f"http {status}" if status else type(error).__name__


def content_hash(package: SyncPackage) -> str:
    canonical = json.dumps(package.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_package(
    meeting: Meeting,
    transcripts: SqliteTranscriptRepository,
    graph: SqliteGraphStore,
    source: SyncSource,
) -> SyncPackage:
    ws, owner = meeting.workspace_id, meeting.owner_id
    versions = tuple(
        SyncVersion(
            version=number,
            segments=tuple(
                SyncSegment(
                    id=s.id,
                    speaker_id=s.speaker_id,
                    start_ms=s.start_ms,
                    end_ms=s.end_ms,
                    text=s.text,
                    confidence=min(1.0, max(0.0, s.confidence)),
                    locale=s.locale,
                )
                for s in transcripts.get_version(ws, meeting.id, number)
            ),
        )
        for number in transcripts.versions(ws, meeting.id)
    )
    exported = graph.export_meeting(ws, owner, meeting.id)
    a = meeting.attestation
    return SyncPackage(
        meeting_id=meeting.id,
        title=meeting.title,
        summary=meeting.summary,
        duration_ms=meeting.duration_ms,
        project=meeting.project,
        created_at=meeting.created_at,
        state=meeting.state.value,
        speaker_names=dict(meeting.speaker_names),
        attestation=SyncAttestation(
            notice_version=a.notice_version,
            locale=a.locale,
            retention_policy_id=a.retention_policy_id,
            acknowledged_at=a.acknowledged_at,
            audit_event_id=a.audit_event_id,
            signature=a.signature,
        ),
        versions=versions,
        entities=tuple(
            SyncEntity(
                type=e.type,
                label=e.label,
                evidence=e.evidence,
                due_at=e.due_at,
                effective_at=e.effective_at,
            )
            for e in exported.entities
        ),
        relations=tuple(
            SyncRelation(
                source=_ref(r.source),
                kind=r.kind,
                target=_ref(r.target),
                confidence=r.confidence,
                status=r.status,
                evidence=r.evidence,
            )
            for r in exported.relations
        ),
        source=source,
    )


@dataclass
class SyncReport:
    state: str = "idle"  # idle | running | done | failed
    total: int = 0
    done: int = 0
    pushed: int = 0
    unchanged: int = 0
    deleted: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)
    message: str = ""


def check_cloud_url(url: str) -> str:
    """Only https (or this computer, for tests), without credentials or a query in the address."""
    parsed = urlparse(url)
    loopback = parsed.hostname in {"127.0.0.1", "localhost"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("The cloud address must start with https://")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
        raise ValueError("The cloud address must be a plain address")
    return url.rstrip("/")


class LocalSyncService:
    def __init__(
        self,
        db: LocalDatabase,
        meetings: SqliteMeetingRepository,
        transcripts: SqliteTranscriptRepository,
        graph: SqliteGraphStore,
        source: SyncSource,
        workspace_id: str,
        owner_id: str,
        http_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._db = db
        self._meetings = meetings
        self._transcripts = transcripts
        self._graph = graph
        self._source = source
        self._ws = workspace_id
        self._owner = owner_id
        self._http = http_factory or (lambda: httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS))
        self._report = SyncReport()
        self._force = False
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- state

    def report(self) -> SyncReport:
        with self._lock:
            return SyncReport(**{**self._report.__dict__, "failed": list(self._report.failed)})

    def start(self, cloud_url: str, access_token: str, force: bool = False) -> bool:
        """Begin a run in the background. False if one is already running."""
        url = check_cloud_url(cloud_url)
        with self._lock:
            if self._report.state == "running":
                return False
            self._report = SyncReport(state="running")
        threading.Thread(
            target=self.run, args=(url, access_token, force), name="nodo-sync", daemon=True
        ).start()
        return True

    # ------------------------------------------------------------------- run

    def run(self, cloud_url: str, access_token: str, force: bool = False) -> SyncReport:
        """Copy what changed (everything, with force). Blocks until done; start() runs it in a thread."""
        url = check_cloud_url(cloud_url)
        self._force = force
        with self._lock:
            self._report = SyncReport(state="running")
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        try:
            with self._http() as client:
                candidates = self._candidates()
                removed = self._removed(url)
                with self._lock:
                    self._report.state = "running"
                    self._report.total = len(candidates) + len(removed)
                for meeting in candidates:
                    self._push(client, url, headers, meeting)
                for meeting_id in removed:
                    self._remove(client, url, headers, meeting_id)
            with self._lock:
                self._report.state = "done"
        except Exception as error:  # a sync must end in a report, never in a silent thread death
            logger.error("sync_failed type=%s", type(error).__name__)
            with self._lock:
                self._report.state = "failed"
                self._report.message = type(error).__name__
        return self.report()

    def _candidates(self) -> list[Meeting]:
        found: list[Meeting] = []
        cursor = None
        while True:
            page = self._meetings.list_owned(self._ws, self._owner, PAGE, cursor)
            found += [m for m in page if m.state in SYNCABLE]
            if len(page) < PAGE:
                break
            cursor = (page[-1].created_at, page[-1].id)
        return sorted(found, key=lambda m: m.created_at)  # oldest first: decisions come before the
        # ones that replace them

    def _push(self, client: httpx.Client, url: str, headers: dict, meeting: Meeting) -> None:
        try:
            package = build_package(meeting, self._transcripts, self._graph, self._source)
            digest = content_hash(package)
            if not self._force and self._known_hash(url, meeting.id) == digest:
                self._count("unchanged")
                return
            response = client.put(
                f"{url}/v1/sync/meetings/{meeting.id}",
                headers=headers,
                content=package.model_dump_json(),
            )
            response.raise_for_status()
            self._remember(url, meeting.id, digest)
            self._count("pushed")
        except Exception as error:  # one meeting failing must not stop the others
            self._fail(meeting.id, _reason(error))

    def _remove(self, client: httpx.Client, url: str, headers: dict, meeting_id: str) -> None:
        try:
            response = client.delete(f"{url}/v1/meetings/{meeting_id}", headers=headers)
            if response.status_code not in (200, 204, 404):
                response.raise_for_status()
            self._forget(url, meeting_id)
            self._count("deleted")
        except Exception as error:
            self._fail(meeting_id, _reason(error))

    # ---------------------------------------------------------------- bookkeeping

    def _removed(self, url: str) -> list[str]:
        """Meetings that were copied and have since been deleted here."""
        with self._db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT s.meeting_id FROM sync_state s JOIN meetings m"
                " ON m.workspace_id = s.workspace_id AND m.id = s.meeting_id"
                " WHERE s.workspace_id = ? AND s.cloud_url = ? AND m.tombstoned_at IS NOT NULL",
                (self._ws, url),
            ).fetchall()
        return [r["meeting_id"] for r in rows]

    def _known_hash(self, url: str, meeting_id: str) -> str | None:
        with self._db.transaction(write=False) as conn:
            row = conn.execute(
                "SELECT content_hash FROM sync_state WHERE workspace_id = ? AND meeting_id = ?"
                " AND cloud_url = ?",
                (self._ws, meeting_id, url),
            ).fetchone()
        return row["content_hash"] if row else None

    def _remember(self, url: str, meeting_id: str, digest: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO sync_state (workspace_id, meeting_id, cloud_url, content_hash, synced_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (workspace_id, meeting_id, cloud_url)"
                " DO UPDATE SET content_hash = excluded.content_hash, synced_at = excluded.synced_at",
                (self._ws, meeting_id, url, digest, now_text()),
            )

    def _forget(self, url: str, meeting_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM sync_state WHERE workspace_id = ? AND meeting_id = ? AND cloud_url = ?",
                (self._ws, meeting_id, url),
            )

    def _count(self, what: str) -> None:
        with self._lock:
            setattr(self._report, what, getattr(self._report, what) + 1)
            self._report.done += 1

    def _fail(self, meeting_id: str, reason: str) -> None:
        with self._lock:
            self._report.failed.append({"meeting_id": meeting_id, "reason": reason})
            self._report.done += 1
