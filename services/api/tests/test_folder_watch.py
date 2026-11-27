"""Watched folders (Task 15) on the memory and the SQLite stores. Every file is written under
tmp_path with invented test text; no real folder or content."""

from __future__ import annotations

import logging
import os
import stat
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nodo_api.adapters.local.bootstrap import LocalRuntime
from nodo_api.adapters.local.watch_folders import SqliteWatchFolderRepository
from nodo_api.adapters.memory import MemoryWatchFolderRepository
from nodo_api.errors import ConflictError, NotFoundError, ValidationFailed
from nodo_api.services import folder_watch
from nodo_api.services.folder_watch import (
    CLOUD_TAG,
    RECALL_ON_DATA_ACCESS,
    REPARSE_POINT,
    FolderWatcher,
    skipped,
)
from nodo_api.services.source_import import job_key, source_id_for_key
from tests.test_source_audio_import import Speech, wav
from tests.test_source_import import DOC_A, DOC_B, OWNER, Stack, _stack, deleter

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
WINDOWS = sys.platform == "win32"


def _repository(stack: Stack):
    if stack.name == "sqlite":
        return SqliteWatchFolderRepository(stack.sources._db)
    return MemoryWatchFolderRepository()


@pytest.fixture(params=["memory", "sqlite"])
def watched(request, tmp_path):
    stack = _stack(request.param, tmp_path)
    service = FolderWatcher(_repository(stack), stack.service, stack.blobs, tmp_path / "nodo-data")
    root = tmp_path / "watched"
    root.mkdir()
    folder = service.add_folder(stack.ws, OWNER, str(root), NOW)
    yield stack, service, folder, root
    stack.blobs.close()


def _twice(service: FolderWatcher) -> int:
    return service.scan_once(NOW) + service.scan_once(NOW)


def _digest(stack: Stack, source_id: str) -> str:
    return stack.sources.get_owned(stack.ws, OWNER, source_id).content_hash


def test_new_file_is_queued_after_two_stable_scans(watched) -> None:
    stack, service, folder, root = watched
    (root / "notes.md").write_text("a stable note", encoding="utf-8")
    assert service.scan_once(NOW) == 0
    assert stack.published.keys == []
    assert service.scan_once(NOW) == 1
    tracked = service.repository.get_file(folder.id, "notes.md")
    digest = _digest(stack, tracked.source_id)
    assert tracked.sha256 == digest
    # The source is named after the bytes (a scan after a crash finds it); the queue key is the
    # ordinary import key, so cancel and the worker find the job.
    key = f"watch:{digest[:32]}:{folder.id}:notes.md"
    assert tracked.source_id == source_id_for_key(stack.ws, OWNER, key)
    assert stack.job(tracked.source_id).state == "queued"
    assert stack.published.keys == [job_key(tracked.source_id, digest, 0)]
    assert stack.sources.get_owned(stack.ws, OWNER, tracked.source_id).title == "notes.md"


def test_file_still_growing_is_not_queued(watched) -> None:
    _, service, _, root = watched
    path = root / "growing.md"
    path.write_text("one", encoding="utf-8")
    assert service.scan_once(NOW) == 0
    path.write_text("one two", encoding="utf-8")
    assert service.scan_once(NOW) == 0
    path.write_text("one two three", encoding="utf-8")
    assert service.scan_once(NOW) == 0
    assert service.scan_once(NOW) == 1  # stopped growing


def test_temp_and_lock_files_are_ignored(watched) -> None:
    stack, service, folder, root = watched
    names = ("~$draft.docx", ".~lock.plan.docx#", "a.tmp", "b.crdownload", "c.part", ".hidden.md")
    for name in (*names, "tool.exe", "data.json"):
        (root / name).write_text("ignored", encoding="utf-8")
    hidden_dir = root / ".git"
    hidden_dir.mkdir()
    (hidden_dir / "inside.md").write_text("ignored", encoding="utf-8")
    assert _twice(service) == 0
    assert stack.published.keys == []
    assert service.repository.get_file(folder.id, ".hidden.md") is None


def test_changed_file_creates_a_new_version_of_the_same_source(watched) -> None:
    stack, service, folder, root = watched
    path = root / "plan.md"
    path.write_text("version one", encoding="utf-8")
    assert _twice(service) == 1
    first = service.repository.get_file(folder.id, "plan.md")
    stack.register(first.source_id, 1, DOC_A)
    stack.service.process(stack.ws, first.source_id)
    assert stack.job(first.source_id).state == "ready"

    path.write_text("version two is longer", encoding="utf-8")
    stack.register(first.source_id, 2, DOC_B)
    assert _twice(service) == 1
    second = service.repository.get_file(folder.id, "plan.md")
    assert second.source_id == first.source_id and second.sha256 != first.sha256
    assert stack.blobs.path_for(stack.ws, first.source_id, 2).is_file()
    stack.service.process(stack.ws, first.source_id)
    versions = stack.fragments.versions(stack.ws, OWNER, first.source_id)
    assert [v.version for v in versions] == [2, 1]


def test_a_change_while_the_import_runs_is_retried_after_it(watched) -> None:
    stack, service, folder, root = watched
    path = root / "busy.md"
    path.write_text("first bytes", encoding="utf-8")
    assert _twice(service) == 1
    source_id = service.repository.get_file(folder.id, "busy.md").source_id
    path.write_text("second bytes, written while the first import is queued", encoding="utf-8")
    assert _twice(service) == 0  # one import of a source at a time: tried again later
    stack.register(source_id, 1, DOC_A)
    stack.service.process(stack.ws, source_id)
    assert _twice(service) == 1
    assert stack.blobs.path_for(stack.ws, source_id, 2).is_file()


def test_low_disk_is_retried_and_the_file_imported_later(watched, monkeypatch) -> None:
    stack, service, folder, root = watched
    (root / "later.md").write_text("waits for room on disk", encoding="utf-8")
    monkeypatch.setattr(stack.blobs, "_free_space", lambda probe: 0)
    assert _twice(service) == 0
    assert service.repository.get_file(folder.id, "later.md").sha256 is None
    monkeypatch.setattr(stack.blobs, "_free_space", lambda probe: 10**12)
    assert _twice(service) == 1
    source_id = service.repository.get_file(folder.id, "later.md").source_id
    assert stack.job(source_id).state == "queued"


def test_unchanged_file_is_a_noop(watched) -> None:
    stack, service, _folder, root = watched
    path = root / "stable.md"
    path.write_text("unchanged", encoding="utf-8")
    assert service.scan_once(NOW) == 0
    assert service.scan_once(NOW) == 1
    assert service.scan_once(NOW) == 0
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))  # touched only
    assert _twice(service) == 0
    assert len(stack.published.keys) == 1


def test_recall_on_data_access_placeholder_is_skipped(watched, monkeypatch) -> None:
    stack, service, folder, root = watched
    (root / "cloud.md").write_text("only in the cloud", encoding="utf-8")
    real = folder_watch._entry_info

    def placeholder(entry):
        info = real(entry)
        if entry.name != "cloud.md":
            return info
        return SimpleNamespace(
            st_mode=info.st_mode,
            st_size=info.st_size,
            st_mtime_ns=info.st_mtime_ns,
            st_file_attributes=RECALL_ON_DATA_ACCESS | REPARSE_POINT,
            st_reparse_tag=CLOUD_TAG,
        )

    monkeypatch.setattr(folder_watch, "_entry_info", placeholder)
    opened: list[Path] = []
    hashing = stack.blobs.size_and_sha256
    monkeypatch.setattr(stack.blobs, "size_and_sha256", lambda p: opened.append(p) or hashing(p))
    assert _twice(service) == 0
    assert opened == [] and service.repository.get_file(folder.id, "cloud.md") is None


def test_attribute_rules() -> None:
    regular = stat.S_IFREG | 0o644
    plain = SimpleNamespace(st_mode=regular, st_file_attributes=0, st_reparse_tag=0)
    assert not skipped(plain)
    for flag in (0x2, 0x4, 0x1000, 0x40000, RECALL_ON_DATA_ACCESS):  # hidden ... recall
        assert skipped(SimpleNamespace(st_mode=regular, st_file_attributes=flag))
    # A local OneDrive file is a cloud reparse point: read. A junction or another link: never.
    cloud = SimpleNamespace(
        st_mode=regular, st_file_attributes=REPARSE_POINT, st_reparse_tag=CLOUD_TAG
    )
    assert not skipped(cloud)
    for tag in (0xA0000003, 0xA000000C, 0x80000014, 0):  # junction, symlink, other, unknown
        link = SimpleNamespace(
            st_mode=regular, st_file_attributes=REPARSE_POINT, st_reparse_tag=tag
        )
        assert skipped(link)
    assert skipped(SimpleNamespace(st_mode=stat.S_IFLNK | 0o777))


def test_root_inside_the_data_folder_is_refused(tmp_path) -> None:
    data = tmp_path / "data"
    inside = data / "watched"
    inside.mkdir(parents=True)
    (tmp_path / "file.md").write_text("not a folder", encoding="utf-8")
    service = FolderWatcher(MemoryWatchFolderRepository(), None, None, data)
    for path in (
        "relative",
        str(tmp_path / "missing"),
        str(inside),
        str(data),
        str(tmp_path),  # contains the data folder: the store would import itself
        str(tmp_path / "file.md"),
        "",
    ):
        with pytest.raises(ValidationFailed):
            service.add_folder("ws", OWNER, path, NOW)
    other = tmp_path / "other"
    other.mkdir()
    service.add_folder("ws", OWNER, str(other), NOW)
    with pytest.raises(ConflictError):
        service.add_folder("ws", OWNER, str(other), NOW)


def test_sqlite_refuses_the_same_folder_twice(tmp_path) -> None:
    stack = _stack("sqlite", tmp_path)
    service = FolderWatcher(_repository(stack), stack.service, stack.blobs, tmp_path / "data")
    root = tmp_path / "watched"
    root.mkdir()
    service.add_folder(stack.ws, OWNER, str(root), NOW)
    with pytest.raises(ConflictError):
        service.add_folder(stack.ws, OWNER, str(root), NOW)
    stack.blobs.close()


def test_deleting_a_watched_source_does_not_reimport_it_until_the_file_changes(watched) -> None:
    stack, service, folder, root = watched
    path = root / "delete.md"
    path.write_text("first", encoding="utf-8")
    assert _twice(service) == 1
    tracked = service.repository.get_file(folder.id, "delete.md")
    deleter(stack, tracked.source_id)()
    assert _twice(service) == 0
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    assert _twice(service) == 0  # touched, same bytes
    # A restart (a new watcher on the same store) does not forget it either.
    again = FolderWatcher(service.repository, stack.service, stack.blobs, root.parent / "data")
    assert again.scan_once(NOW) + again.scan_once(NOW) == 0
    path.write_text("changed after delete", encoding="utf-8")
    assert _twice(service) == 1
    renewed = service.repository.get_file(folder.id, "delete.md")
    assert renewed.source_id != tracked.source_id
    with pytest.raises(NotFoundError):
        stack.sources.get_owned(stack.ws, OWNER, tracked.source_id)


def test_deleting_a_watch_folder_keeps_its_sources_and_stops_the_scan(watched) -> None:
    stack, service, folder, root = watched
    (root / "kept.md").write_text("kept", encoding="utf-8")
    assert _twice(service) == 1
    source_id = service.repository.get_file(folder.id, "kept.md").source_id
    service.delete_folder(stack.ws, OWNER, folder.id)
    assert service.list_folders(stack.ws, OWNER) == ()
    assert service.repository.get_file(folder.id, "kept.md") is None
    assert stack.sources.get_owned(stack.ws, OWNER, source_id).id == source_id
    (root / "later.md").write_text("never seen", encoding="utf-8")
    assert _twice(service) == 0
    with pytest.raises(NotFoundError):
        service.delete_folder(stack.ws, OWNER, folder.id)


def test_scan_limits_depth_and_entries(watched, monkeypatch) -> None:
    stack, service, folder, root = watched
    deep = root
    for index in range(6):
        deep = deep / f"level{index}"
        deep.mkdir()
        (deep / f"at{index + 1}.md").write_text(f"depth {index + 1}", encoding="utf-8")
    assert _twice(service) == 5  # folders down to depth 5; the sixth is not read
    tracked = "level0/level1/level2/level3/level4/at5.md"
    assert service.repository.get_file(folder.id, tracked) is not None
    assert service.repository.get_file(folder.id, tracked.replace("at5", "level5/at6")) is None

    flat = root.parent / "flat"
    flat.mkdir()
    for index in range(5):
        (flat / f"{index}.md").write_text(f"file {index}", encoding="utf-8")
    other = service.add_folder(stack.ws, OWNER, str(flat), NOW)
    service.delete_folder(stack.ws, OWNER, folder.id)
    monkeypatch.setattr(folder_watch, "MAX_ENTRIES", 3)
    assert _twice(service) == 3
    assert service.repository.get_file(other.id, "3.md") is None


@pytest.mark.skipif(not WINDOWS, reason="junctions are a Windows feature")
def test_junctions_are_never_followed(watched, tmp_path) -> None:
    import _winapi

    stack, service, _folder, root = watched
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("outside the root", encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(root / "jump"))
    assert _twice(service) == 0
    assert stack.published.keys == []
    linked_root = tmp_path / "linked-root"
    _winapi.CreateJunction(str(outside), str(linked_root))
    with pytest.raises(ValidationFailed):
        service.add_folder(stack.ws, OWNER, str(linked_root), NOW)


def test_symbolic_links_are_never_followed(watched, tmp_path) -> None:
    stack, service, _, root = watched
    outside = tmp_path / "outside-file.md"
    outside.write_text("outside the root", encoding="utf-8")
    try:
        os.symlink(outside, root / "link.md")
        os.symlink(tmp_path, root / "link-dir", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("creating symbolic links is not permitted here")
    assert _twice(service) == 0
    assert stack.published.keys == []
    with pytest.raises(ValidationFailed):
        service.add_folder(stack.ws, OWNER, str(root / "link-dir"), NOW)


@pytest.fixture(params=["memory", "sqlite"])
def watched_audio(request, tmp_path):
    stack = _stack(request.param, tmp_path)
    stack.speech = Speech()  # type: ignore[attr-defined]
    stack.service = stack.build(speech=stack.speech, speech_locales=("en-GB",))  # type: ignore[attr-defined]
    service = FolderWatcher(_repository(stack), stack.service, stack.blobs, tmp_path / "nodo-data")
    root = tmp_path / "watched"
    root.mkdir()
    folder = service.add_folder(stack.ws, OWNER, str(root), NOW)
    yield stack, service, folder, root
    stack.blobs.close()


def test_a_watched_recording_waits_for_confirmation_also_after_a_change(watched_audio) -> None:
    stack, service, folder, root = watched_audio
    path = root / "standup.wav"
    path.write_bytes(wav(1600))
    assert _twice(service) == 1
    source_id = service.repository.get_file(folder.id, "standup.wav").source_id
    assert stack.job(source_id).state == "awaiting_confirmation"
    assert stack.published.keys == [] and stack.speech.calls == []
    stack.service.process(stack.ws, source_id)  # a stray delivery processes nothing
    assert stack.speech.calls == []

    path.write_bytes(wav(3200))
    assert _twice(service) == 1
    assert service.repository.get_file(folder.id, "standup.wav").source_id == source_id
    job = stack.job(source_id)
    assert (job.state, job.step) == ("awaiting_confirmation", None)
    stack.service.process(stack.ws, source_id)
    assert stack.published.keys == [] and stack.speech.calls == []
    stack.service.confirm(stack.ws, OWNER, source_id)
    stack.service.process(stack.ws, source_id)
    assert len(stack.speech.calls) == 1


def test_scans_never_log_a_path_or_a_name(watched, caplog) -> None:
    _, service, _, root = watched
    (root / "private-name.md").write_text("private text", encoding="utf-8")
    (root / "empty.md").write_bytes(b"")
    caplog.set_level(logging.DEBUG)
    _twice(service)
    text = caplog.text
    assert "private" not in text and str(root) not in text and "empty.md" not in text


def test_one_scan_at_a_time(watched) -> None:
    _, service, _, root = watched
    (root / "a.md").write_text("a", encoding="utf-8")
    with service._scanning:
        assert _twice(service) == 0
    assert _twice(service) == 1


def test_the_watch_loop_stops_and_logs_failures_by_code(caplog) -> None:
    repository = MemoryWatchFolderRepository()
    repository.list_enabled = MagicMock(side_effect=OSError("C:/private/path"))
    service = FolderWatcher(repository, None, None, "unused")
    caplog.set_level(logging.INFO)
    service.scan_once = MagicMock(side_effect=OSError("C:/private/path"))
    stop = threading.Event()
    thread = threading.Thread(target=service.run, args=(stop, 0.01), daemon=True)
    thread.start()
    while service.scan_once.call_count < 2:
        stop.wait(0.01)
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert "watch_scan_failed type=OSError" in caplog.text and "private" not in caplog.text


def test_local_runtime_runs_the_watch_thread_while_started() -> None:
    watcher = MagicMock()
    watcher.run.side_effect = lambda stop, poll: stop.wait()
    runtime = LocalRuntime(
        *(MagicMock() for _ in range(11)), watcher=watcher, watch_poll_seconds=60
    )
    runtime.server = None
    runtime.lock = None
    runtime.imports = None
    runtime.start()
    try:
        names = [t.name for t in threading.enumerate()]
        assert "nodo-watch" in names
    finally:
        runtime.stop()
    assert "nodo-watch" not in [t.name for t in threading.enumerate() if t.is_alive()]
    watcher.run.assert_called_once()


def test_watch_routes_are_owner_scoped_and_501_on_azure(app, client, auth_headers, tmp_path):
    root = tmp_path / "api-watch"
    root.mkdir()
    created = client.post(
        "/v1/sources/watch-folders", headers=auth_headers, json={"path": str(root)}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["path"] == str(root) and body["enabled"] is True
    folder_id = body["id"]
    listed = client.get("/v1/sources/watch-folders", headers=auth_headers).json()["items"]
    assert [item["id"] for item in listed] == [folder_id]
    for bad in ("relative/folder", str(tmp_path / "missing")):
        response = client.post(
            "/v1/sources/watch-folders", headers=auth_headers, json={"path": bad}
        )
        assert response.status_code == 422
    again = client.post("/v1/sources/watch-folders", headers=auth_headers, json={"path": str(root)})
    assert again.status_code == 409

    app.state.container.auth.add_dev_token("bob-watch-token-012345", "bob", "workspace-a")
    bob = {"Authorization": "Bearer bob-watch-token-012345"}
    assert client.get("/v1/sources/watch-folders", headers=bob).json()["items"] == []
    assert client.delete(f"/v1/sources/watch-folders/{folder_id}", headers=bob).status_code == 404
    assert client.get("/v1/sources/watch-folders").status_code == 401
    deleted = client.delete(f"/v1/sources/watch-folders/{folder_id}", headers=auth_headers)
    assert deleted.status_code == 204

    app.state.container.folder_watcher = None
    for method, path in (
        ("get", "/v1/sources/watch-folders"),
        ("post", "/v1/sources/watch-folders"),
        ("delete", "/v1/sources/watch-folders/anything"),
    ):
        response = client.request(method, path, headers=auth_headers, json={"path": str(root)})
        assert response.status_code == 501
        assert response.json()["error"]["code"] == "not_available_on_engine"


# --------------------------------------------------------------------------- review fixes


def _frozen_listing(monkeypatch, name: str) -> None:
    """Make the directory listing lie like NTFS does for a file a writer keeps open: the size
    and time it shows do not move until the writer closes the file."""
    real = folder_watch._entry_info
    frozen: dict[str, object] = {}

    def listing(entry):
        info = real(entry)
        if entry.name != name:
            return info
        frozen.setdefault("info", info)
        first = frozen["info"]
        return SimpleNamespace(
            st_mode=first.st_mode,
            st_size=0,
            st_mtime_ns=first.st_mtime_ns,
            st_file_attributes=0,
            st_reparse_tag=0,
        )

    monkeypatch.setattr(folder_watch, "_entry_info", listing)


def test_a_file_held_open_by_its_writer_is_not_imported(watched, monkeypatch) -> None:
    stack, service, folder, root = watched
    path = root / "copying.md"
    _frozen_listing(monkeypatch, "copying.md")
    with path.open("wb") as writer:
        for index in range(4):
            writer.write(b"chunk %d of a file still being copied\n" % index)
            writer.flush()
            os.fsync(writer.fileno())
            assert service.scan_once(NOW) == 0
    assert stack.published.keys == []
    assert _twice(service) == 1
    tracked = service.repository.get_file(folder.id, "copying.md")
    assert tracked.sha256 == stack.blobs.size_and_sha256(path)[1]


def test_a_file_that_changes_while_it_is_hashed_is_retried(watched, monkeypatch) -> None:
    stack, service, folder, root = watched
    path = root / "racing.md"
    path.write_bytes(b"first part")
    real = stack.blobs.size_and_sha256
    calls = []

    def hash_then_write(target):
        result = real(target)
        if Path(target) == path and not calls:
            calls.append(target)
            with path.open("ab") as more:
                more.write(b" and a later part")
        return result

    monkeypatch.setattr(stack.blobs, "size_and_sha256", hash_then_write)
    assert _twice(service) == 0
    assert service.repository.get_file(folder.id, "racing.md").sha256 is None
    assert _twice(service) == 1
    source_id = service.repository.get_file(folder.id, "racing.md").source_id
    assert _digest(stack, source_id) == real(path)[1]


def test_a_copy_that_differs_from_the_hash_is_dropped(watched, monkeypatch) -> None:
    stack, service, folder, root = watched
    path = root / "swapped.md"
    path.write_bytes(b"what was hashed")
    real = stack.blobs.adopt
    calls = []

    def write_then_adopt(ws, source_id, version, src):
        if not calls:
            calls.append(source_id)
            Path(src).write_bytes(b"written between the hash and the copy")
        return real(ws, source_id, version, src)

    monkeypatch.setattr(stack.blobs, "adopt", write_then_adopt)
    assert _twice(service) == 0
    assert not stack.blobs.path_for(stack.ws, calls[0], 1).is_file()
    assert stack.published.keys == []
    assert _twice(service) == 1
    source_id = service.repository.get_file(folder.id, "swapped.md").source_id
    stored = stack.blobs.path_for(stack.ws, source_id, 1)
    assert stored.read_bytes() == b"written between the hash and the copy"


def test_a_changed_file_whose_copy_differs_is_not_a_new_version(watched, monkeypatch) -> None:
    stack, service, folder, root = watched
    path = root / "edited.md"
    path.write_bytes(b"version one")
    assert _twice(service) == 1
    source_id = service.repository.get_file(folder.id, "edited.md").source_id
    stack.register(source_id, 1, DOC_A)
    stack.service.process(stack.ws, source_id)
    path.write_bytes(b"version two")
    real = stack.blobs.adopt
    calls = []

    def write_then_adopt(ws, sid, version, src):
        if not calls:
            calls.append(version)
            Path(src).write_bytes(b"version two, still being saved")
        return real(ws, sid, version, src)

    monkeypatch.setattr(stack.blobs, "adopt", write_then_adopt)
    assert _twice(service) == 0
    assert not stack.blobs.path_for(stack.ws, source_id, 2).is_file()
    assert stack.job(source_id).state == "ready"
    assert _twice(service) == 1
    assert stack.blobs.path_for(stack.ws, source_id, 2).read_bytes() == (
        b"version two, still being saved"
    )


def test_a_file_replaced_after_its_stat_is_refused(tmp_path) -> None:
    path = tmp_path / "file.md"
    path.write_bytes(b"checked")
    checked = os.stat(path, follow_symlinks=False)
    folder_watch.FolderWatcher._held(path, checked)  # the same file passes
    path.unlink()
    (tmp_path / "other.md").write_bytes(b"checked")
    os.replace(tmp_path / "other.md", path)
    with pytest.raises(folder_watch.Moved):
        folder_watch.FolderWatcher._held(path, checked)


def test_one_bad_file_does_not_stop_the_rest_of_the_folder(watched, monkeypatch) -> None:
    stack, service, folder, root = watched
    (root / "a-poison.md").write_text("adopt fails for this one", encoding="utf-8")
    (root / "b-fine.md").write_text("this one is fine", encoding="utf-8")
    real = stack.blobs.adopt

    def poison(ws, source_id, version, src):
        if Path(src).name == "a-poison.md":
            raise ValueError("Unsafe path length")
        return real(ws, source_id, version, src)

    monkeypatch.setattr(stack.blobs, "adopt", poison)
    assert _twice(service) == 1
    assert service.repository.get_file(folder.id, "b-fine.md").source_id is not None
    assert service.repository.get_file(folder.id, "a-poison.md").sha256 is None


def test_an_unchanged_file_is_not_hashed_again(watched, monkeypatch) -> None:
    stack, service, _, root = watched
    (root / "once.md").write_text("hashed once", encoding="utf-8")
    assert _twice(service) == 1
    real = stack.blobs.size_and_sha256
    calls = []
    monkeypatch.setattr(stack.blobs, "size_and_sha256", lambda p: calls.append(p) or real(p))
    assert _twice(service) + _twice(service) == 0
    assert calls == []


def _report_twins(stack: Stack, service: FolderWatcher, folder, root: Path):
    (root / "report.md").write_bytes(b"the same report")
    (root / "report_copy.md").write_bytes(b"the same report")
    assert _twice(service) == 1  # one source: the copy is linked to it, not a duplicate
    original = service.repository.get_file(folder.id, "report.md")
    copy = service.repository.get_file(folder.id, "report_copy.md")
    assert copy.source_id == original.source_id
    assert (original.linked_only, copy.linked_only) == (False, True)
    stack.register(original.source_id, 1, DOC_A)
    stack.service.process(stack.ws, original.source_id)
    return original.source_id


def test_an_edited_copy_of_a_watched_file_starts_its_own_source(watched) -> None:
    stack, service, folder, root = watched
    original = _report_twins(stack, service, folder, root)
    (root / "report_copy.md").write_bytes(b"the copy, edited")
    assert _twice(service) == 1
    copy = service.repository.get_file(folder.id, "report_copy.md")
    assert copy.source_id != original and copy.linked_only is False
    kept = stack.sources.get_owned(stack.ws, OWNER, original)
    assert kept.title == "report.md" and kept.content_hash == _sha(b"the same report")
    assert not stack.blobs.path_for(stack.ws, original, 2).is_file()
    # The path that created the source still versions it.
    (root / "report.md").write_bytes(b"the report, edited")
    stack.register(original, 2, DOC_B)
    assert _twice(service) == 1
    assert stack.blobs.path_for(stack.ws, original, 2).is_file()


def test_a_watched_copy_of_an_uploaded_source_never_changes_it(watched) -> None:
    stack, service, folder, root = watched
    uploaded = stack.imported("hand.md", b"uploaded by hand")
    (root / "hand copy.md").write_bytes(b"uploaded by hand")
    assert _twice(service) == 0  # linked to the upload: nothing new to import
    copy = service.repository.get_file(folder.id, "hand copy.md")
    assert (copy.source_id, copy.linked_only) == (uploaded.id, True)
    (root / "hand copy.md").write_bytes(b"edited in the watched folder")
    assert _twice(service) == 1
    assert service.repository.get_file(folder.id, "hand copy.md").source_id != uploaded.id
    kept = stack.sources.get_owned(stack.ws, OWNER, uploaded.id)
    assert kept.title == "hand.md" and kept.content_hash == _sha(b"uploaded by hand")
    assert not stack.blobs.path_for(stack.ws, uploaded.id, 2).is_file()


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def test_vanished_files_are_pruned_after_a_full_scan_only(watched, monkeypatch) -> None:
    _, service, folder, root = watched
    for name in ("a.md", "b.md", "c.md"):
        (root / name).write_text(f"text of {name}", encoding="utf-8")
    assert _twice(service) == 3
    (root / "a.md").unlink()
    monkeypatch.setattr(folder_watch, "MAX_ENTRIES", 1)  # b.md only: not a full scan
    service.scan_once(NOW)
    assert service.repository.get_file(folder.id, "c.md") is not None
    monkeypatch.setattr(folder_watch, "MAX_ENTRIES", 5000)
    service.scan_once(NOW)
    assert service.repository.get_file(folder.id, "a.md") is None
    assert service.repository.get_file(folder.id, "b.md") is not None


def test_files_below_the_depth_limit_are_not_pruned(watched, monkeypatch) -> None:
    _, service, folder, root = watched
    (root / "top.md").write_text("top", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "deep.md").write_text("deep", encoding="utf-8")
    assert _twice(service) == 2
    monkeypatch.setattr(folder_watch, "MAX_DEPTH", 0)
    service.scan_once(NOW)
    assert service.repository.get_file(folder.id, "sub/deep.md") is not None


def test_network_and_device_roots_are_refused(tmp_path) -> None:
    service = FolderWatcher(MemoryWatchFolderRepository(), None, None, tmp_path / "data")
    for path in (
        "\\\\server\\share\\folder",
        "//server/share/folder",
        "\\\\?\\UNC\\server\\share\\folder",
        "\\\\.\\C:\\",
        "\\\\localhost\\C$\\Users",
    ):
        with pytest.raises(ValidationFailed):
            service.add_folder("ws", OWNER, path, NOW)


@pytest.mark.skipif(not WINDOWS, reason="Windows path forms")
def test_every_spelling_of_the_data_folder_is_refused(tmp_path) -> None:
    import _winapi
    import ctypes

    data = tmp_path / "NodoDataFolderLongName"
    (data / "inside_long_name").mkdir(parents=True)
    service = FolderWatcher(MemoryWatchFolderRepository(), None, None, data)
    spellings = [
        "\\\\?\\" + str(data),
        "\\\\?\\" + str(data / "inside_long_name"),
        str(data).upper(),
        str(data) + "\\",
        str(data / "inside_long_name" / ".." / "inside_long_name"),
    ]
    buffer = ctypes.create_unicode_buffer(1024)
    found = ctypes.windll.kernel32.GetShortPathNameW(str(data), buffer, 1024)
    if found and buffer.value.lower() != str(data).lower():
        spellings.append(buffer.value)
    jump = tmp_path / "jump"
    _winapi.CreateJunction(str(data), str(jump))
    spellings.append(str(jump / "inside_long_name"))  # an ancestor is a junction into it
    for path in spellings:
        with pytest.raises(ValidationFailed):
            service.add_folder("ws", OWNER, path, NOW)


def test_the_poll_interval_has_a_floor() -> None:
    from pydantic import ValidationError

    from nodo_api.config import Settings

    base = {"environment": "test", "auth_mode": "dev", "attestation_hmac_key": "x" * 32}
    with pytest.raises(ValidationError):
        Settings(**base, watch_poll_seconds=1)
    assert Settings(**base, watch_poll_seconds=5).watch_poll_seconds == 5
