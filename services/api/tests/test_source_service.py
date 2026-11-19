from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import tests.test_source_import as doc_module
from nodo_api.adapters.local.answers import SqliteAnswerStore
from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.fragment_index import SqliteFragmentIndex
from nodo_api.adapters.local.graph import SqliteGraphStore
from nodo_api.adapters.local.imports import SqliteImportJobRepository
from nodo_api.adapters.local.queue import SqliteJobQueue
from nodo_api.adapters.local.repos import SqliteAuditLog
from nodo_api.adapters.local.source_blobs import LocalSourceBlobStore
from nodo_api.adapters.local.sources import SqliteFragmentRepository, SqliteSourceRepository
from nodo_api.adapters.memory import (
    MemoryAuditLog,
    MemoryFragmentRepository,
    MemoryGraphStore,
    MemoryMeetingRepository,
    MemorySourceRepository,
    MemoryTranscriptRepository,
)
from nodo_api.adapters.memory.answers import MemoryAnswerStore
from nodo_api.adapters.memory.graph import _Row
from nodo_api.domain.graph import normalize_label
from nodo_api.domain.models import Locale, Meeting, MeetingState, RecordingAttestation
from nodo_api.domain.source_files import UploadRejected
from nodo_api.errors import ConflictError, NotFoundError, ValidationFailed
from nodo_api.services.answer_cache import AnswerCache
from nodo_api.services.source_import import IMPORT_SOURCE, job_key
from nodo_api.services.sources import SourceService
from tests.source_contract import OTHER, OWNER, T0, fragment, section, segment, source
from tests.source_graph_contract import handbook
from tests.test_local_adapters import HashEmbedder

WS = "workspace-a"
DELETED_AT = datetime(2026, 11, 12, 15, 30, tzinfo=UTC)


class Rig:
    def __init__(self) -> None:
        meetings = MemoryMeetingRepository()
        transcripts = MemoryTranscriptRepository()
        self.sources = MemorySourceRepository(meetings)
        self.fragments = MemoryFragmentRepository(self.sources, transcripts)
        self.graph = MemoryGraphStore()
        self.audit = MemoryAuditLog()
        self.answer_store = MemoryAnswerStore()
        answers = AnswerCache(
            self.answer_store,
            lambda _question: (1.0, 0.0),
            version="test",
            min_similarity=0.9,
        )
        self.service = SourceService(
            sources=self.sources,
            fragments=self.fragments,
            graph=self.graph,
            answers=answers,
            audit=self.audit,
            clock=lambda: DELETED_AT,
        )
        self.meetings = meetings
        self.transcripts = transcripts

    def add_document(self, source_id: str = "doc1", owner: str = OWNER) -> None:
        value = replace(source(source_id, owner=owner, ws=WS), title="Private handbook")
        self.sources.add(WS, value)
        self.fragments.add_version(
            WS,
            source_id,
            (section("s1", source_id, 0),),
            (
                fragment("f1", source_id, 0, "first", "s1"),
                fragment("f2", source_id, 1, "second", "s1"),
            ),
        )

    def seed_graph(self) -> tuple[str, str, str]:
        doc, child, parent, relations = handbook(WS)
        part = self.graph._part(WS, OWNER)
        for entity in (doc, child, parent):
            self.graph._put_entity(part, entity, normalize_label(entity.label))
        for relation in relations:
            part.relations[relation.id] = _Row(relation, next(self.graph._clock))
        return doc.id, child.id, parent.id

    def add_meeting(self, meeting_id: str = "m1") -> None:
        attestation = RecordingAttestation(
            OWNER, meeting_id, "2026-10-v1", T0, "retention-30d", Locale.EN, "a", "sig"
        )
        self.meetings.add(
            WS,
            Meeting(meeting_id, WS, OWNER, T0, MeetingState.READY, attestation),
            f"key-{meeting_id}",
            "hash",
        )
        self.transcripts.add_version(
            WS, meeting_id, (segment(meeting_id, "seg1", 0, "meeting text"),)
        )


@pytest.fixture
def rig() -> Rig:
    return Rig()


def test_delete_removes_fragments_graph_evidence_and_bumps_cache_epoch(rig: Rig) -> None:
    rig.add_document()
    graph_ids = rig.seed_graph()
    before = rig.answer_store.epoch(WS, OWNER)

    rig.service.delete(WS, OWNER, "doc1")

    with pytest.raises(NotFoundError):
        rig.sources.get_owned(WS, OWNER, "doc1")
    assert rig.sources.get_owned(WS, OWNER, "doc1", include_tombstoned=True).tombstoned_at == (
        DELETED_AT
    )
    assert rig.fragments.latest(WS, "doc1") == ()
    assert rig.fragments.outline(WS, "doc1") == ()
    assert rig.graph.evidence_segment_ids(WS, OWNER, "doc1") == frozenset()
    assert (
        rig.graph.neighborhood(
            WS,
            OWNER,
            graph_ids,
            depth=1,
            node_limit=10,
            fanout=10,
            include_proposed=True,
            as_of=None,
        ).nodes
        == ()
    )
    assert rig.answer_store.epoch(WS, OWNER) == before + 1


def test_delete_other_users_source_is_not_found(rig: Rig) -> None:
    rig.add_document()

    with pytest.raises(NotFoundError):
        rig.service.delete(WS, OTHER, "doc1")

    assert len(rig.fragments.latest(WS, "doc1")) == 2
    assert rig.sources.get_owned(WS, OWNER, "doc1").tombstoned_at is None
    assert rig.audit._events == {}


def test_delete_meeting_source_is_refused(rig: Rig) -> None:
    rig.add_meeting()

    with pytest.raises(ValidationFailed, match="use the meetings API"):
        rig.service.delete(WS, OWNER, "m1")

    assert rig.sources.get_owned(WS, OWNER, "m1").kind.value == "meeting"
    assert [item.id for item in rig.fragments.latest(WS, "m1")] == ["seg1"]
    assert rig.audit._events == {}


def test_delete_records_audit_without_title(rig: Rig) -> None:
    rig.add_document()

    rig.service.delete(WS, OWNER, "doc1")

    assert len(rig.audit._events) == 1
    event_type, metadata = next(iter(rig.audit._events.values()))
    assert event_type == "source_deleted"
    assert metadata == {"kind": "document"}
    assert "Private handbook" not in repr(rig.audit._events)


# ------------------------------------------------------------------ complete deletion (Task 12)
# The whole import stack on the memory and the SQLite stores; the text, title and file name below
# are invented markers that a scan for leftovers can find.

TITLE = "zebracrate-plan.md"
ORIGIN = "zebracrate origin note"
MARKER = "Zebracrate"
SECRET_DOC = doc_module.doc(("Plan", [f"{MARKER} quarterly ledger for ingestion queue."]))
SECRET_V2 = doc_module.doc(("Plan", [f"{MARKER} ledger second edition for ingestion queue."]))


class FailOnce:
    """A port whose named method raises once, then works."""

    def __init__(self, inner: Any, method: str) -> None:
        self._inner = inner
        self._method = method
        self.armed = True

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)
        if name != self._method or not self.armed:
            return target

        def fail(*args: Any, **kwargs: Any) -> Any:
            self.armed = False
            raise OSError("erase failed")

        return fail


class Full:
    def __init__(self, kind: str, tmp_path: Path) -> None:
        self.stack = doc_module._stack(kind, tmp_path)
        self.kind = kind
        self.db = getattr(self.stack.sources, "_db", None)
        if self.db is not None:
            self.stack.audit = SqliteAuditLog(self.db)
            self.stack.service = self.stack.build()
            self.answer_store: Any = SqliteAnswerStore(self.db)
        else:
            self.answer_store = MemoryAnswerStore()
        self.answers = AnswerCache(
            self.answer_store, lambda _q: (1.0, 0.0), version="test", min_similarity=0.9
        )
        self.ws = self.stack.ws
        self.wrap()

    def wrap(self, **ports: Any) -> None:
        s = self.stack
        self.deleter = SourceService(
            sources=s.sources,
            fragments=s.fragments,
            graph=ports.get("graph", s.graph),
            answers=self.answers,
            audit=s.audit,
            clock=lambda: DELETED_AT,
            fragment_index=ports.get("fragment_index", s.index),
            blobs=ports.get("blobs", s.blobs),
            imports=s.imports,
            jobs=s.queue,
        )

    def delete(self, source_id: str) -> None:
        self.deleter.delete(self.ws, doc_module.OWNER, source_id)

    def imported(self, parsed=SECRET_DOC):
        return self.stack.imported(TITLE, b"zebracrate bytes v1", parsed)

    def audits(self) -> list[tuple[str, dict[str, str]]]:
        if self.db is None:
            return [v for (w, _), v in self.stack.audit._events.items() if w == self.ws]
        with self.db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT event_type, metadata FROM audit_events WHERE workspace_id = ?", (self.ws,)
            ).fetchall()
        return [(r["event_type"], json.loads(r["metadata"])) for r in rows]

    def deleted_events(self) -> list[dict[str, str]]:
        return [meta for kind, meta in self.audits() if kind == "source_deleted"]

    def source_folder(self, source_id: str) -> Path:
        return self.stack.blobs.path_for(self.ws, source_id, 1).parent.parent

    def assert_gone(self, source_id: str) -> None:
        s = self.stack
        s.nothing_left(source_id)
        with pytest.raises(NotFoundError):
            s.imports.get_for_processing(self.ws, source_id)
        assert not self.source_folder(source_id).exists()
        dead = s.sources.get_owned(self.ws, doc_module.OWNER, source_id, include_tombstoned=True)
        assert dead.tombstoned_at is not None
        assert (dead.title, dead.origin, dead.content_hash) == (None, None, None)
        assert dead.state == "deleted"
        assert self.queue_keys(source_id) == []
        assert self.deleted_events() == [{"kind": "note"}]
        if self.db is not None:
            with self.db.transaction(write=False) as conn:
                for table in ("search_fragments", "fragments", "sections", "source_versions"):
                    left = conn.execute(
                        f"SELECT count(*) FROM {table} WHERE source_id = ?",
                        (source_id,),
                    ).fetchone()[0]
                    assert left == 0, table
                fts = conn.execute(
                    "SELECT count(*) FROM fragment_fts WHERE fragment_fts MATCH ?", (MARKER,)
                ).fetchone()[0]
                assert fts == 0

    def queue_keys(self, source_id: str) -> list[str]:
        """Queue keys of the source (they carry a prefix of the content hash)."""
        prefix = f"{source_id}:"
        if self.db is None:
            keys = [key for _, key, _ in self.stack.queue.job_keys()]
        else:
            with self.db.transaction(write=False) as conn:
                keys = [r["job_key"] for r in conn.execute("SELECT job_key FROM jobs")]
        return [key for key in keys if key.startswith(prefix)]

    def leftovers(self) -> list[str]:
        """Every place that still holds the marker, title or origin: raw database files (the
        WAL included, without a checkpoint of our own) and files under the blob root."""
        needles = [n.lower().encode() for n in (MARKER, TITLE, ORIGIN)]
        found: list[str] = []
        paths = [p for p in self.stack.blobs.root.rglob("*") if p.is_file()]
        if self.db is not None:
            folder = Path(self.db._path).parent
            paths += [p for p in folder.iterdir() if p.is_file() and p.name.startswith("nodo.db")]
        for path in paths:
            data = path.read_bytes().lower()
            found += [f"{path.name}:{n.decode()}" for n in needles if n in data]
        return found


@pytest.fixture(params=["memory", "sqlite"])
def full(request, tmp_path) -> Full:
    built = Full(request.param, tmp_path)
    yield built
    built.stack.blobs.close()


def test_delete_removes_search_rows_vectors_original_file_and_import_job(
    full: Full, tmp_path: Path
) -> None:
    source = full.imported()
    # a second file version, a stray partial upload and an open upload link
    new_file = tmp_path / "zebracrate-plan-v2.md"
    new_file.write_bytes(b"zebracrate bytes v2")
    full.stack.register(source.id, 2, SECRET_V2)
    full.stack.service.reimport(full.ws, doc_module.OWNER, source.id, new_file)
    full.stack.service.process(full.ws, source.id)
    assert full.stack.blobs.path_for(full.ws, source.id, 2).is_file()
    folder = full.stack.blobs.path_for(full.ws, source.id, 2).parent
    (folder / "original.0123456789abcdef.part").write_bytes(b"zebracrate partial")
    session = full.stack.blobs.create_upload_session(
        full.ws, doc_module.OWNER, source.id, TITLE, 10, version=3
    )
    assert full.stack.index.count_source(full.ws, source.id) > 0
    before = full.answer_store.epoch(full.ws, doc_module.OWNER)

    full.delete(source.id)

    full.assert_gone(source.id)
    with (
        pytest.raises(UploadRejected) as refused,
        full.stack.blobs.open_upload(session.token) as writer,
    ):
        writer.write(b"x" * 10)  # exactly the declared size: only a dead link refuses it
    assert refused.value.code in {"unknown_token", "cancelled"}
    assert not full.source_folder(source.id).exists()
    assert full.answer_store.epoch(full.ws, doc_module.OWNER) == before + 1
    assert full.leftovers() == []


@pytest.mark.parametrize("stage", ["queued", "parse", "index", "extract", "graph"])
def test_delete_of_a_running_import_makes_the_next_stage_stop(full: Full, stage: str) -> None:
    s = full.stack
    source = s.upload(TITLE, b"zebracrate bytes v1", SECRET_DOC)
    s.service.complete(full.ws, doc_module.OWNER, source.id)
    delete = lambda: full.delete(source.id)  # noqa: E731
    if stage == "queued":
        delete()
    elif stage == "parse":
        s.parser.hook = delete
    elif stage == "index":
        s.hooked_index.before_write = delete
    elif stage == "extract":
        s.inner.hook = delete
    else:
        s.hooked_graph.after_write = delete

    s.service.process(full.ws, source.id)

    full.assert_gone(source.id)
    if stage == "queued":
        assert s.parser.calls == []  # the queued job never ran
        # the queued message is gone (assert_gone), so no later delivery can start it
        digest = hashlib.sha256(b"zebracrate bytes v1").hexdigest()
        assert s.queue.publish_count(full.ws, job_key(source.id, digest, 0), IMPORT_SOURCE) == 0
    assert full.leftovers() == []


@pytest.mark.parametrize("port", ["fragment_index", "graph", "blobs"])
def test_delete_retry_after_a_failed_erase_completes_and_audits_once(full: Full, port: str) -> None:
    source = full.imported()
    real = {"fragment_index": full.stack.index, "graph": full.stack.graph}.get(port)
    method = "erase_source" if port == "fragment_index" else "erase"
    full.wrap(**{port: FailOnce(real or full.stack.blobs, method)})

    with pytest.raises(OSError):
        full.delete(source.id)
    with pytest.raises(NotFoundError):  # the tombstone came first: already invisible
        full.stack.sources.get_owned(full.ws, doc_module.OWNER, source.id)
    assert full.deleted_events() == [{"kind": "note"}]  # recorded before the first erase

    full.delete(source.id)
    full.delete(source.id)  # and once more: still one audit event

    full.assert_gone(source.id)
    assert full.leftovers() == []


def test_deleted_source_title_never_appears_in_audit(full: Full, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    s = full.stack
    source, _ = s.service.create(
        full.ws,
        doc_module.OWNER,
        filename=TITLE,
        size_bytes=19,
        kind=None,
        origin_note=ORIGIN,
        idempotency_key=None,
    )
    pending, _ = s.service.create(
        full.ws,
        doc_module.OWNER,
        filename="other.md",
        size_bytes=19,
        kind=None,
        origin_note=None,
        idempotency_key=None,
    )
    full.delete(source.id)  # never uploaded: only a title, an origin and a link
    imported = full.imported()
    full.delete(imported.id)

    for event_type, metadata in full.audits():
        assert set(metadata) <= {"kind"}, event_type
    text = repr(full.audits()) + caplog.text
    for needle in (MARKER, TITLE, ORIGIN, "zebracrate"):
        assert needle.lower() not in text.lower()
    assert full.leftovers() == []
    assert s.sources.get_owned(full.ws, doc_module.OWNER, pending.id).title == "other.md"


def test_a_deleted_source_can_not_be_retried_reimported_or_processed(
    full: Full, tmp_path: Path
) -> None:
    s = full.stack
    source = full.imported()
    full.delete(source.id)
    new_file = tmp_path / "zebracrate-plan-v2.md"
    new_file.write_bytes(b"zebracrate bytes v2")

    for attempt in (
        lambda: s.service.retry(full.ws, doc_module.OWNER, source.id),
        lambda: s.service.reimport(full.ws, doc_module.OWNER, source.id, new_file),
        lambda: s.service.cancel(full.ws, doc_module.OWNER, source.id),
        lambda: full.delete("unknown-source"),
    ):
        with pytest.raises(NotFoundError):
            attempt()
    s.service.process(full.ws, source.id)  # a late message is a no-op
    full.assert_gone(source.id)
    assert full.leftovers() == []
    keyed, _ = s.service.create(
        full.ws,
        doc_module.OWNER,
        filename="keyed.md",
        size_bytes=3,
        kind=None,
        origin_note=None,
        idempotency_key="key-1",
    )
    full.delete(keyed.id)
    with pytest.raises(ConflictError):  # the same key never brings the deleted source back
        s.service.create(
            full.ws,
            doc_module.OWNER,
            filename="keyed.md",
            size_bytes=3,
            kind=None,
            origin_note=None,
            idempotency_key="key-1",
        )
    with pytest.raises(NotFoundError):
        s.sources.get_owned(full.ws, doc_module.OWNER, keyed.id)


def test_the_container_wires_every_store_the_delete_erases(app) -> None:
    container = app.state.container
    service = container.source_service
    assert service._index is not None
    assert service._blobs is container.source_blobs
    assert service._imports is container.imports
    assert service._jobs is container.jobs


# ------------------------------------------------------------------ review fixes (Task 12)


def _restarted(full: Full) -> SourceService:
    """A new engine on the same files: fresh SQLite connections and a fresh file store, whose
    erased set is empty. The memory stores have no files to reopen, so they are reused."""
    s = full.stack
    blobs = LocalSourceBlobStore(s.blobs.root)
    if full.db is None:
        stores = (s.sources, s.fragments, s.graph, s.audit, s.index, s.imports, s.queue)
    else:
        db = LocalDatabase(full.db._path)
        stores = (
            SqliteSourceRepository(db),
            SqliteFragmentRepository(db),
            SqliteGraphStore(db),
            SqliteAuditLog(db),
            SqliteFragmentIndex(db, HashEmbedder()),
            SqliteImportJobRepository(db),
            SqliteJobQueue(db),
        )
    sources, fragments, graph, audit, index, imports, queue = stores
    return SourceService(
        sources,
        fragments,
        graph,
        full.answers,
        audit,
        lambda: DELETED_AT,
        fragment_index=index,
        blobs=blobs,
        imports=imports,
        jobs=queue,
    )


def test_a_delete_that_failed_after_the_tombstone_is_finished_at_engine_start(
    full: Full,
) -> None:
    source = full.imported()
    full.wrap(blobs=FailOnce(full.stack.blobs, "erase"))
    before = full.answer_store.epoch(full.ws, doc_module.OWNER)

    with pytest.raises(OSError):
        full.delete(source.id)

    # cached answers can quote the document: they are gone although the delete failed
    assert full.answer_store.epoch(full.ws, doc_module.OWNER) > before
    assert full.source_folder(source.id).exists()  # the failed step left the original

    assert _restarted(full).finish_deletes() == 1
    assert _restarted(full).finish_deletes() == 0  # finished deletes are not run again

    full.assert_gone(source.id)
    assert full.leftovers() == []


def test_app_start_finishes_unfinished_deletes(app) -> None:
    from fastapi.testclient import TestClient

    container = app.state.container
    value = replace(source("doc9", owner=OWNER, ws=WS), title="Private handbook")
    container.sources.add(WS, value)
    container.sources.save(WS, replace(value, tombstoned_at=DELETED_AT, state="deleting"))

    with TestClient(app):
        pass

    dead = container.sources.get_owned(WS, OWNER, "doc9", include_tombstoned=True)
    assert (dead.state, dead.title) == ("deleted", None)


def test_delete_forgets_the_content_hash_and_dedupe_stays_for_live_sources(full: Full) -> None:
    s = full.stack
    first = full.imported()
    full.delete(first.id)
    dead = s.sources.get_owned(full.ws, doc_module.OWNER, first.id, include_tombstoned=True)
    assert dead.content_hash is None
    assert full.queue_keys(first.id) == []

    again = full.imported()  # the same bytes again: a new source, not the deleted one
    assert again.id != first.id
    assert s.job(again.id).state == "ready"
    assert s.index.count_source(full.ws, again.id) > 0
    twin = s.upload("copy.md", b"zebracrate bytes v1", SECRET_DOC)
    job = s.service.complete(full.ws, doc_module.OWNER, twin.id)
    assert job.source_id == again.id  # dedupe still finds the live source


def test_delete_leaves_other_sources_and_workspaces_intact(full: Full) -> None:
    s = full.stack
    other_doc = doc_module.doc(("Notes", ["Sara will benchmark retrieval next week."]))
    victim = full.imported()
    sibling = s.imported("sibling.md", b"sibling bytes", other_doc)
    home = s.ws
    s.ws = f"{home}-other"
    stranger = s.imported("stranger.md", b"stranger bytes", other_doc)
    s.ws = home

    full.delete(victim.id)

    assert s.index.count_source(home, victim.id) == 0
    assert s.fragments.latest(home, victim.id) == ()
    assert s.evidence(victim.id) == frozenset()
    assert not full.source_folder(victim.id).exists()
    hits = s.index.search(home, doc_module.OWNER, "queue ingestion ledger", 10)
    assert victim.id not in {h.fragment.source_id for h in hits}
    for ws, kept in ((home, sibling), (f"{home}-other", stranger)):
        assert len(s.fragments.latest(ws, kept.id)) == 1
        assert s.index.count_source(ws, kept.id) == 1
        hits = s.index.search(ws, doc_module.OWNER, "benchmark retrieval", 5)
        assert [h.fragment.source_id for h in hits] == [kept.id]
        assert s.graph.evidence_segment_ids(ws, doc_module.OWNER, kept.id)
        assert s.blobs.path_for(ws, kept.id, 1).is_file()
        assert s.imports.get_for_processing(ws, kept.id).state == "ready"
        live = s.sources.get_owned(ws, doc_module.OWNER, kept.id)
        assert live.title is not None and live.content_hash is not None
        if full.db is not None:
            with full.db.transaction(write=False) as conn:
                vectors = conn.execute(
                    "SELECT count(*) FROM search_fragments WHERE workspace_id = ?"
                    " AND source_id = ? AND length(vector) > 0",
                    (ws, kept.id),
                ).fetchone()[0]
                assert vectors == 1
                jobs = conn.execute(
                    "SELECT count(*) FROM jobs WHERE workspace_id = ? AND job_key LIKE ?",
                    (ws, f"{kept.id}:%"),
                ).fetchone()[0]
                assert jobs == 1


def test_the_local_container_wires_every_store_the_delete_erases(tmp_path) -> None:
    from nodo_api.config import Settings
    from nodo_api.main import _container

    container = _container(
        Settings(
            environment="prod",
            backend="local",
            auth_mode="local",
            local_token="local-test-token-0123456789-0123456789",
            local_data_dir=tmp_path,
            attestation_hmac_key="local-test-attestation-key-0123456789",
        ),
        None,
    )
    try:
        service = container.source_service
        assert isinstance(service._index, SqliteFragmentIndex)
        assert service._blobs is container.source_blobs
        assert service._imports is container.imports
        assert service._jobs is container.jobs
    finally:
        container.runtime.db.close()
