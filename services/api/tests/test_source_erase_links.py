"""A deleted source leaves no watch link, no discarded upload and no queue key behind. Memory and
SQLite stores; every file is invented test text under tmp_path."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nodo_api.adapters.local.watch_folders import SqliteWatchFolderRepository
from nodo_api.adapters.memory import MemoryWatchFolderRepository
from nodo_api.errors import NotFoundError
from nodo_api.services.folder_watch import FolderWatcher
from nodo_api.services.source_import import IMPORT_SOURCE
from nodo_api.services.sources import SourceService
from tests.test_source_import import DOC_A, OWNER, Stack, _stack, deleter

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


@pytest.fixture(params=["memory", "sqlite"])
def stack(request, tmp_path):
    built = _stack(request.param, tmp_path)
    yield built
    built.blobs.close()


def _sources(stack: Stack, **ports) -> SourceService:
    return SourceService(
        stack.sources,
        stack.fragments,
        stack.graph,
        None,
        stack.audit,
        lambda: NOW,
        blobs=stack.blobs,
        imports=stack.imports,
        jobs=stack.queue,
        **ports,
    )


def test_delete_unlinks_the_watched_path_and_the_file_is_not_imported_again(
    stack: Stack, tmp_path
) -> None:
    repository = (
        SqliteWatchFolderRepository(stack.sources._db)
        if stack.name == "sqlite"
        else MemoryWatchFolderRepository()
    )
    watcher = FolderWatcher(repository, stack.service, stack.blobs, tmp_path / "nodo-data")
    root = tmp_path / "watched"
    root.mkdir()
    folder = watcher.add_folder(stack.ws, OWNER, str(root), NOW)
    (root / "notes.md").write_text("a stable note", encoding="utf-8")
    assert watcher.scan_once(NOW) + watcher.scan_once(NOW) == 1
    source_id = repository.get_file(folder.id, "notes.md").source_id

    _sources(stack, watch=repository).delete(stack.ws, OWNER, source_id)

    row = repository.get_file(folder.id, "notes.md")
    assert (row.source_id, row.linked_only) == (None, False)
    assert row.sha256 is not None  # kept while the file stays: no re-import of deleted bytes
    keys = list(stack.published.keys)
    assert watcher.scan_once(NOW) + watcher.scan_once(NOW) == 0
    assert stack.published.keys == keys


def test_a_discarded_upload_whose_erase_failed_is_finished_at_the_next_start(stack: Stack) -> None:
    stack.imported("plan.md", b"same bytes")
    second = stack.upload("copy.md", b"same bytes", DOC_A)
    erase = stack.blobs.erase
    calls = []

    def locked_once(workspace_id, source_id):
        calls.append(source_id)
        if len(calls) == 1:
            raise PermissionError("locked")
        return erase(workspace_id, source_id)

    stack.blobs.erase = locked_once
    with pytest.raises(PermissionError):
        stack.service.complete(stack.ws, OWNER, second.id)
    dead = stack.sources.get_owned(stack.ws, OWNER, second.id, include_tombstoned=True)
    assert (dead.state, dead.content_hash, dead.title) == ("deleting", None, None)
    assert stack.blobs.path_for(stack.ws, second.id, 1).exists()

    assert _sources(stack).finish_deletes() == 1
    assert not stack.blobs.path_for(stack.ws, second.id, 1).exists()
    dead = stack.sources.get_owned(stack.ws, OWNER, second.id, include_tombstoned=True)
    assert dead.state == "deleted"


def test_a_discarded_upload_ends_deleted_with_no_hash(stack: Stack) -> None:
    stack.imported("plan.md", b"same bytes")
    second = stack.upload("copy.md", b"same bytes", DOC_A)
    stack.service.complete(stack.ws, OWNER, second.id)
    dead = stack.sources.get_owned(stack.ws, OWNER, second.id, include_tombstoned=True)
    assert (dead.state, dead.content_hash) == ("deleted", None)
    assert stack.sources.unfinished_deletes(10) == ()


def test_a_delete_before_the_publish_lands_leaves_no_queue_key(stack: Stack) -> None:
    source = stack.upload("plan.md", b"plan v1", DOC_A)
    publish = stack.published.publish_once

    def deleted_first(*args):
        deleter(stack, source.id)()  # its queue discard ran before this message landed
        return publish(*args)

    stack.published.publish_once = deleted_first
    stack.service.complete(stack.ws, OWNER, source.id)
    assert stack.published.keys  # the message was published, then removed
    assert stack.queue.discard(stack.ws, IMPORT_SOURCE, f"{source.id}:") == 0
    with pytest.raises(NotFoundError):
        stack.sources.get_owned(stack.ws, OWNER, source.id)
