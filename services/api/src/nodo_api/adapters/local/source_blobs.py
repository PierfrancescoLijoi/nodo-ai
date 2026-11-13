"""Original files of documents, on the local disk (Task 6).

Layout: <root>/<workspace>/<source>/<version>/original. The client file name is metadata and never
a path part; ids are checked against a short allow-list before they touch a path.

A file becomes readable only by one atomic step: the body is written to a random `.part` file in
the same folder, flushed and synced, then renamed over `original` with os.replace. The size limit
is enforced while the body streams in, and the SHA-256 is computed on the same pass. Windows will
not delete or replace a file somebody has open, so those steps wait and retry for a short while.

Upload links live in memory: they last minutes, and a restart only means asking for a new one."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import secrets
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

from nodo_api.domain.source_files import (
    UploadRejected,
    UploadSession,
    check_id,
    check_version,
    safe_filename,
)
from nodo_api.errors import NotFoundError

__all__ = ["LocalSourceBlobStore", "SourceUpload", "safe_filename"]

ORIGINAL = "original"
BLOCK = 1024 * 1024
DEFAULT_MAX_BYTES = 200 * 1024 * 1024
DISK_FACTOR = 2  # room for the copy and for the parser's work files
MAX_LINKS = 10_000  # open upload links at once
MAX_PATH_CHARS = 240  # Windows MAX_PATH is 260; leave room for the folder a parser may add
PART_SUFFIX_CHARS = 22  # ".<16 hex>.part" in place of "original" (the name is longer by this)


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


@contextmanager
def _disk_full_is_a_refusal() -> Iterator[None]:
    """A full disk in the middle of a stream is the same refusal as too little room up front."""
    try:
        yield
    except OSError as error:
        if error.errno == errno.ENOSPC or getattr(error, "winerror", None) == 112:
            raise UploadRejected("low_disk") from None
        raise


def _retry_while_locked(action: Callable[[], None], wait_seconds: float) -> None:
    """Run `action`; on Windows a file in use raises PermissionError, so try again for a while."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            return action()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def size_and_sha256(path: Path) -> tuple[int, str]:
    digest, size = hashlib.sha256(), 0
    with Path(path).open("rb") as handle:
        while block := handle.read(BLOCK):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


class SourceUpload:
    """The open `.part` file of one upload. Written chunk by chunk; the cap holds on every chunk."""

    def __init__(self, session: UploadSession, part: Path) -> None:
        self.session = session
        self.part = part
        self.size = 0
        self._digest = hashlib.sha256()
        self._lock = threading.Lock()  # write() runs in a request thread, abort() in another
        self._aborted = False
        self._handle: BinaryIO | None = part.open("xb")

    workspace_id = property(lambda self: self.session.workspace_id)
    owner_id = property(lambda self: self.session.owner_id)
    source_id = property(lambda self: self.session.source_id)
    version = property(lambda self: self.session.version)
    filename = property(lambda self: self.session.filename)

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()

    def write(self, chunk: bytes) -> None:
        with self._lock:
            handle = self._open_handle()
            added = memoryview(chunk).nbytes
            if self.size + added > self.session.size_bytes:
                raise UploadRejected("too_large")
            with _disk_full_is_a_refusal():
                handle.write(chunk)
            self._digest.update(chunk)
            self.size += added

    def finish(self) -> None:
        """Flush to disk and check the declared size; called once the body has ended."""
        with self._lock:
            handle = self._open_handle()
            if self.size != self.session.size_bytes:
                raise UploadRejected("size_mismatch")
            with _disk_full_is_a_refusal():
                handle.flush()
                os.fsync(handle.fileno())
            handle.close()
            self._handle = None

    def abort(self) -> None:
        """Close the file now and refuse any further write."""
        with self._lock:
            self._aborted = True
            if self._handle is not None:
                self._handle.close()
                self._handle = None

    def _open_handle(self) -> BinaryIO:
        if self._aborted or self._handle is None:
            raise UploadRejected("cancelled")
        return self._handle


@dataclass(slots=True)
class _Link:
    session: UploadSession
    claimed: bool = False


class LocalSourceBlobStore:
    def __init__(
        self,
        root: Path | str,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        upload_seconds: int = 900,
        free_space: Callable[[Path], int] = _free_bytes,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        lock_wait_seconds: float = 2.0,
        max_links: int = MAX_LINKS,
        stale_part_seconds: float = 3600.0,
    ) -> None:
        self.root = Path(root)
        self._max_bytes = max_bytes
        self._upload_seconds = upload_seconds
        self._free_space = free_space
        self._now = now
        self._lock_wait = lock_wait_seconds
        self._max_links = max_links
        # The lock orders bookkeeping (links, tombstones), folder and .part creation, pruning and
        # each rename attempt. It is never held while waiting for a file that is in use.
        self._lock = threading.RLock()
        self._links: dict[str, _Link] = {}
        self._running: dict[str, SourceUpload] = {}
        # Sources erased since this process started: nothing may be written for them again.
        # ponytail: in memory only, so the service re-checks the tombstone after a write (report).
        self._erased: set[tuple[str, str]] = set()
        self._sweep_stale_parts(stale_part_seconds)

    # paths -----------------------------------------------------------------------------------

    def _source_dir(self, workspace_id: str, source_id: str) -> Path:
        return self.root / check_id(workspace_id) / check_id(source_id)

    def path_for(self, workspace_id: str, source_id: str, version: int) -> Path:
        path = self._source_dir(workspace_id, source_id) / str(check_version(version)) / ORIGINAL
        if len(str(path)) + PART_SUFFIX_CHARS > MAX_PATH_CHARS:  # stays under Windows MAX_PATH
            raise ValueError("Unsafe path length")
        return path

    def _sweep_stale_parts(self, older_than_seconds: float) -> None:
        """Remove `.part` files a killed process left behind. Runs at start, when no upload of
        this process exists; a young file is left alone."""
        if not self.root.is_dir():
            return
        cutoff = time.time() - older_than_seconds
        for part in list(self.root.glob("*/*/*/*.part")):
            try:
                if part.stat().st_mtime < cutoff:
                    part.unlink()
                    self._prune(part.parent)
            except OSError:
                continue

    # upload links ----------------------------------------------------------------------------

    def create_upload_session(
        self,
        workspace_id: str,
        owner_id: str,
        source_id: str,
        filename: str,
        size_bytes: int,
        *,
        version: int = 1,
    ) -> UploadSession:
        final = self.path_for(workspace_id, source_id, version)  # checks ids and version
        name = safe_filename(filename)
        if not isinstance(owner_id, str) or not owner_id:
            raise ValueError("Owner is required")
        if type(size_bytes) is not int or size_bytes < 1:
            raise ValueError("Size must be a positive number of bytes")
        if size_bytes > self._max_bytes:
            raise UploadRejected("too_large")
        self._require_room(final.parent, size_bytes)
        now = self._now()
        session = UploadSession(
            token=secrets.token_urlsafe(32),
            workspace_id=workspace_id,
            owner_id=owner_id,
            source_id=source_id,
            version=version,
            filename=name,
            size_bytes=size_bytes,
            expires_at=now + timedelta(seconds=self._upload_seconds),
        )
        with self._lock:
            if (workspace_id, source_id) in self._erased:
                raise UploadRejected("erased")
            self._purge_links(now)
            if len(self._links) >= self._max_links:
                raise UploadRejected("busy")
            self._links[session.token] = _Link(session)
        return session

    def _purge_links(self, now: datetime) -> None:
        """Forget links nobody claimed in time (a claimed one lives until its body ends)."""
        self._links = {
            t: link
            for t, link in self._links.items()
            if link.claimed or link.session.expires_at > now
        }

    def _require_room(self, folder: Path, size: int) -> None:
        probe = folder
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if self._free_space(probe) < size * DISK_FACTOR:
            raise UploadRejected("low_disk")

    @contextmanager
    def open_upload(self, token: str) -> Iterator[SourceUpload]:
        """Claim the link and give a writer for the body. The original appears only if the body
        ends with exactly the declared size; any failure removes the partial file and frees the
        link for another try."""
        now = self._now()
        with self._lock:
            self._purge_links(now)
            link = self._links.get(token)
            if link is None or link.claimed or link.session.expires_at <= now:
                raise UploadRejected("unknown_token")
            link.claimed = True
        session = link.session
        folder = self.path_for(session.workspace_id, session.source_id, session.version).parent
        part = folder / f"{ORIGINAL}.{secrets.token_hex(8)}.part"
        upload: SourceUpload | None = None

        def still_open() -> None:  # called with the lock held
            if self._links.get(token) is not link:
                raise UploadRejected("cancelled")  # the source was erased meanwhile

        try:
            with self._lock:
                still_open()  # before anything is created on disk
                folder.mkdir(parents=True, exist_ok=True)
                upload = SourceUpload(session, part)
                self._running[token] = upload
            yield upload
            upload.finish()
            self._publish(part, folder / ORIGINAL, still_open, lambda: self._finish_link(token))
            _sync_folder(folder)
        except BaseException:
            if upload is not None:
                upload.abort()
            self._discard(part)
            with self._lock:
                self._running.pop(token, None)
                self._prune(folder)
                if self._links.get(token) is link:
                    link.claimed = False
            raise

    def _finish_link(self, token: str) -> None:
        self._links.pop(token, None)
        self._running.pop(token, None)

    # files -----------------------------------------------------------------------------------

    def adopt(self, workspace_id: str, source_id: str, version: int, src: Path) -> Path:
        """Copy a watched file into the store (the original stays where it is). The copy follows
        the same rules as an upload: size cap while copying, room on disk, atomic rename. It is
        refused for a source erased in this process, also when the erase comes during the copy."""
        final = self.path_for(workspace_id, source_id, version)
        key = (workspace_id, source_id)

        def not_erased() -> None:  # called with the lock held
            if key in self._erased:
                raise UploadRejected("erased")

        src = Path(src)
        if not src.is_file():
            raise NotFoundError
        size = src.stat().st_size
        if size > self._max_bytes:
            raise UploadRejected("too_large")
        self._require_room(final.parent, size)
        part = final.parent / f"{ORIGINAL}.{secrets.token_hex(8)}.part"
        try:
            with self._lock:  # folder and .part together, so a prune cannot slip in between
                not_erased()
                final.parent.mkdir(parents=True, exist_ok=True)
                writer = part.open("xb")
            with writer, src.open("rb") as reader, _disk_full_is_a_refusal():
                copied = 0
                while block := reader.read(BLOCK):
                    copied += len(block)
                    if copied > self._max_bytes:  # the file kept growing while it was copied
                        raise UploadRejected("too_large")
                    writer.write(block)
                writer.flush()
                os.fsync(writer.fileno())
            self._publish(part, final, not_erased)
            _sync_folder(final.parent)
        except BaseException:
            self._discard(part)
            with self._lock:
                self._prune(final.parent)
            raise
        return final

    def size_and_sha256(self, path: Path) -> tuple[int, str]:
        return size_and_sha256(path)

    def erase(self, workspace_id: str, source_id: str) -> None:
        """Remove every version, leftover and open link of a source, and refuse new writes for it.
        Safe to repeat. A reader that still holds a file open is waited for, without blocking the
        store; if it never lets go the OSError is raised, so the caller knows the erase is not
        complete and can try again."""
        folder = self._source_dir(workspace_id, source_id)
        with self._lock:  # bookkeeping only
            self._erased.add((workspace_id, source_id))
            for token in [
                t
                for t, link in self._links.items()
                if (link.session.workspace_id, link.session.source_id) == (workspace_id, source_id)
            ]:
                del self._links[token]
                running = self._running.pop(token, None)
                if running is not None:
                    running.abort()  # close its handle so the folder can go
        _retry_while_locked(lambda: _remove_tree(folder), self._lock_wait)
        with self._lock:
            self._prune(folder)

    # helpers ---------------------------------------------------------------------------------

    def _publish(
        self,
        part: Path,
        final: Path,
        check: Callable[[], None],
        done: Callable[[], None] | None = None,
    ) -> None:
        """Rename the finished .part over the original. `check` (raises to refuse) and the rename
        run together under the lock, one attempt at a time, so a file in use makes us wait
        without holding the lock."""
        deadline = time.monotonic() + self._lock_wait
        while True:
            with self._lock:
                check()
                try:
                    os.replace(part, final)
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                else:
                    if done is not None:
                        done()
                    return
            time.sleep(0.05)

    def _discard(self, part: Path) -> None:
        with contextlib.suppress(OSError):
            _retry_while_locked(lambda: part.unlink(missing_ok=True), self._lock_wait)

    def _prune(self, folder: Path) -> None:
        """Remove `folder` and its parents while they are empty, never the root itself."""
        for current in (folder, *folder.parents):
            if current == self.root or self.root not in current.parents:
                return
            try:
                current.rmdir()
            except FileNotFoundError:
                continue  # already gone: keep going up
            except OSError:
                return  # not empty


def _remove_tree(folder: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        shutil.rmtree(folder)


def _sync_folder(folder: Path) -> None:
    """Make the rename durable. Windows cannot open a folder this way, so it is skipped there."""
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
