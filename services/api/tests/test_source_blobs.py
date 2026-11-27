"""Original file storage (Task 6): names, ids, atomic writes, size caps and complete erasure.

The local (disk) store and the memory store run the same suite. Each test class provides the
fixture `make`, a factory that builds a store with the given limits."""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nodo_api.adapters.local.source_blobs import LocalSourceBlobStore, safe_filename
from nodo_api.adapters.memory import MemorySourceBlobStore
from nodo_api.domain.source_files import UploadRejected
from nodo_api.errors import NotFoundError

WS = "ws-1"
OWNER = "user-a"
MIB = 1024 * 1024


def files(store) -> list[str]:
    return sorted(
        p.relative_to(store.root).as_posix() for p in store.root.rglob("*") if p.is_file()
    )


def entries(store) -> list[str]:
    return sorted(p.relative_to(store.root).as_posix() for p in store.root.rglob("*"))


def upload(store, data: bytes, sid="s1", version=1, name="report.pdf", chunk=4):
    session = store.create_upload_session(WS, OWNER, sid, name, len(data), version=version)
    with store.open_upload(session.token) as writer:
        for i in range(0, len(data), chunk):
            writer.write(data[i : i + chunk])
    return session, writer


class FireAt:
    """Stands in for the store lock and runs `action` just before the n-th time it is taken, so a
    test can place an erase at an exact point of another operation."""

    def __init__(self, lock, n, action):
        self.lock, self.n, self.action, self.count = lock, n, action, 0

    def __enter__(self):
        self.count += 1
        if self.count == self.n:
            self.action()
        return self.lock.__enter__()

    def __exit__(self, *exc):
        return self.lock.__exit__(*exc)


def disk_full(*args, **kwargs):
    raise OSError(errno.ENOSPC, "No space left on device")


class TestSafeFilename:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("Report 2026.pdf", "Report 2026.pdf"),
            ("dir/sub/file.docx", "file.docx"),
            ("C:\\Users\\me\\notes.md", "notes.md"),
            ("relatorio-final.txt", "relatorio-final.txt"),
            ("a" * 200, "a" * 200),
            ("CONSOLE.txt", "CONSOLE.txt"),
        ],
    )
    def test_good_names_keep_only_the_last_part(self, given, expected):
        assert safe_filename(given) == expected

    @pytest.mark.parametrize(
        "name",
        [
            "CON.pdf",
            "con",
            "NUL",
            "nul.tar.gz",
            "Aux .txt",
            "COM1",
            "lpt9.log",
            "COM" + chr(0xB9),  # superscript one: reserved on Windows too
            "a/../b",
            "..",
            ".",
            "x.",
            "x ",
            " ",
            "",
            "dir/",
            "a" * 201,
            "bad\x00.pdf",
            "tab\t.pdf",
            "del\x7f.pdf",
            "a:b.pdf",
            "C:evil.pdf",
            "x" + chr(0x202E) + "fdp.exe",  # right-to-left override
            "lone" + chr(0xD800) + ".pdf",
            None,
            12,
        ],
    )
    def test_reserved_and_hostile_names_are_refused(self, name):
        with pytest.raises(ValueError, match=r"^Unsafe file name$"):
            safe_filename(name)


class SourceBlobContract:
    def test_original_name_never_becomes_a_path(self, make):
        store = make()
        session, _ = upload(store, b"hello", name="C:\\evil\\Report 2026.pdf")
        assert session.filename == "Report 2026.pdf"
        assert store.path_for(WS, "s1", 1) == store.root / WS / "s1" / "1" / "original"
        assert files(store) == [f"{WS}/s1/1/original"]

    def test_hostile_name_refused_before_anything_is_created(self, make):
        store = make()
        with pytest.raises(ValueError):
            store.create_upload_session(WS, OWNER, "s1", "CON.pdf", 5)
        assert entries(store) == []

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            ".",
            "..",
            "a/b",
            "a\\b",
            "C:",
            "c:x",
            "/abs",
            "\\abs",
            "a.b",
            "a b",
            "NUL",
            "con",
            "x" * 129,
            "a\x00b",
            "\u00e9",
        ],
    )
    def test_ids_used_in_paths_are_validated(self, make, bad):
        store = make()
        with pytest.raises(ValueError, match=r"^Unsafe identifier$"):
            store.path_for(bad, "s1", 1)
        with pytest.raises(ValueError, match=r"^Unsafe identifier$"):
            store.path_for(WS, bad, 1)
        with pytest.raises(ValueError, match=r"^Unsafe identifier$"):
            store.create_upload_session(bad, OWNER, "s1", "a.pdf", 1)
        with pytest.raises(ValueError, match=r"^Unsafe identifier$"):
            store.create_upload_session(WS, OWNER, bad, "a.pdf", 1)
        with pytest.raises(ValueError, match=r"^Unsafe identifier$"):
            store.erase(WS, bad)
        with pytest.raises(ValueError, match=r"^Unsafe identifier$"):
            store.erase(bad, "s1")
        assert entries(store) == []

    @pytest.mark.parametrize("bad", [0, -1, True, "1", 1.0, 10**7])
    def test_versions_are_small_positive_integers(self, make, bad):
        store = make()
        with pytest.raises(ValueError, match=r"^Unsafe version$"):
            store.path_for(WS, "s1", bad)

    def test_upload_streams_to_part_file_then_renames(self, make):
        store = make()
        data = b"0123456789" * 100
        session = store.create_upload_session(WS, OWNER, "s1", "a.pdf", len(data))
        final = store.path_for(WS, "s1", 1)
        with store.open_upload(session.token) as writer:
            writer.write(data[:500])
            assert not final.exists()  # nothing readable while the body is still arriving
            assert [p.suffix for p in final.parent.iterdir()] == [".part"]
            writer.write(data[500:])
            assert not final.exists()
        assert final.read_bytes() == data
        assert [p.name for p in final.parent.iterdir()] == ["original"]
        assert writer.size == len(data)
        assert writer.sha256 == hashlib.sha256(data).hexdigest()
        assert (writer.workspace_id, writer.owner_id, writer.source_id) == (WS, OWNER, "s1")

    def test_a_token_works_once(self, make):
        store = make()
        session, _ = upload(store, b"abc")
        with pytest.raises(UploadRejected) as error, store.open_upload(session.token):
            pass
        assert error.value.code == "unknown_token"

    def test_unknown_and_expired_tokens_are_refused(self, make):
        clock = [datetime(2026, 11, 13, tzinfo=UTC)]
        store = make(now=lambda: clock[0], upload_seconds=60)
        with pytest.raises(UploadRejected), store.open_upload("nope"):
            pass
        session = store.create_upload_session(WS, OWNER, "s1", "a.pdf", 3)
        clock[0] += timedelta(seconds=61)
        with pytest.raises(UploadRejected), store.open_upload(session.token):
            pass
        assert entries(store) == []

    def test_failed_upload_leaves_no_file_and_frees_the_token(self, make):
        store = make()
        session = store.create_upload_session(WS, OWNER, "s1", "a.pdf", 6)
        with pytest.raises(RuntimeError), store.open_upload(session.token) as writer:
            writer.write(b"abc")
            raise RuntimeError("connection dropped")
        assert entries(store) == []  # not even the empty folders
        with store.open_upload(session.token) as writer:  # the same link works again
            writer.write(b"abcdef")
        assert store.path_for(WS, "s1", 1).read_bytes() == b"abcdef"

    def test_a_link_in_use_cannot_be_opened_twice(self, make):
        store = make()
        session = store.create_upload_session(WS, OWNER, "s1", "a.pdf", 3)
        with store.open_upload(session.token) as writer:
            with pytest.raises(UploadRejected), store.open_upload(session.token):
                pass
            writer.write(b"abc")
        assert store.path_for(WS, "s1", 1).read_bytes() == b"abc"

    def test_upload_over_the_size_cap_is_refused(self, make):
        store = make(max_bytes=100)
        with pytest.raises(UploadRejected) as error:
            store.create_upload_session(WS, OWNER, "s1", "a.pdf", 101)
        assert error.value.code == "too_large"
        assert entries(store) == []

    def test_more_bytes_than_declared_stop_the_stream_at_once(self, make):
        store = make()
        session = store.create_upload_session(WS, OWNER, "s1", "a.pdf", 10)
        part_sizes = []
        with pytest.raises(UploadRejected) as error, store.open_upload(session.token) as writer:
            writer.write(b"x" * 6)
            part = next(store.path_for(WS, "s1", 1).parent.iterdir())
            writer.write(b"y" * 6)  # 12 > 10: refused on this very chunk
            part_sizes.append(part.stat().st_size)  # never reached
        assert error.value.code == "too_large"
        assert part_sizes == []
        assert entries(store) == []

    def test_fewer_bytes_than_declared_leave_no_file(self, make):
        store = make()
        session = store.create_upload_session(WS, OWNER, "s1", "a.pdf", 10)
        with pytest.raises(UploadRejected) as error, store.open_upload(session.token) as writer:
            writer.write(b"short")
        assert error.value.code == "size_mismatch"
        assert entries(store) == []

    def test_empty_declared_size_is_refused(self, make):
        with pytest.raises(ValueError):
            make().create_upload_session(WS, OWNER, "s1", "a.pdf", 0)

    def test_low_disk_space_is_refused(self, make):
        free = [999]
        store = make(free_space=lambda path: free[0])
        with pytest.raises(UploadRejected) as error:
            store.create_upload_session(WS, OWNER, "s1", "a.pdf", 500)  # needs 1000
        assert error.value.code == "low_disk"
        assert entries(store) == []
        free[0] = 1000
        assert store.create_upload_session(WS, OWNER, "s1", "a.pdf", 500).token

    def test_a_new_upload_of_the_same_version_replaces_it_atomically(self, make, monkeypatch):
        store = make()
        upload(store, b"old content")
        session = store.create_upload_session(WS, OWNER, "s1", "a.pdf", 11)

        def broken(src, dst):
            raise OSError("disk error")

        monkeypatch.setattr("nodo_api.adapters.local.source_blobs.os.replace", broken)
        with pytest.raises(OSError), store.open_upload(session.token) as writer:
            writer.write(b"new content")
        monkeypatch.undo()
        assert store.path_for(WS, "s1", 1).read_bytes() == b"old content"
        assert files(store) == [f"{WS}/s1/1/original"]
        upload(store, b"new content")
        assert store.path_for(WS, "s1", 1).read_bytes() == b"new content"

    def test_adopt_copies_a_watched_file(self, make, tmp_path):
        store = make()
        src = tmp_path / "watched" / "note.txt"
        src.parent.mkdir()
        src.write_bytes(b"watched bytes")
        dest = store.adopt(WS, "s1", 2, src)
        assert dest == store.path_for(WS, "s1", 2)
        assert dest.read_bytes() == b"watched bytes"
        assert src.read_bytes() == b"watched bytes"  # the original stays where it was
        assert [p.name for p in dest.parent.iterdir()] == ["original"]

    def test_adopt_refuses_big_missing_and_cramped(self, make, tmp_path):
        store = make(max_bytes=10, free_space=lambda path: 10**9)
        big = tmp_path / "big.bin"
        big.write_bytes(b"x" * 11)
        with pytest.raises(UploadRejected) as error:
            store.adopt(WS, "s1", 1, big)
        assert error.value.code == "too_large"
        with pytest.raises(NotFoundError):
            store.adopt(WS, "s1", 1, tmp_path / "missing.txt")
        with pytest.raises(NotFoundError):
            store.adopt(WS, "s1", 1, tmp_path)  # a folder is not a file
        small = tmp_path / "small.bin"
        small.write_bytes(b"x" * 10)
        tight = make(free_space=lambda path: 19)
        with pytest.raises(UploadRejected) as error:
            tight.adopt(WS, "s1", 1, small)
        assert error.value.code == "low_disk"
        assert entries(store) == [] and entries(tight) == []

    def test_size_and_sha256(self, make, tmp_path):
        data = b"abc" * 1_000_000  # more than one read block
        path = tmp_path / "x.bin"
        path.write_bytes(data)
        assert make().size_and_sha256(path) == (len(data), hashlib.sha256(data).hexdigest())

    def test_erase_removes_every_version_and_every_leftover(self, make):
        store = make()
        upload(store, b"one", version=1)
        upload(store, b"two", version=2)
        upload(store, b"other", sid="s2")
        leftover = store.path_for(WS, "s1", 3)
        leftover.parent.mkdir(parents=True)
        (leftover.parent / "original.abc123.part").write_bytes(b"crashed")
        store.erase(WS, "s1")
        assert not (store.root / WS / "s1").exists()
        assert store.path_for(WS, "s2", 1).read_bytes() == b"other"
        store.erase(WS, "s2")
        assert entries(store) == []  # the empty workspace folder goes too

    def test_erase_is_idempotent_and_accepts_unknown_sources(self, make):
        store = make()
        store.erase(WS, "never-existed")
        upload(store, b"one")
        store.erase(WS, "s1")
        store.erase(WS, "s1")
        assert entries(store) == []

    def test_erase_cancels_open_links_and_running_uploads(self, make):
        store = make()
        waiting = store.create_upload_session(WS, OWNER, "s1", "a.pdf", 4)
        running = store.create_upload_session(WS, OWNER, "s1", "a.pdf", 8, version=2)
        with pytest.raises(UploadRejected) as error, store.open_upload(running.token) as writer:
            writer.write(b"half")
            store.erase(WS, "s1")  # the owner deletes the source mid-upload
            writer.write(b"more")
        assert error.value.code == "cancelled"
        with pytest.raises(UploadRejected), store.open_upload(waiting.token):
            pass
        assert entries(store) == []

    def test_a_finished_upload_that_lost_a_race_with_erase_leaves_nothing(self, make):
        store = make()
        session = store.create_upload_session(WS, OWNER, "s1", "a.pdf", 4)
        with pytest.raises(UploadRejected), store.open_upload(session.token) as writer:
            writer.write(b"done")
            store.erase(WS, "s1")
        assert entries(store) == []

    def test_erase_waits_for_a_reader_that_still_holds_the_file(self, make):
        # Windows refuses to delete a file somebody has open; the store retries for a moment.
        store = make()
        upload(store, b"content")
        reader = store.path_for(WS, "s1", 1).open("rb")
        threading.Timer(0.3, reader.close).start()
        started = time.monotonic()
        store.erase(WS, "s1")
        assert entries(store) == []
        assert time.monotonic() - started < 5

    def test_replace_waits_for_a_reader_too(self, make):
        store = make()
        upload(store, b"old")
        reader = store.path_for(WS, "s1", 1).open("rb")
        threading.Timer(0.3, reader.close).start()
        upload(store, b"new")
        assert store.path_for(WS, "s1", 1).read_bytes() == b"new"

    def test_erase_does_not_block_the_store_while_it_waits_for_a_reader(self, make):
        store = make(lock_wait_seconds=1.5)
        upload(store, b"content")
        reader = store.path_for(WS, "s1", 1).open("rb")
        outcome = []

        def erase():
            try:
                store.erase(WS, "s1")
            except PermissionError as error:
                outcome.append(error)

        eraser = threading.Thread(target=erase)
        eraser.start()
        try:
            time.sleep(0.2)  # on Windows the eraser is now waiting for the reader
            started = time.monotonic()
            store.create_upload_session(WS, OWNER, "s2", "b.pdf", 5)
            store.erase(WS, "s3")
            assert time.monotonic() - started < 0.5
        finally:
            reader.close()
            eraser.join()
        store.erase(WS, "s1")
        assert entries(store) == []

    def test_erase_between_claim_and_folder_creates_nothing(self, make):
        store = make()
        session = store.create_upload_session(WS, OWNER, "s1", "a.pdf", 4)
        seen = []
        store._lock = FireAt(
            store._lock, 2, lambda: (store.erase(WS, "s1"), seen.append(entries(store)))
        )
        with pytest.raises(UploadRejected) as error, store.open_upload(session.token):
            pytest.fail("the body must not be read for an erased source")
        assert error.value.code == "cancelled"
        assert seen == [[]]
        assert entries(store) == []

    def test_erase_after_the_body_but_before_the_rename_leaves_nothing(self, make):
        store = make()
        session = store.create_upload_session(WS, OWNER, "s1", "a.pdf", 4)
        with pytest.raises(UploadRejected) as error, store.open_upload(session.token) as writer:
            writer.write(b"done")
            store._lock = FireAt(store._lock, 1, lambda: store.erase(WS, "s1"))
        assert error.value.code == "cancelled"
        assert entries(store) == []

    def test_the_session_never_prints_its_token_or_file_name(self, make):
        session = make().create_upload_session(WS, OWNER, "s1", "Secret-Name.pdf", 5)
        for text in (repr(session), str(session), f"{session}"):
            assert "Secret-Name" not in text
            assert session.token not in text

    def test_adopt_and_new_links_are_refused_for_an_erased_source(self, make, tmp_path):
        store = make()
        src = tmp_path / "note.txt"
        src.write_bytes(b"late file")
        store.erase(WS, "s1")
        with pytest.raises(UploadRejected) as error:
            store.adopt(WS, "s1", 2, src)
        assert error.value.code == "erased"
        with pytest.raises(UploadRejected) as error:
            store.create_upload_session(WS, OWNER, "s1", "a.pdf", 5)
        assert error.value.code == "erased"
        assert entries(store) == []
        assert store.adopt(WS, "s2", 1, src).read_bytes() == b"late file"  # others are fine

    def test_erase_during_adopt_leaves_nothing(self, make, tmp_path):
        store = make()
        src = tmp_path / "note.txt"
        src.write_bytes(b"late file")
        store._lock = FireAt(store._lock, 2, lambda: store.erase(WS, "s1"))
        with pytest.raises(UploadRejected) as error:
            store.adopt(WS, "s1", 1, src)
        assert error.value.code == "erased"
        assert entries(store) == []
        assert src.read_bytes() == b"late file"

    def test_a_failed_adopt_leaves_nothing(self, make, tmp_path, monkeypatch):
        store = make()
        src = tmp_path / "note.txt"
        src.write_bytes(b"data")
        monkeypatch.setattr(
            "nodo_api.adapters.local.source_blobs.os.fsync",
            lambda fd: (_ for _ in ()).throw(OSError(errno.EIO, "io")),
        )
        with pytest.raises(OSError):
            store.adopt(WS, "s1", 1, src)
        monkeypatch.undo()
        assert entries(store) == []

    def test_a_full_disk_in_the_middle_of_a_stream_is_a_low_disk_refusal(self, make, monkeypatch):
        store = make()
        session = store.create_upload_session(WS, OWNER, "s1", "a.pdf", 8)
        with pytest.raises(UploadRejected) as error, store.open_upload(session.token) as writer:
            real = writer._handle

            class Full:
                write = staticmethod(disk_full)
                close = staticmethod(real.close)

            writer._handle = Full()
            writer.write(b"abcd")
        assert error.value.code == "low_disk"
        assert entries(store) == []

    def test_a_full_disk_at_the_final_flush_is_a_low_disk_refusal(
        self, make, tmp_path, monkeypatch
    ):
        store = make()
        session = store.create_upload_session(WS, OWNER, "s1", "a.pdf", 4)
        monkeypatch.setattr("nodo_api.adapters.local.source_blobs.os.fsync", disk_full)
        with pytest.raises(UploadRejected) as error, store.open_upload(session.token) as writer:
            writer.write(b"abcd")
        assert error.value.code == "low_disk"
        src = tmp_path / "note.txt"
        src.write_bytes(b"data")
        with pytest.raises(UploadRejected) as error:
            store.adopt(WS, "s2", 1, src)
        assert error.value.code == "low_disk"
        monkeypatch.undo()
        assert entries(store) == []

    def test_paths_that_would_pass_the_windows_limit_are_refused(self, make):
        store = make()
        with pytest.raises(ValueError, match=r"^Unsafe path length$"):
            store.path_for("w" * 128, "s" * 128, 1)
        with pytest.raises(ValueError, match=r"^Unsafe path length$"):
            store.create_upload_session("w" * 128, OWNER, "s" * 128, "a.pdf", 5)
        assert len(str(store.path_for(str(uuid.uuid4()), str(uuid.uuid4()), 999_999))) < 240
        assert entries(store) == []

    def test_unclaimed_links_expire_and_the_map_is_capped(self, make):
        clock = [datetime(2026, 11, 13, tzinfo=UTC)]
        store = make(now=lambda: clock[0], upload_seconds=60, max_links=2)
        store.create_upload_session(WS, OWNER, "s1", "a.pdf", 3)
        store.create_upload_session(WS, OWNER, "s2", "a.pdf", 3)
        with pytest.raises(UploadRejected) as error:
            store.create_upload_session(WS, OWNER, "s3", "a.pdf", 3)
        assert error.value.code == "busy"
        clock[0] += timedelta(seconds=61)
        store.create_upload_session(WS, OWNER, "s3", "a.pdf", 3)  # the two old ones were purged
        assert len(store._links) == 1

    def test_a_claimed_link_is_not_purged_while_its_body_is_still_arriving(self, make):
        clock = [datetime(2026, 11, 13, tzinfo=UTC)]
        store = make(now=lambda: clock[0], upload_seconds=60)
        session = store.create_upload_session(WS, OWNER, "s1", "a.pdf", 6)
        with store.open_upload(session.token) as writer:
            writer.write(b"abc")
            clock[0] += timedelta(seconds=120)
            store.create_upload_session(WS, OWNER, "s2", "a.pdf", 3)  # runs the purge
            writer.write(b"def")
        assert store.path_for(WS, "s1", 1).read_bytes() == b"abcdef"

    def test_nothing_with_a_name_reaches_errors_or_logs(self, make, caplog):
        store = make(max_bytes=10)
        secret = "Payroll-Q3-SECRET"
        caplog.set_level(logging.DEBUG)
        refused = [
            lambda: store.create_upload_session(WS, OWNER, "s1", secret + ":x.pdf", 5),
            lambda: store.create_upload_session(WS, OWNER, "s1", secret + ".pdf", 11),
            lambda: store.create_upload_session(WS, OWNER, "s1", secret + "." * 3, 5),
            lambda: store.create_upload_session(WS, OWNER, "s1", secret + "x" * 300, 5),
        ]
        for call in refused:
            with pytest.raises((ValueError, UploadRejected)) as error:
                call()
            assert secret not in str(error.value) + repr(error.value)
        session = store.create_upload_session(WS, OWNER, "s1", secret + ".pdf", 5)
        with pytest.raises(UploadRejected) as error, store.open_upload(session.token) as writer:
            writer.write(b"toolong")
            writer.write(b"toolong")
        assert secret not in str(error.value) + repr(error.value)
        assert secret not in "".join(files(store))
        assert caplog.records == []


class TestLocalSourceBlobs(SourceBlobContract):
    @pytest.fixture
    def make(self, tmp_path):
        def build(**limits):
            limits.setdefault("max_bytes", 200 * MIB)
            return LocalSourceBlobStore(tmp_path / "sources", **limits)

        return build

    def test_start_sweeps_old_part_files_and_nothing_else(self, tmp_path):
        root = tmp_path / "sources"
        old, fresh = root / WS / "s1" / "1", root / WS / "s2" / "1"
        keep = root / WS / "s3" / "1"
        for folder in (old, fresh, keep):
            folder.mkdir(parents=True)
        (old / "original.aaaa.part").write_bytes(b"crashed")
        (fresh / "original.bbbb.part").write_bytes(b"maybe running")
        (keep / "original").write_bytes(b"published")
        long_ago = time.time() - 7200
        os.utime(old / "original.aaaa.part", (long_ago, long_ago))
        os.utime(keep / "original", (long_ago, long_ago))
        LocalSourceBlobStore(root, stale_part_seconds=3600)
        assert files_in(root) == [f"{WS}/s2/1/original.bbbb.part", f"{WS}/s3/1/original"]
        assert not (root / WS / "s1").exists()  # its empty folders went too


def files_in(root) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


class TestMemorySourceBlobs(SourceBlobContract):
    @pytest.fixture
    def make(self):
        stores = []

        def build(**limits):
            limits.setdefault("max_bytes", 200 * MIB)
            stores.append(MemorySourceBlobStore(**limits))
            return stores[-1]

        yield build
        for store in stores:
            store.close()
            store.close()  # twice is fine
            assert not store.root.exists()
