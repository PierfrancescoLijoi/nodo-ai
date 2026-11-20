"""The source import service end to end on the memory and the SQLite stores (Task 11).

Every document here is invented test text; no real content, names or files."""

from __future__ import annotations

import logging
import os
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.fragment_index import SqliteFragmentIndex
from nodo_api.adapters.local.graph import SqliteGraphStore
from nodo_api.adapters.local.imports import SqliteImportJobRepository
from nodo_api.adapters.local.queue import SqliteJobQueue
from nodo_api.adapters.local.sources import SqliteFragmentRepository, SqliteSourceRepository
from nodo_api.adapters.memory import (
    MemoryAuditLog,
    MemoryFragmentIndex,
    MemoryFragmentRepository,
    MemoryGraphStore,
    MemoryImportJobRepository,
    MemoryJobQueue,
    MemoryMeetingRepository,
    MemorySourceBlobStore,
    MemorySourceRepository,
    MemoryTranscriptRepository,
)
from nodo_api.adapters.memory.documents import MemoryDocumentParser
from nodo_api.adapters.memory.fragment_extractor import KeywordFragmentExtractor
from nodo_api.domain.documents import (
    Block,
    ParsedDocument,
    ParseError,
    build_sections_and_fragments,
)
from nodo_api.domain.graph import ExtractionError, source_entity_id
from nodo_api.domain.models import Locale
from nodo_api.domain.source_files import UploadRejected
from nodo_api.domain.sources import MAX_EMBEDDING_TOKENS, SourceKind, estimate_tokens
from nodo_api.errors import ConflictError, ModelUnavailable, NotFoundError, ValidationFailed
from nodo_api.services.fragment_extraction import ChunkedFragmentExtractor
from nodo_api.services.source_import import IMPORT_SOURCE, SourceImportService, job_key
from nodo_api.services.sources import SourceService
from tests.test_local_adapters import HashEmbedder

OWNER = "u1"
VERSION_TAG = "doc-1|test"


def doc(*sections: tuple[str, list[str]]) -> ParsedDocument:
    blocks: list[Block] = []
    for title, paragraphs in sections:
        blocks.append(Block("heading", title, heading_level=1))
        blocks.extend(Block("paragraph", text) for text in paragraphs)
    return ParsedDocument("fake", tuple(blocks))


PLAN = ("Plan", ["Marco decided to use the queue for ingestion."])
NOTES = ("Notes", ["Sara will benchmark retrieval next week."])
NOTES_V2 = ("Notes", ["Giulia will prepare the retrieval benchmark."])
DOC_A = doc(PLAN, NOTES)
DOC_B = doc(PLAN, NOTES_V2)


class Recording:
    """The inner fragment extractor: keyword rules, a call log, a failure switch and a hook."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail: Exception | None = None
        self.hook: Any = None

    def extract(self, fragments, section_path, locale, known_decisions=()):
        self.calls.append(tuple(f.id for f in fragments))
        if self.hook is not None:
            self.hook()
        if self.fail is not None:
            raise self.fail
        return KeywordFragmentExtractor().extract(fragments, section_path, locale, known_decisions)


class CountingParser(MemoryDocumentParser):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str | None] = []
        self.hook: Any = None

    def parse(self, path, *, timeout_seconds=None, declared_name=None):
        self.calls.append(declared_name)
        if self.hook is not None:
            self.hook()
        return super().parse(path, declared_name=declared_name)


class Publishes:
    """The job queue as the service sees it, with every published key recorded."""

    def __init__(self, queue: Any) -> None:
        self.queue = queue
        self.keys: list[str] = []
        self.fail_next = False

    def publish_once(self, workspace_id, job_key, job_type, payload):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("queue down")
        assert job_type == IMPORT_SOURCE and payload == {"source_id": payload["source_id"]}
        self.keys.append(job_key)
        return self.queue.publish_once(workspace_id, job_key, job_type, payload)

    def publish_later(self, *args, **kwargs):  # pragma: no cover - imports never delay
        raise AssertionError("not used")


class GraphHook:
    """The graph store with a hook that runs right after a source write."""

    def __init__(self, graph: Any) -> None:
        self._graph = graph
        self.after_write: Any = None

    def replace_source_evidence(self, *args, **kwargs):
        self._graph.replace_source_evidence(*args, **kwargs)
        if self.after_write is not None:
            self.after_write()

    def __getattr__(self, name):
        return getattr(self._graph, name)


class IndexHook:
    """The fragment index with a hook that runs right before each index write."""

    def __init__(self, index: Any) -> None:
        self._index = index
        self.before_write: Any = None

    def index(self, *args, **kwargs):
        if self.before_write is not None:
            self.before_write()
        return self._index.index(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._index, name)


@dataclass
class Stack:
    name: str
    sources: Any
    fragments: Any
    imports: Any
    index: Any
    graph: Any
    queue: Any
    blobs: Any
    parser: CountingParser
    inner: Recording
    published: Publishes
    audit: MemoryAuditLog
    service: SourceImportService
    hooked_graph: GraphHook
    hooked_index: IndexHook
    screened: list[tuple[Path, str]] = field(default_factory=list)
    ws: str = field(default_factory=lambda: f"ws-{uuid.uuid4().hex[:8]}")

    def build(self, **overrides: Any) -> SourceImportService:
        options = {
            "extraction_version": VERSION_TAG,
            "control": self.queue,
            "screen": lambda path, name: self.screened.append((path, name)),
            **overrides,
        }
        return SourceImportService(
            self.sources,
            self.fragments,
            self.imports,
            self.blobs,
            self.parser,
            self.hooked_index,
            ChunkedFragmentExtractor(self.inner, 2000),
            self.hooked_graph,
            self.published,
            self.audit,
            **options,
        )

    def upload(self, name: str, data: bytes, parsed: ParsedDocument | ParseError, **kw: Any):
        source, session = self.service.create(
            self.ws,
            OWNER,
            filename=name,
            size_bytes=len(data),
            kind=kw.get("kind"),
            origin_note=kw.get("origin_note"),
            idempotency_key=kw.get("key"),
        )
        with self.blobs.open_upload(session.token) as writer:
            writer.write(data)
        self.register(source.id, 1, parsed)
        return source

    def register(self, source_id: str, version: int, parsed: ParsedDocument | ParseError) -> None:
        path = self.blobs.path_for(self.ws, source_id, version)
        if isinstance(parsed, ParseError):
            self.parser.register_error(path, parsed)
        else:
            self.parser.register(path, parsed)

    def imported(self, name: str, data: bytes, parsed=DOC_A):
        source = self.upload(name, data, parsed)
        self.service.complete(self.ws, OWNER, source.id)
        self.service.process(self.ws, source.id)
        return source

    def job(self, source_id: str):
        return self.imports.get_for_processing(self.ws, source_id)

    def evidence(self, source_id: str) -> frozenset[str]:
        return self.graph.evidence_segment_ids(self.ws, OWNER, source_id)

    def nothing_left(self, source_id: str) -> None:
        assert self.index.count_source(self.ws, source_id) == 0
        assert self.index.search(self.ws, OWNER, "queue ingestion", 10) == ()
        assert self.evidence(source_id) == frozenset()
        assert self.fragments.latest(self.ws, source_id) == ()
        node = source_entity_id(self.ws, OWNER, source_id)
        sub = self.graph.neighborhood(
            self.ws,
            OWNER,
            [node],
            depth=1,
            node_limit=50,
            fanout=50,
            include_proposed=True,
            as_of=None,
        )
        assert sub.nodes == ()


def _stack(kind: str, tmp_path: Path) -> Stack:
    if kind == "memory":
        meetings = MemoryMeetingRepository()
        sources = MemorySourceRepository(meetings)
        fragments = MemoryFragmentRepository(sources, MemoryTranscriptRepository())
        imports = MemoryImportJobRepository(sources)
        index: Any = MemoryFragmentIndex(sources)
        graph: Any = MemoryGraphStore()
        queue: Any = MemoryJobQueue()
    else:
        db = LocalDatabase(tmp_path / "nodo.db")
        sources = SqliteSourceRepository(db)
        fragments = SqliteFragmentRepository(db)
        imports = SqliteImportJobRepository(db)
        index = SqliteFragmentIndex(db, HashEmbedder())
        graph = SqliteGraphStore(db)
        queue = SqliteJobQueue(db)
    stack = Stack(
        kind,
        sources,
        fragments,
        imports,
        index,
        graph,
        queue,
        MemorySourceBlobStore(),
        CountingParser(),
        Recording(),
        Publishes(queue),
        MemoryAuditLog(),
        None,  # type: ignore[arg-type]
        GraphHook(graph),
        IndexHook(index),
    )
    stack.service = stack.build()
    return stack


@pytest.fixture(params=["memory", "sqlite"])
def stack(request, tmp_path) -> Stack:
    built = _stack(request.param, tmp_path)
    yield built
    built.blobs.close()


def deleter(stack: Stack, source_id: str):
    service = SourceService(
        stack.sources, stack.fragments, stack.graph, None, stack.audit, lambda: datetime.now(UTC)
    )
    return lambda: service.delete(stack.ws, OWNER, source_id)


# --------------------------------------------------------------------------- the happy path


def test_import_builds_sections_fragments_index_and_graph(stack: Stack) -> None:
    source = stack.imported("plan.md", b"plan v1")
    job = stack.job(source.id)
    assert (job.state, job.error_code) == ("ready", None)
    assert [s.title for s in stack.fragments.outline(stack.ws, source.id)] == ["Plan", "Notes"]
    latest = stack.fragments.latest(stack.ws, source.id)
    assert len(latest) == 2 and {f.version for f in latest} == {1}
    assert stack.index.count_source(stack.ws, source.id) == 2
    hits = stack.index.search(stack.ws, OWNER, "queue ingestion", 5)
    assert hits and hits[0].fragment.source_id == source.id
    assert stack.evidence(source.id) == {f.id for f in latest}
    (record,) = stack.fragments.versions(stack.ws, OWNER, source.id)
    assert (record.version, record.extraction_version, record.original_name) == (
        1,
        VERSION_TAG,
        "plan.md",
    )
    assert stack.sources.get_owned(stack.ws, OWNER, source.id).state == "ready"
    # sniff and limits ran before the parse, both with the declared name
    assert stack.screened == [(stack.blobs.path_for(stack.ws, source.id, 1), "plan.md")]
    assert stack.parser.calls == ["plan.md"]
    assert stack.published.keys == [job_key(source.id, source_hash(stack, source.id), 0)]


def source_hash(stack: Stack, source_id: str) -> str:
    hashed = stack.sources.get_owned(stack.ws, OWNER, source_id).content_hash
    assert hashed is not None
    return hashed


def test_job_key_is_the_source_and_hash_prefix_then_the_attempt() -> None:
    assert job_key("s1", "ab" * 32, 0) == f"s1:{'ab' * 8}"
    assert job_key("s1", "ab" * 32, 2) == f"s1:{'ab' * 8}:2"


def test_kind_is_inferred_from_the_extension(stack: Stack) -> None:
    kinds = {}
    for name in ("a.pdf", "b.pptx", "c.html", "d.md", "e.txt"):
        source, _ = stack.service.create(
            stack.ws, OWNER, filename=name, size_bytes=3, kind=None, origin_note=None,
            idempotency_key=None,
        )  # fmt: skip
        kinds[name] = source.kind
    assert kinds == {
        "a.pdf": SourceKind.DOCUMENT,
        "b.pptx": SourceKind.DECK,
        "c.html": SourceKind.WEB,
        "d.md": SourceKind.NOTE,
        "e.txt": SourceKind.DOCUMENT,
    }
    for bad in ("song.mp3", "x.exe", "../evil.md", "CON.md"):
        with pytest.raises(ValidationFailed):
            stack.service.create(
                stack.ws, OWNER, filename=bad, size_bytes=3, kind=None, origin_note=None,
                idempotency_key=None,
            )  # fmt: skip
    with pytest.raises(ValidationFailed):
        stack.service.create(
            stack.ws, OWNER, filename="a.md", size_bytes=3, kind=SourceKind.MEETING,
            origin_note=None, idempotency_key=None,
        )  # fmt: skip


# --------------------------------------------------------------------------- same bytes


def test_same_bytes_again_is_unchanged_with_no_new_version_or_job(stack: Stack) -> None:
    first = stack.imported("plan.md", b"same bytes")
    keys = list(stack.published.keys)
    second = stack.upload("copy.md", b"same bytes", DOC_A)
    job = stack.service.complete(stack.ws, OWNER, second.id)
    assert (job.source_id, job.state) == (first.id, "ready")  # unchanged: the live source
    assert stack.published.keys == keys
    with pytest.raises(NotFoundError):
        stack.sources.get_owned(stack.ws, OWNER, second.id)
    assert not stack.blobs.path_for(stack.ws, second.id, 1).exists()
    assert len(stack.fragments.versions(stack.ws, OWNER, first.id)) == 1
    # repeated complete of the first is idempotent too
    assert stack.service.complete(stack.ws, OWNER, first.id).state == "ready"
    assert stack.published.keys == keys


def test_same_bytes_imported_concurrently_gives_one_source(stack: Stack) -> None:
    for _ in range(5):
        data = uuid.uuid4().bytes
        a = stack.upload("a.md", data, DOC_A)
        b = stack.upload("b.md", data, DOC_A)
        barrier = threading.Barrier(2)
        results: dict[str, Any] = {}

        def run(source_id: str, barrier=barrier, results=results) -> None:
            barrier.wait()
            try:
                results[source_id] = stack.service.complete(stack.ws, OWNER, source_id).source_id
            except Exception as error:  # pragma: no cover - reported by the assert below
                results[source_id] = error

        threads = [threading.Thread(target=run, args=(s.id,)) for s in (a, b)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        winners = set(results.values())
        assert len(winners) == 1, results
        (winner,) = winners
        loser = b.id if winner == a.id else a.id
        with pytest.raises(NotFoundError):
            stack.sources.get_owned(stack.ws, OWNER, loser)
        live = stack.sources.find_by_hash(stack.ws, OWNER, source_hash(stack, winner))
        assert live is not None and live.id == winner


def test_upload_of_the_bytes_of_a_failed_source_retries_it(stack: Stack) -> None:
    first = stack.imported("broken.pdf", b"broken", ParseError("damaged"))
    assert stack.job(first.id).state == "failed"
    stack.register(first.id, 1, DOC_A)  # say the parser has been fixed meanwhile
    second = stack.upload("again.pdf", b"broken", DOC_A)
    job = stack.service.complete(stack.ws, OWNER, second.id)
    assert (job.source_id, job.state, job.attempt) == (first.id, "queued", 1)
    stack.service.process(stack.ws, first.id)
    assert stack.job(first.id).state == "ready"


# --------------------------------------------------------------------------- versions


def reimport(stack: Stack, source_id: str, tmp_path: Path, data: bytes, parsed, name="plan.md"):
    folder = tmp_path / uuid.uuid4().hex
    folder.mkdir()
    new = folder / name
    new.write_bytes(data)
    versions = sum(1 for v in range(1, 10) if stack.blobs.path_for(stack.ws, source_id, v).exists())
    stack.register(source_id, versions + 1, parsed)
    return stack.service.reimport(stack.ws, OWNER, source_id, new)


def test_changed_bytes_create_version_two_and_extract_only_changed_sections(
    stack: Stack, tmp_path: Path
) -> None:
    source = stack.imported("plan.md", b"v1")
    v1 = {f.text: f for f in stack.fragments.latest(stack.ws, source.id)}
    stack.inner.calls.clear()
    job = reimport(stack, source.id, tmp_path, b"v2", DOC_B)
    assert job.state == "queued"
    assert stack.published.keys[-1] == job_key(source.id, source_hash(stack, source.id), 1)
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"
    latest = stack.fragments.latest(stack.ws, source.id)
    assert {f.version for f in latest} == {2}
    changed = next(f for f in latest if f.text not in v1)
    assert {i for call in stack.inner.calls for i in call} == {changed.id}
    assert [r.version for r in stack.fragments.versions(stack.ws, OWNER, source.id)] == [2, 1]
    assert stack.index.count_source(stack.ws, source.id) == 2


def test_unchanged_fragments_keep_their_ids_across_versions(stack: Stack, tmp_path: Path) -> None:
    source = stack.imported("plan.md", b"v1")
    before = {f.text: f.id for f in stack.fragments.latest(stack.ws, source.id)}
    reimport(stack, source.id, tmp_path, b"v2", DOC_B)
    stack.service.process(stack.ws, source.id)
    after = {f.text: f.id for f in stack.fragments.latest(stack.ws, source.id)}
    kept = PLAN[1][0]
    assert after[kept] == before[kept]
    evidence = stack.evidence(source.id)
    assert before[kept] in evidence  # carried forward, not re-extracted
    assert before[NOTES[1][0]] not in evidence  # the dropped fragment lost its evidence
    assert after[NOTES_V2[1][0]] in evidence


def test_reimport_of_the_same_bytes_is_a_no_op(stack: Stack, tmp_path: Path) -> None:
    source = stack.imported("plan.md", b"v1")
    keys = list(stack.published.keys)
    job = reimport(stack, source.id, tmp_path, b"v1", DOC_A)
    assert job.state == "ready" and stack.published.keys == keys
    assert not stack.blobs.path_for(stack.ws, source.id, 2).exists()


def test_reimport_of_bytes_another_source_holds_points_at_it(stack: Stack, tmp_path) -> None:
    one = stack.imported("one.md", b"one")
    two = stack.imported("two.md", b"two")
    job = reimport(stack, two.id, tmp_path, b"one", DOC_A)
    assert job.source_id == one.id
    assert stack.job(two.id).state == "ready"
    assert source_hash(stack, two.id) != source_hash(stack, one.id)


def test_reimport_refuses_a_link_and_a_running_import(stack: Stack, tmp_path: Path) -> None:
    source = stack.imported("plan.md", b"v1")
    target = tmp_path / "real.md"
    target.write_bytes(b"real")
    link = tmp_path / "link.md"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("this account can not create symbolic links")
    with pytest.raises(ValidationFailed):
        stack.service.reimport(stack.ws, OWNER, source.id, link)
    pending = stack.upload("other.md", b"other", DOC_A)
    stack.service.complete(stack.ws, OWNER, pending.id)
    with pytest.raises(ConflictError):
        stack.service.reimport(stack.ws, OWNER, pending.id, target)


def test_title_change_on_reimport_relabels_the_graph_node(stack: Stack, tmp_path) -> None:
    """The source title follows the newest file name and the graph node label follows the title
    (Phase 3, Task 2; pinned by the source graph contract)."""
    source = stack.imported("plan.md", b"v1")
    reimport(stack, source.id, tmp_path, b"v2", DOC_B, name="roadmap.md")
    stack.service.process(stack.ws, source.id)
    assert stack.sources.get_owned(stack.ws, OWNER, source.id).title == "roadmap.md"
    node = source_entity_id(stack.ws, OWNER, source.id)
    sub = stack.graph.neighborhood(
        stack.ws, OWNER, [node], depth=1, node_limit=50, fanout=50, include_proposed=True,
        as_of=None,
    )  # fmt: skip
    assert {n.label for n in sub.nodes if n.id == node} == {"roadmap.md"}


# --------------------------------------------------------------------------- failures


def test_transient_extraction_error_is_retried_without_reparsing(stack: Stack) -> None:
    source = stack.upload("plan.md", b"v1", DOC_A)
    stack.service.complete(stack.ws, OWNER, source.id)
    stack.inner.fail = ExtractionError("model said nothing", "extraction_invalid")
    with pytest.raises(ExtractionError):
        stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "extracting"
    stack.inner.fail = ModelUnavailable()
    with pytest.raises(ModelUnavailable):
        stack.service.process(stack.ws, source.id)
    stack.inner.fail = None
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"
    assert len(stack.parser.calls) == 1
    assert stack.evidence(source.id)


@pytest.mark.parametrize("code", ["encrypted", "damaged", "too_large", "timeout"])
def test_permanent_parse_error_fails_at_once_with_the_stable_code(stack: Stack, code) -> None:
    source = stack.imported("plan.pdf", b"bytes", ParseError(code))
    job = stack.job(source.id)
    assert (job.state, job.error_code) == ("failed", code)
    assert stack.sources.get_owned(stack.ws, OWNER, source.id).state == "failed"
    assert stack.inner.calls == []
    assert stack.blobs.path_for(stack.ws, source.id, 1).exists()  # kept for a retry


def test_limits_refusal_fails_before_any_parse(stack: Stack) -> None:
    def refuse(path, name):
        raise ParseError("too_large")

    stack.service = stack.build(screen=refuse)
    source = stack.imported("big.docx", b"zip", DOC_A)
    assert (stack.job(source.id).state, stack.job(source.id).error_code) == ("failed", "too_large")
    assert stack.parser.calls == []


def test_failed_import_leaves_nothing_searchable_or_in_the_graph(stack, tmp_path) -> None:
    source = stack.imported("plan.md", b"v1")
    reimport(stack, source.id, tmp_path, b"v2", DOC_B)
    stack.inner.fail = ExtractionError("no", "extraction_failed")
    with pytest.raises(ExtractionError):
        stack.service.process(stack.ws, source.id)
    stack.service.mark_failed(stack.ws, source.id, "extraction_failed")  # the last delivery
    job = stack.job(source.id)
    assert (job.state, job.error_code) == ("failed", "extraction_failed")
    stack.nothing_left(source.id)
    assert stack.blobs.path_for(stack.ws, source.id, 2).exists()
    # retry after the failure rebuilds the newest version
    stack.inner.fail = None
    stack.service.retry(stack.ws, OWNER, source.id)
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"
    assert {f.text for f in stack.fragments.latest(stack.ws, source.id)} == {
        PLAN[1][0],
        NOTES_V2[1][0],
    }


def test_mark_failed_never_overwrites_a_finished_job(stack: Stack) -> None:
    source = stack.imported("plan.md", b"v1")
    stack.service.mark_failed(stack.ws, source.id, "engine_crashed")
    assert stack.job(source.id).state == "ready"
    assert stack.index.count_source(stack.ws, source.id) == 2
    stack.service.mark_failed(stack.ws, "unknown", "x")  # no error for an unknown source


def test_unstable_reason_becomes_a_generic_code(stack: Stack) -> None:
    source = stack.upload("plan.md", b"v1", DOC_A)
    stack.service.complete(stack.ws, OWNER, source.id)
    stack.service.mark_failed(stack.ws, source.id, "Secret text from the file")
    assert stack.job(source.id).error_code == "import_failed"


def test_no_text_layer_file_becomes_ready_with_no_fragments_and_a_warning(stack: Stack) -> None:
    scan = ParsedDocument("pdf", (), no_text_layer=True)
    source = stack.imported("scan.pdf", b"scan", scan)
    job = stack.job(source.id)
    assert (job.state, job.error_code) == ("ready", "no_text_layer")
    assert stack.fragments.latest(stack.ws, source.id) == ()
    assert stack.index.count_source(stack.ws, source.id) == 0
    other = stack.imported("scan2.pdf", b"scan2", ParseError("no_text_layer"))
    assert (stack.job(other.id).state, stack.job(other.id).error_code) == (
        "ready",
        "no_text_layer",
    )


def test_empty_file_fails_with_empty(stack: Stack) -> None:
    source = stack.imported("blank.txt", b" ", ParsedDocument("text", ()))
    assert (stack.job(source.id).state, stack.job(source.id).error_code) == ("failed", "empty")
    other = stack.imported("blank2.txt", b"  ", ParseError("empty"))
    assert (stack.job(other.id).state, stack.job(other.id).error_code) == ("failed", "empty")


def test_skipped_windows_leave_a_warning_and_a_retry_extracts_again(stack: Stack) -> None:
    source = stack.upload("plan.md", b"v1", DOC_A)
    stack.service.complete(stack.ws, OWNER, source.id)

    def fail_whole_window_then_first_half() -> None:
        # one window of two fragments: it fails, then its first half fails and is skipped
        if len(stack.inner.calls) in (1, 2):
            raise ExtractionError("no", "extraction_invalid")

    stack.inner.hook = fail_whole_window_then_first_half
    stack.service.process(stack.ws, source.id)
    job = stack.job(source.id)
    assert (job.state, job.error_code) == ("ready", "extraction_partial")
    (record,) = stack.fragments.versions(stack.ws, OWNER, source.id)
    assert record.extraction_version is None  # not current: the next run extracts again
    stack.inner.hook = None
    stack.service.retry(stack.ws, OWNER, source.id)
    stack.service.process(stack.ws, source.id)
    assert (stack.job(source.id).state, stack.job(source.id).error_code) == ("ready", None)
    (record,) = stack.fragments.versions(stack.ws, OWNER, source.id)
    assert record.extraction_version == VERSION_TAG
    assert len(stack.parser.calls) == 1


def test_without_a_model_the_source_is_searchable_with_a_warning(stack: Stack) -> None:
    stack.service = stack.build(extraction_version=None)
    source = stack.imported("plan.md", b"v1")
    job = stack.job(source.id)
    assert (job.state, job.error_code) == ("ready", "extraction_unavailable")
    assert stack.index.count_source(stack.ws, source.id) == 2
    assert stack.inner.calls == []


def test_a_stale_message_for_a_ready_source_is_ignored(stack: Stack) -> None:
    source = stack.imported("plan.md", b"v1")
    parses, extracts = len(stack.parser.calls), len(stack.inner.calls)
    stack.service.process(stack.ws, source.id)
    stack.service.process(stack.ws, "unknown")
    assert (len(stack.parser.calls), len(stack.inner.calls)) == (parses, extracts)
    assert stack.job(source.id).state == "ready"


# --------------------------------------------------------------------------- delete and cancel


@pytest.mark.parametrize("moment", ["parse", "index", "extract", "graph_write"])
def test_delete_during_import_leaves_no_index_graph_or_fragments(stack: Stack, moment) -> None:
    source = stack.upload("secret-name.md", b"v1", DOC_A)
    stack.service.complete(stack.ws, OWNER, source.id)
    delete = deleter(stack, source.id)
    if moment == "parse":
        stack.parser.hook = delete
    elif moment == "index":
        stack.hooked_index.before_write = delete
    elif moment == "extract":
        stack.inner.hook = delete
    else:
        stack.hooked_graph.after_write = delete
    stack.service.process(stack.ws, source.id)
    stack.nothing_left(source.id)
    assert not stack.blobs.path_for(stack.ws, source.id, 1).exists()
    gone = stack.sources.get_owned(stack.ws, OWNER, source.id, include_tombstoned=True)
    assert gone.tombstoned_at is not None and gone.title is None
    with pytest.raises(NotFoundError):
        stack.imports.get_for_processing(stack.ws, source.id)


def test_delete_before_complete_erases_the_upload(stack: Stack) -> None:
    source = stack.upload("plan.md", b"v1", DOC_A)
    deleter(stack, source.id)()
    with pytest.raises(NotFoundError):
        stack.service.complete(stack.ws, OWNER, source.id)
    assert not stack.blobs.path_for(stack.ws, source.id, 1).exists()


def test_cancel_during_extraction_compensates_and_ends_cancelled(stack: Stack) -> None:
    source = stack.upload("plan.md", b"v1", DOC_A)
    stack.service.complete(stack.ws, OWNER, source.id)
    assert stack.queue.claim_next() is not None  # the worker holds it: cancel is cooperative
    stack.inner.hook = lambda: stack.service.cancel(stack.ws, OWNER, source.id)
    stack.service.process(stack.ws, source.id)
    job = stack.job(source.id)
    assert (job.state, job.cancel_requested) == ("cancelled", True)
    stack.nothing_left(source.id)
    assert stack.blobs.path_for(stack.ws, source.id, 1).exists()
    with pytest.raises(ConflictError):
        stack.service.cancel(stack.ws, OWNER, source.id)
    stack.inner.hook = None
    job = stack.service.retry(stack.ws, OWNER, source.id)
    assert (job.state, job.cancel_requested) == ("queued", False)
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"


def test_cancel_of_a_queued_import_ends_it_at_once(stack: Stack) -> None:
    source = stack.upload("plan.md", b"v1", DOC_A)
    stack.service.complete(stack.ws, OWNER, source.id)
    assert stack.service.cancel(stack.ws, OWNER, source.id).state == "cancelled"
    stack.service.process(stack.ws, source.id)  # a late message changes nothing
    assert stack.parser.calls == []
    with pytest.raises(NotFoundError):
        stack.service.cancel(stack.ws, "someone-else", source.id)


def test_retry_only_from_failed_or_cancelled(stack: Stack) -> None:
    source = stack.imported("plan.md", b"v1")
    with pytest.raises(ConflictError):
        stack.service.retry(stack.ws, OWNER, source.id)
    with pytest.raises(NotFoundError):
        stack.service.retry(stack.ws, "someone-else", source.id)


def test_resume_republishes_imports_left_running_by_a_crash(stack: Stack) -> None:
    source = stack.upload("plan.md", b"v1", DOC_A)
    stack.service.complete(stack.ws, OWNER, source.id)
    stack.inner.fail = ModelUnavailable()
    with pytest.raises(ModelUnavailable):
        stack.service.process(stack.ws, source.id)
    stack.inner.fail = None
    assert stack.service.resume_interrupted() == 1
    assert stack.published.keys[-1] == job_key(source.id, source_hash(stack, source.id), 1)
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"
    assert stack.service.resume_interrupted() == 0


# --------------------------------------------------------------------------- privacy and keys


def test_audit_records_kind_only(stack: Stack, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    source = stack.imported("secret-name.md", b"v1")
    stack.service.mark_failed(stack.ws, source.id, "x")  # a no-op on a ready job, logs nothing
    events = list(stack.audit._events.values())
    assert events and all(meta == {"kind": "note"} for _, meta in events)
    text = caplog.text + repr(events)
    for secret in ("secret-name", "Marco", "queue", source.title or "x"):
        assert secret not in text


def test_conflicting_idempotency_key_raises_conflict(stack: Stack) -> None:
    def create(name: str, size: int = 4):
        return stack.service.create(
            stack.ws, OWNER, filename=name, size_bytes=size, kind=None, origin_note=None,
            idempotency_key="key-1",
        )  # fmt: skip

    first, session = create("plan.md")
    again, fresh = create("plan.md")
    assert again.id == first.id and fresh.token != session.token  # a fresh link each time
    with pytest.raises(ConflictError):
        create("other.md")
    with stack.blobs.open_upload(fresh.token) as writer:
        writer.write(b"abcd")
    stack.register(first.id, 1, DOC_A)
    stack.service.complete(stack.ws, OWNER, first.id)
    with pytest.raises(ConflictError):  # uploaded already: no new link for version 1
        create("plan.md")


def test_complete_before_the_upload_is_a_conflict(stack: Stack) -> None:
    source, _ = stack.service.create(
        stack.ws, OWNER, filename="plan.md", size_bytes=4, kind=None, origin_note=None,
        idempotency_key=None,
    )  # fmt: skip
    with pytest.raises(ConflictError):
        stack.service.complete(stack.ws, OWNER, source.id)
    with pytest.raises(NotFoundError):
        stack.service.complete(stack.ws, "someone-else", source.id)


# --------------------------------------------------------------------------- builder


def test_builder_splits_fragments_over_the_token_ceiling() -> None:
    long = "-".join("x" * 1500)  # one token per character: 1100 characters hold 1100 tokens
    parsed = doc(("Big", [long]), ("Small", ["short text"]))
    _, plain = build_sections_and_fragments(parsed, "s1", 1)
    fits = lambda text: estimate_tokens(text) <= MAX_EMBEDDING_TOKENS  # noqa: E731
    _, split = build_sections_and_fragments(parsed, "s1", 1, fits=fits)
    assert all(fits(f.text) for f in split)
    assert len(split) > len(plain)
    stable = "\n\n".join(b.text for b in parsed.blocks)
    assert all(stable[f.locator.chars[0] : f.locator.chars[1]] == f.text for f in split)
    assert [f.ordinal for f in split] == list(range(len(split)))
    assert {f.id for f in plain if f.text == "short text"} <= {f.id for f in split}


# --------------------------------------------------------------------------- wiring


def test_engines_carry_the_import_service_and_the_worker_routes_to_it(tmp_path) -> None:
    from nodo_api import worker
    from nodo_api.adapters.local.bootstrap import local_adapters
    from nodo_api.config import Settings
    from nodo_api.main import create_app

    assert worker.IMPORT_SOURCE == IMPORT_SOURCE
    memory = create_app(
        Settings(
            environment="test",
            auth_mode="dev",
            dev_token="test-dev-token-0123456789",
            attestation_hmac_key="test-only-attestation-key-0123456789",
        )
    ).state.container
    assert isinstance(memory.source_import, SourceImportService)
    assert memory.imports is not None and memory.source_blobs is not None
    local = local_adapters(
        Settings(
            environment="prod",
            backend="local",
            auth_mode="local",
            local_token="local-test-token-0123456789-0123456789",
            local_data_dir=tmp_path,
            attestation_hmac_key="local-test-attestation-key-0123456789",
        )
    )
    try:
        assert isinstance(local.source_import, SourceImportService)
        assert local.runtime.imports is local.source_import
        assert local.runtime.loop._worker._imports is local.source_import
    finally:
        local.runtime.db.close()


# --------------------------------------------------------------------------- review fixes


def _hits(stack: Stack, query: str) -> set[str]:
    return {h.fragment.id for h in stack.index.search(stack.ws, OWNER, query, 20)}


def test_failed_index_on_reimport_never_leaves_old_text_searchable(stack, tmp_path) -> None:
    source = stack.imported("plan.md", b"v1")
    old_ids = {f.id for f in stack.fragments.latest(stack.ws, source.id)}
    reimport(stack, source.id, tmp_path, b"v2", DOC_B)  # same fragment count as v1

    def fail_once() -> None:
        stack.hooked_index.before_write = None
        raise ModelUnavailable

    stack.hooked_index.before_write = fail_once
    with pytest.raises(ModelUnavailable):
        stack.service.process(stack.ws, source.id)
    stack.service.process(stack.ws, source.id)  # the queue retry
    assert stack.job(source.id).state == "ready"
    new_ids = {f.id for f in stack.fragments.latest(stack.ws, source.id)}
    indexed = _hits(stack, "Sara Giulia benchmark retrieval prepare queue ingestion Marco")
    assert indexed and indexed <= new_ids
    assert not (old_ids - new_ids) & indexed


# No headings (one section) and long enough for several model windows (about 6000 characters).
HEADINGLESS = ParsedDocument(
    "fake",
    tuple(Block("paragraph", f"Paragraph {i} says Marco decided. " * 28) for i in range(40)),
)


@pytest.mark.parametrize("stop", ["cancel", "delete"])
def test_stop_is_honoured_within_a_document_without_headings(stack: Stack, stop) -> None:
    source = stack.upload("notes.txt", b"plain", HEADINGLESS)
    stack.service.complete(stack.ws, OWNER, source.id)
    assert stack.queue.claim_next() is not None
    if stop == "cancel":
        action = lambda: stack.service.cancel(stack.ws, OWNER, source.id)  # noqa: E731
    else:
        action = deleter(stack, source.id)

    def first_call_only() -> None:
        if len(stack.inner.calls) == 1:
            action()

    stack.inner.hook = first_call_only
    stack.service.process(stack.ws, source.id)
    assert len(stack.inner.calls) <= 2  # the call that saw the stop and at most one more
    stack.nothing_left(source.id)


@pytest.mark.parametrize("refusal", ["too_large", "low_disk"])
def test_a_refused_upload_link_leaves_no_source_or_job(stack: Stack, refusal) -> None:
    from nodo_api.domain.source_files import UploadRejected

    size = 10
    if refusal == "too_large":
        size = 10**13
    else:
        stack.blobs.close()
        stack.blobs = MemorySourceBlobStore(free_space=lambda _: 0)
        stack.service = stack.build()
    with pytest.raises(UploadRejected):
        stack.service.create(
            stack.ws, OWNER, filename="huge-name.pdf", size_bytes=size, kind=None,
            origin_note=None, idempotency_key=None,
        )  # fmt: skip
    assert stack.sources.list_owned(stack.ws, OWNER, 10, None) == ()
    assert stack.imports.in_progress(100) == () or all(
        j.workspace_id != stack.ws for j in stack.imports.in_progress(100)
    )


def test_save_live_only_never_brings_a_deleted_source_back(stack: Stack) -> None:
    source = stack.upload("plan.md", b"v1", DOC_A)
    stored = stack.sources.get_owned(stack.ws, OWNER, source.id)
    stack.sources.save(stack.ws, replace(stored, tombstoned_at=datetime.now(UTC)))
    with pytest.raises(NotFoundError):
        stack.sources.save(stack.ws, replace(stored, state="ready"), live_only=True)
    kept = stack.sources.get_owned(stack.ws, OWNER, source.id, include_tombstoned=True)
    assert kept.tombstoned_at is not None and kept.state == "queued"
    live = stack.upload("other.md", b"v2", DOC_A)
    saved = stack.sources.save(
        stack.ws, replace(stack.sources.get_owned(stack.ws, OWNER, live.id), state="ready"),
        live_only=True,
    )  # fmt: skip
    assert saved.state == "ready"


def test_in_progress_lists_every_unfinished_job(stack: Stack) -> None:
    queued = stack.upload("a.md", b"a", DOC_A)
    running = stack.upload("b.md", b"b", DOC_A)
    ended = stack.upload("c.md", b"c", DOC_A)
    stack.imports.update(stack.ws, running.id, state="extracting")
    stack.imports.update(stack.ws, ended.id, state="failed")
    mine = {j.source_id for j in stack.imports.in_progress(1000) if j.workspace_id == stack.ws}
    assert mine == {queued.id, running.id}
    assert len(stack.imports.in_progress(1)) == 1


def test_delete_during_complete_is_not_undone(stack: Stack, monkeypatch) -> None:
    source = stack.upload("plan.md", b"v1", DOC_A)
    original = stack.blobs.size_and_sha256

    def delete_then_hash(path):
        deleter(stack, source.id)()
        return original(path)

    monkeypatch.setattr(stack.blobs, "size_and_sha256", delete_then_hash)
    with pytest.raises(NotFoundError):
        stack.service.complete(stack.ws, OWNER, source.id)
    with pytest.raises(NotFoundError):
        stack.sources.get_owned(stack.ws, OWNER, source.id)
    assert not stack.blobs.path_for(stack.ws, source.id, 1).exists()


def test_delete_after_the_reimport_copy_is_read_is_not_undone(stack, tmp_path, monkeypatch):
    source = stack.imported("plan.md", b"v1")
    adopt, write = stack.blobs.adopt, stack.sources.update
    copied = []

    def remember_copy(*args):
        copied.append(1)
        return adopt(*args)

    def delete_then_write(workspace_id, source_id, **changes):
        if copied and len(copied) == 1:
            copied.append(2)
            deleter(stack, source.id)()  # between the copy and the write
        return write(workspace_id, source_id, **changes)

    monkeypatch.setattr(stack.blobs, "adopt", remember_copy)
    monkeypatch.setattr(stack.sources, "update", delete_then_write)
    with pytest.raises(NotFoundError):
        reimport(stack, source.id, tmp_path, b"v2", DOC_B)
    with pytest.raises(NotFoundError):
        stack.sources.get_owned(stack.ws, OWNER, source.id)
    assert not any(stack.blobs.path_for(stack.ws, source.id, v).exists() for v in (1, 2))


def test_delete_during_the_reimport_copy_is_not_undone(stack, tmp_path, monkeypatch) -> None:
    source = stack.imported("plan.md", b"v1")
    original = stack.blobs.adopt

    def delete_then_copy(*args):
        deleter(stack, source.id)()
        return original(*args)

    monkeypatch.setattr(stack.blobs, "adopt", delete_then_copy)
    with pytest.raises(NotFoundError):
        reimport(stack, source.id, tmp_path, b"v2", DOC_B)
    with pytest.raises(NotFoundError):
        stack.sources.get_owned(stack.ws, OWNER, source.id)
    assert not any(stack.blobs.path_for(stack.ws, source.id, v).exists() for v in (1, 2))


def test_delete_while_the_state_is_written_is_not_undone(stack: Stack, monkeypatch) -> None:
    source = stack.upload("plan.md", b"v1", DOC_A)
    stack.service.complete(stack.ws, OWNER, source.id)
    original = stack.sources.update
    fired = []

    def delete_first(workspace_id, source_id, **changes):
        if changes.get("state") == "ready" and not fired:
            fired.append(1)
            deleter(stack, source.id)()
        return original(workspace_id, source_id, **changes)

    monkeypatch.setattr(stack.sources, "update", delete_first)
    stack.service.process(stack.ws, source.id)
    assert fired
    with pytest.raises(NotFoundError):
        stack.sources.get_owned(stack.ws, OWNER, source.id)
    stack.nothing_left(source.id)


def test_delete_between_adopt_and_job_end_leaves_nothing(stack, tmp_path) -> None:
    source = stack.imported("plan.md", b"v1")
    reimport(stack, source.id, tmp_path, b"v2", DOC_B)
    deleter(stack, source.id)()
    stack.service.process(stack.ws, source.id)
    stack.nothing_left(source.id)
    assert not any(stack.blobs.path_for(stack.ws, source.id, v).exists() for v in (1, 2))
    with pytest.raises(NotFoundError):
        stack.imports.get_for_processing(stack.ws, source.id)


def test_cancel_during_indexing_is_seen_without_an_extraction_stage(stack: Stack) -> None:
    stack.service = stack.build(extraction_version=None)
    source = stack.upload("plan.md", b"v1", DOC_A)
    stack.service.complete(stack.ws, OWNER, source.id)
    assert stack.queue.claim_next() is not None
    stack.hooked_index.before_write = lambda: stack.service.cancel(stack.ws, OWNER, source.id)
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "cancelled"
    stack.nothing_left(source.id)


class Answers:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def invalidate(self, workspace_id: str, owner_id: str) -> None:
        self.calls.append((workspace_id, owner_id))


def test_answers_are_invalidated_on_ready_failed_cancelled_and_deleted(stack: Stack) -> None:
    answers = Answers()
    stack.service = stack.build(answers=answers)
    stack.imported("ready.md", b"r")
    after_ready = len(answers.calls)
    assert after_ready >= 1
    stack.imported("failed.pdf", b"f", ParseError("damaged"))
    after_failed = len(answers.calls)
    assert after_failed > after_ready
    queued = stack.upload("cancel.md", b"c", DOC_A)
    stack.service.complete(stack.ws, OWNER, queued.id)
    stack.service.cancel(stack.ws, OWNER, queued.id)
    after_cancel = len(answers.calls)
    assert after_cancel > after_failed
    gone = stack.upload("gone.md", b"g", DOC_A)
    stack.service.complete(stack.ws, OWNER, gone.id)
    stack.parser.hook = deleter(stack, gone.id)
    stack.service.process(stack.ws, gone.id)
    assert len(answers.calls) > after_cancel
    assert set(answers.calls) == {(stack.ws, OWNER)}


def test_a_failed_publish_on_reimport_leaves_a_retryable_job(stack, tmp_path) -> None:
    source = stack.imported("plan.md", b"v1")
    stack.published.fail_next = True
    with pytest.raises(RuntimeError):
        reimport(stack, source.id, tmp_path, b"v2", DOC_B)
    job = stack.job(source.id)
    assert (job.state, job.error_code) == ("failed", "queue_unavailable")
    assert stack.sources.get_owned(stack.ws, OWNER, source.id).state == "ready"
    assert stack.index.count_source(stack.ws, source.id) == 2
    stack.service.retry(stack.ws, OWNER, source.id)
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"
    assert NOTES_V2[1][0] in {f.text for f in stack.fragments.latest(stack.ws, source.id)}


def test_the_start_sweep_publishes_a_queued_job_whose_message_was_lost(stack: Stack) -> None:
    source = stack.upload("plan.md", b"v1", DOC_A)
    stack.published.fail_next = True
    with pytest.raises(RuntimeError):
        stack.service.complete(stack.ws, OWNER, source.id)
    assert stack.job(source.id).state == "queued"
    assert stack.service.resume_interrupted() >= 1
    assert stack.published.keys[-1] == job_key(source.id, source_hash(stack, source.id), 0)
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"


def test_a_damaged_new_version_keeps_the_earlier_one_searchable(stack, tmp_path) -> None:
    source = stack.imported("plan.md", b"v1")
    evidence = stack.evidence(source.id)
    reimport(stack, source.id, tmp_path, b"v2", ParseError("damaged"))
    stack.service.process(stack.ws, source.id)
    job = stack.job(source.id)
    assert (job.state, job.error_code) == ("failed", "damaged")
    assert stack.sources.get_owned(stack.ws, OWNER, source.id).state == "ready"
    assert stack.index.count_source(stack.ws, source.id) == 2
    assert stack.evidence(source.id) == evidence
    assert [v.version for v in stack.fragments.versions(stack.ws, OWNER, source.id)] == [1]


def test_cancel_of_a_reimport_before_its_index_keeps_the_earlier_version(stack, tmp_path) -> None:
    source = stack.imported("plan.md", b"v1")
    reimport(stack, source.id, tmp_path, b"v2", DOC_B)
    while stack.queue.claim_next() is not None:
        pass
    stack.parser.hook = lambda: stack.service.cancel(stack.ws, OWNER, source.id)
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "cancelled"
    assert stack.sources.get_owned(stack.ws, OWNER, source.id).state == "ready"
    assert stack.index.count_source(stack.ws, source.id) == 2
    assert PLAN[1][0] in {f.text for f in stack.fragments.latest(stack.ws, source.id)}


def test_a_second_reimport_during_the_first_copy_is_refused(stack, tmp_path, monkeypatch) -> None:
    source = stack.imported("plan.md", b"v1")
    original = stack.blobs.adopt
    refused = []

    def second_reimport_meanwhile(*args):
        if not refused:
            other = tmp_path / "other.md"
            other.write_bytes(b"v3")
            with pytest.raises(ConflictError):
                stack.service.reimport(stack.ws, OWNER, source.id, other)
            refused.append(1)
        return original(*args)

    monkeypatch.setattr(stack.blobs, "adopt", second_reimport_meanwhile)
    reimport(stack, source.id, tmp_path, b"v2", DOC_B)
    assert refused
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"
    assert NOTES_V2[1][0] in {f.text for f in stack.fragments.latest(stack.ws, source.id)}


def test_complete_after_the_twin_vanished_imports_the_bytes_as_new(stack, monkeypatch) -> None:
    twin = stack.imported("one.md", b"same")
    second = stack.upload("two.md", b"same", DOC_A)
    original = stack.sources.find_by_hash
    fired = []

    def vanish_after_lookup(*args):
        found = original(*args)
        if found is not None and not fired:
            fired.append(1)
            deleter(stack, twin.id)()
        return found

    monkeypatch.setattr(stack.sources, "find_by_hash", vanish_after_lookup)
    job = stack.service.complete(stack.ws, OWNER, second.id)
    assert fired and (job.source_id, job.state) == (second.id, "queued")
    stack.service.process(stack.ws, second.id)
    assert stack.job(second.id).state == "ready"


def test_an_unrecorded_version_is_not_taken_as_extracted(stack, tmp_path, monkeypatch) -> None:
    source = stack.imported("plan.md", b"v1")
    reimport(stack, source.id, tmp_path, b"v2", DOC_B)
    original = stack.fragments.record_version
    failed = []

    def fail_first(*args, **kwargs):
        if not failed:
            failed.append(1)
            raise RuntimeError("disk full")
        return original(*args, **kwargs)

    monkeypatch.setattr(stack.fragments, "record_version", fail_first)
    with pytest.raises(RuntimeError):
        stack.service.process(stack.ws, source.id)
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"
    latest = {f.text: f.id for f in stack.fragments.latest(stack.ws, source.id)}
    assert latest[NOTES_V2[1][0]] in stack.evidence(source.id)


def test_local_engine_imports_through_the_real_store_parser_and_limits(tmp_path) -> None:
    from nodo_api.adapters.local.bootstrap import document_screen
    from nodo_api.adapters.local.documents.parser import SubprocessDocumentParser
    from nodo_api.adapters.local.source_blobs import LocalSourceBlobStore
    from nodo_api.config import Settings
    from nodo_api.domain.documents import PARSE_ERROR_CODES

    db = LocalDatabase(tmp_path / "nodo.db")
    blobs = LocalSourceBlobStore(tmp_path / "sources")
    queue = SqliteJobQueue(db)
    sources = SqliteSourceRepository(db)
    index = SqliteFragmentIndex(db, HashEmbedder())
    service = SourceImportService(
        sources,
        SqliteFragmentRepository(db),
        SqliteImportJobRepository(db),
        blobs,
        SubprocessDocumentParser(120, 1024),
        index,
        ChunkedFragmentExtractor(Recording(), 2000),
        SqliteGraphStore(db),
        queue,
        MemoryAuditLog(),
        extraction_version=VERSION_TAG,
        control=queue,
        screen=document_screen(Settings(environment="test", auth_mode="dev",
                                        dev_token="test-dev-token-0123456789",
                                        attestation_hmac_key="test-only-attestation-key-0123456789")),
    )  # fmt: skip
    ws = "ws-local"

    def run(name: str, data: bytes):
        source, session = service.create(
            ws, OWNER, filename=name, size_bytes=len(data), kind=None, origin_note=None,
            idempotency_key=None,
        )  # fmt: skip
        with blobs.open_upload(session.token) as writer:
            writer.write(data)
        service.complete(ws, OWNER, source.id)
        service.process(ws, source.id)
        return SqliteImportJobRepository(db).get_for_processing(ws, source.id)

    try:
        text = b"# Plan\n\nMarco decided to use the queue.\n\n# Notes\n\nSara will benchmark.\n"
        job = run("plan.md", text)
        assert (job.state, job.error_code) == ("ready", None)
        titles = [s.title for s in SqliteFragmentRepository(db).outline(ws, job.source_id)]
        assert titles == ["Plan", "Notes"]
        assert index.search(ws, OWNER, "queue", 5)
        plain = run("notes.txt", b"Giulia will prepare the retrieval benchmark.\n")
        assert plain.state == "ready"
        broken = run("broken.pdf", b"%PDF-1.4\nnot really a pdf\n")
        assert broken.state == "failed" and broken.error_code in PARSE_ERROR_CODES
    finally:
        db.close()


# --------------------------------------------------------------------------- races (Task 13 review)


def _during_hash(stack: Stack, action) -> None:
    """Run `action` once while complete() hashes the upload: the widest stale-read window."""
    original = stack.blobs.size_and_sha256

    def hashing(path):
        result = original(path)
        stack.blobs.size_and_sha256 = original
        action()
        return result

    stack.blobs.size_and_sha256 = hashing


def test_cancel_while_complete_hashes_leaves_job_and_source_agreeing(stack: Stack) -> None:
    source = stack.upload("plan.md", b"v1", DOC_A)
    _during_hash(stack, lambda: stack.service.cancel(stack.ws, OWNER, source.id))
    stack.service.complete(stack.ws, OWNER, source.id)
    stack.service.process(stack.ws, source.id)  # a late message changes nothing
    row = stack.sources.get_owned(stack.ws, OWNER, source.id)
    assert stack.job(source.id).state == row.state == "cancelled"
    assert row.content_hash is not None  # the bytes are known, so retry can resume
    stack.service.retry(stack.ws, OWNER, source.id)
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"
    assert stack.sources.get_owned(stack.ws, OWNER, source.id).state == "ready"


def test_title_and_language_edits_during_an_import_survive(stack: Stack) -> None:
    source = stack.upload("plan.md", b"v1", DOC_A)

    def edit(title: str, language: Locale) -> None:
        live = stack.sources.get_owned(stack.ws, OWNER, source.id)
        stack.sources.save(stack.ws, replace(live, title=title, language=language))

    _during_hash(stack, lambda: edit("Renamed", Locale.PL))
    stack.service.complete(stack.ws, OWNER, source.id)
    row = stack.sources.get_owned(stack.ws, OWNER, source.id)
    assert (row.title, row.language) == ("Renamed", Locale.PL)
    stack.inner.hook = lambda: edit("Renamed again", Locale.DA)  # during extraction
    stack.service.process(stack.ws, source.id)
    row = stack.sources.get_owned(stack.ws, OWNER, source.id)
    assert (row.state, row.title, row.language) == ("ready", "Renamed again", Locale.DA)


def test_cancel_revokes_the_upload_link_and_leaves_no_file(stack: Stack) -> None:
    source, session = stack.service.create(
        stack.ws, OWNER, filename="plan.md", size_bytes=2, kind=None, origin_note=None,
        idempotency_key="cancel-before-upload",
    )  # fmt: skip
    stack.service.cancel(stack.ws, OWNER, source.id)
    with pytest.raises(UploadRejected), stack.blobs.open_upload(session.token) as writer:
        writer.write(b"v1")
    assert not stack.blobs.path_for(stack.ws, source.id, 1).exists()
    with pytest.raises(ConflictError):  # a replay of the create gives no new link either
        stack.service.create(
            stack.ws, OWNER, filename="plan.md", size_bytes=2, kind=None, origin_note=None,
            idempotency_key="cancel-before-upload",
        )  # fmt: skip


def test_a_replayed_link_can_not_overwrite_the_original(stack: Stack) -> None:
    def create():
        return stack.service.create(
            stack.ws, OWNER, filename="plan.md", size_bytes=2, kind=None, origin_note=None,
            idempotency_key="replayed-link",
        )  # fmt: skip

    source, first = create()
    _, second = create()  # a replay before the upload: a second live link
    with stack.blobs.open_upload(first.token) as writer:
        writer.write(b"v1")
    stack.register(source.id, 1, DOC_A)
    with pytest.raises(ConflictError):  # once the bytes are stored a replay gives no link
        create()
    stack.service.complete(stack.ws, OWNER, source.id)
    # after complete the hashed original can not be replaced through the earlier link
    with pytest.raises(UploadRejected), stack.blobs.open_upload(second.token) as writer:
        writer.write(b"xx")
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"
    assert stack.blobs.path_for(stack.ws, source.id, 1).read_bytes() == b"v1"
