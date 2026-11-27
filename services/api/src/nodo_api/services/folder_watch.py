"""Watched folders (FR-1): supported files in a folder the owner chose become Sources.

A poll, not a file system hook: `os.scandir` down to MAX_DEPTH folders and at most MAX_ENTRIES
entries per folder and scan. The listing gives only the attributes (links, placeholders, hidden
files): its size and time can lag behind a writer that keeps the file open (NTFS updates them on
close), so size and mtime come from `os.stat`. A file is imported after two scans in a row saw the
same size and mtime, and only if neither moved while it was hashed and copied and the copy has the
hashed bytes; otherwise the copy is dropped and the next scans try again.

Each watched path owns its lineage. A new file goes through the import service like an upload
(create, adopt, complete); when its bytes already belong to a live source (another watched file or
an upload) no duplicate is made and the path is recorded as linked to it. Only the path that
created a source reimports it when the file changes (the next version, recordings wait for the
owner's confirmation); a change at a linked path starts a new source for that path. The hash of
each handled file is kept, so a source the owner deleted comes back only when the content changes.
Rows of files that vanished are pruned after a scan that saw the whole folder.

Links never lead out of the folder: symbolic links, junctions and other name-surrogate reparse
points are refused at the root and skipped inside the walk, and the file handle is checked against
the checked stat before a copy. A OneDrive placeholder (recall flags, offline) is skipped from the
listing without being opened, which would download it. Roots are absolute folders on a local
fixed drive (no network share) that do not overlap the NODO data folder.

The folder path lives in the local database only and is answered to its owner; logs carry codes
and counts, never a path, a name or content."""

from __future__ import annotations

import logging
import os
import stat
import sys
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nodo_api.domain.source_files import UploadRejected
from nodo_api.domain.sources import WatchFolder
from nodo_api.errors import ConflictError, NotFoundError, ValidationFailed
from nodo_api.ports import WatchFolderRepository
from nodo_api.services.source_import import SUPPORTED_EXTENSIONS, source_id_for_key

logger = logging.getLogger("nodo_watch")

MAX_DEPTH = 5
MAX_ENTRIES = 5000
MAX_PATH_CHARS = 1024
_IGNORED_PREFIXES = ("~$", ".~lock", ".")  # Office owner files, LibreOffice locks, hidden names
_IGNORED_SUFFIXES = (".tmp", ".crdownload", ".part")
_LONG_PREFIX = "\\\\?\\"  # the Windows long-path form of a drive path
# Windows file attributes (st_file_attributes); 0 elsewhere.
HIDDEN = 0x2
SYSTEM = 0x4
REPARSE_POINT = 0x400
OFFLINE = 0x1000
RECALL_ON_OPEN = 0x40000
RECALL_ON_DATA_ACCESS = 0x400000
_SKIPPED = HIDDEN | SYSTEM | OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS
CLOUD_TAG = 0x9000001A  # IO_REPARSE_TAG_CLOUD and its 0x9000X01A variants (OneDrive files)
DRIVE_FIXED = 3


class Moved(Exception):
    """The file changed or was replaced while it was read: try again at a later scan."""


def _entry_info(entry: os.DirEntry[str]) -> os.stat_result:
    """The listing's own data on Windows (no open, so no hydration); lstat elsewhere."""
    return entry.stat(follow_symlinks=False)


def redirects(info: Any) -> bool:
    """A symbolic link, a junction or another reparse point that is not a cloud file. Cloud
    files (OneDrive) are reparse points too but never point elsewhere."""
    if stat.S_ISLNK(info.st_mode):
        return True
    if getattr(info, "st_file_attributes", 0) & REPARSE_POINT:
        return getattr(info, "st_reparse_tag", 0) & 0xFFFF0FFF != CLOUD_TAG
    return False


def skipped(info: Any) -> bool:
    """Never read: links, placeholders not on this computer, hidden and system entries."""
    return redirects(info) or bool(getattr(info, "st_file_attributes", 0) & _SKIPPED)


def _ignored_name(name: str) -> bool:
    lower = name.lower()
    return lower.startswith(_IGNORED_PREFIXES) or lower.endswith(_IGNORED_SUFFIXES)


def _same(a: Path, b: Path) -> bool:
    """The same folder: by file identity, or (one side missing) by the resolved path."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(str(a.resolve())).casefold() == (
            os.path.normcase(str(b.resolve())).casefold()
        )


def _local_fixed_drive(root: Path) -> bool:
    if sys.platform != "win32":
        return True
    import ctypes

    return ctypes.windll.kernel32.GetDriveTypeW(root.drive + "\\") == DRIVE_FIXED


def _identity(info: Any) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


class FolderWatcher:
    def __init__(
        self,
        repository: WatchFolderRepository,
        imports: Any,  # SourceImportService
        blobs: Any,  # SourceBlobStore
        data_folder: Path | str,
    ) -> None:
        self.repository = repository
        self._imports = imports
        self._blobs = blobs
        self._data = Path(data_folder)
        self._scanning = threading.Lock()  # one scan at a time

    # ------------------------------------------------------------------------------ folders

    def add_folder(
        self, workspace_id: str, owner_id: str, path: str, now: datetime | None = None
    ) -> WatchFolder:
        """Watch an absolute, existing folder on a local fixed drive that is not a link and does
        not overlap the NODO data folder (in either direction: the store never imports itself)."""
        root = self._checked_root(path)
        if root is None:
            raise ValidationFailed
        folder = WatchFolder(
            f"wf_{uuid.uuid4().hex}",
            workspace_id,
            owner_id,
            str(root),
            True,
            now or datetime.now(UTC),
        )
        try:
            return self.repository.add(folder)
        except ValueError:
            raise ConflictError from None  # this owner watches it already

    def list_folders(self, workspace_id: str, owner_id: str) -> tuple[WatchFolder, ...]:
        return self.repository.list_owned(workspace_id, owner_id)

    def delete_folder(self, workspace_id: str, owner_id: str, folder_id: str) -> None:
        """Stop watching. Sources already imported from it stay."""
        self.repository.delete_owned(workspace_id, owner_id, folder_id)

    def _checked_root(self, path: Any) -> Path | None:
        if not isinstance(path, str) or not path or len(path) > MAX_PATH_CHARS or "\x00" in path:
            return None
        if path.startswith(_LONG_PREFIX):
            path = path[len(_LONG_PREFIX) :]  # a drive path; UNC is refused just below
        if path.startswith(("\\\\", "//")) or path.upper().startswith("UNC\\"):
            return None  # network shares and devices: only folders on this computer
        root = Path(path)
        if not root.is_absolute() or not _local_fixed_drive(root):
            return None
        try:
            info = os.stat(root, follow_symlinks=False)
            resolved = root.resolve(strict=True)
            if not stat.S_ISDIR(info.st_mode) or redirects(info) or self._overlaps(resolved):
                return None
        except (OSError, RuntimeError, ValueError):
            return None
        return resolved

    def _overlaps(self, root: Path) -> bool:
        data = self._data.resolve()
        return any(_same(folder, data) for folder in (root, *root.parents)) or any(
            _same(folder, root) for folder in data.parents
        )

    # ------------------------------------------------------------------------------ scanning

    def run(self, stop: threading.Event, poll_seconds: float) -> None:
        """The `nodo-watch` thread: a scan every poll_seconds until `stop` is set."""
        while not stop.wait(poll_seconds):
            try:
                self.scan_once()
            except Exception as error:
                logger.error("watch_scan_failed type=%s", type(error).__name__)

    def scan_once(self, now: datetime | None = None) -> int:
        """Scan every enabled folder once; the number of imports started. A scan already running
        makes this one a no-op."""
        if not self._scanning.acquire(blocking=False):
            return 0
        try:
            started = 0
            for folder in self.repository.list_enabled():
                try:
                    started += self._scan(folder)
                except Exception as error:  # one broken folder never stops the others
                    logger.warning("watch_folder_failed type=%s", type(error).__name__)
            return started
        finally:
            self._scanning.release()

    def _scan(self, folder: WatchFolder) -> int:
        root = self._checked_root(folder.path)
        if root is None:  # gone, replaced by a link, or moved over the data folder
            logger.debug("watch_root_unavailable")
            return 0
        files, complete = self._walk(root)
        started = sum(self._consider(folder, rel, path) for rel, path in files)
        if complete:  # a limited or partly unreadable scan never forgets what it did not see
            self.repository.prune(folder.id, frozenset(rel for rel, _ in files))
        return started

    def _walk(self, root: Path) -> tuple[list[tuple[str, Path]], bool]:
        """Candidate files and whether the whole folder was seen."""
        files: list[tuple[str, Path]] = []
        complete = True
        budget = MAX_ENTRIES
        pending = [(root, 0)]
        while pending:
            folder, depth = pending.pop()
            try:
                with os.scandir(folder) as listing:
                    entries = sorted(listing, key=lambda e: e.name)
            except OSError:
                complete = False
                continue
            for entry in entries:
                if budget == 0:
                    logger.debug("watch_entry_limit count=%s", MAX_ENTRIES)
                    return files, False
                budget -= 1
                if _ignored_name(entry.name):
                    continue
                try:
                    info = _entry_info(entry)
                except OSError:
                    complete = False
                    continue
                if skipped(info):
                    continue
                path = Path(entry.path)
                if stat.S_ISDIR(info.st_mode):
                    if depth < MAX_DEPTH:
                        pending.append((path, depth + 1))
                    else:
                        complete = False
                elif stat.S_ISREG(info.st_mode) and path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    files.append((path.relative_to(root).as_posix(), path))
        return files, complete

    def _consider(self, folder: WatchFolder, rel: str, path: Path) -> int:
        """One file; an error here never stops the rest of the folder."""
        try:
            return self._handle(folder, rel, path)
        except Exception as error:
            try:
                self.repository.reset_stable(folder.id, rel)
            except Exception as reset_error:
                logger.debug("watch_reset_failed type=%s", type(reset_error).__name__)
            logger.warning("watch_file_failed type=%s", type(error).__name__)
            return 0

    def _handle(self, folder: WatchFolder, rel: str, path: Path) -> int:
        checked = os.stat(path, follow_symlinks=False)  # accurate while a writer holds it open
        if skipped(checked) or not stat.S_ISREG(checked.st_mode):
            return 0
        seen = self.repository.observe_file(folder.id, rel, checked.st_size, checked.st_mtime_ns)
        # Act on the second stable scan; later scans only when an earlier one ended unrecorded.
        if seen.seen_stable_count < 2 or (seen.seen_stable_count > 2 and seen.sha256 is not None):
            return 0
        ws, owner = folder.workspace_id, folder.owner_id
        digest = None
        try:
            _, digest = self._blobs.size_and_sha256(path)
            self._unmoved(path, checked)
            if digest == seen.sha256:
                return 0  # touched, not changed (also: a deleted Source stays deleted)
            if seen.source_id is not None and not seen.linked_only:
                try:  # this path created the source: the change is its next version
                    self._held(path, checked)
                    self._imports.reimport(ws, owner, seen.source_id, path, expected_sha256=digest)
                    self.repository.mark_imported(folder.id, rel, digest, seen.source_id)
                    return 1
                except NotFoundError:
                    pass  # the owner deleted it: these new bytes start a new source
            source_id, linked = self._import_new(folder, rel, path, checked, digest)
        except (ValidationFailed, NotFoundError) as error:
            if digest is not None:  # not importable as it is: only a change tries again
                self.repository.mark_imported(
                    folder.id, rel, digest, seen.source_id, seen.linked_only
                )
            logger.debug("watch_file_skipped code=%s", type(error).__name__)
            return 0
        except UploadRejected as error:
            if error.code == "too_large" and digest is not None:
                self.repository.mark_imported(
                    folder.id, rel, digest, seen.source_id, seen.linked_only
                )
            else:  # low disk, busy, erased meanwhile: the next scan tries again
                self.repository.reset_stable(folder.id, rel)
            logger.debug("watch_file_refused code=%s", error.code)
            return 0
        except (OSError, ConflictError, Moved) as error:
            # Locked, unreadable, changed while read, or an import of this source still
            # running: the next stable scan tries again.
            self.repository.reset_stable(folder.id, rel)
            logger.debug("watch_file_retry type=%s", type(error).__name__)
            return 0
        self.repository.mark_imported(folder.id, rel, digest, source_id, linked)
        return 0 if linked else 1  # linked: these bytes were imported already

    def _import_new(
        self, folder: WatchFolder, rel: str, path: Path, checked: Any, digest: str
    ) -> tuple[str, bool]:
        """create, adopt, complete: the upload path, so audio waits for confirmation, a twin is
        answered (no duplicate) and a tombstone is re-read after the copy. The key names this
        path's source for these bytes, so a scan after a crash finds it. Returns the source that
        holds the bytes and whether it belongs to someone else (linked)."""
        ws, owner = folder.workspace_id, folder.owner_id
        key = f"watch:{digest[:32]}:{folder.id}:{rel}"
        try:
            return self._create(ws, owner, path, checked, digest, key)
        except ConflictError:
            pass  # copied before a crash: complete it
        own = source_id_for_key(ws, owner, key)
        try:
            job = self._imports.complete(ws, owner, own)
            return job.source_id, job.source_id != own
        except NotFoundError:
            # That source is gone (a refused copy removed it): this file starts a fresh one.
            return self._create(ws, owner, path, checked, digest, None)

    def _create(
        self, ws: str, owner: str, path: Path, checked: Any, digest: str, key: str | None
    ) -> tuple[str, bool]:
        source, _ = self._imports.create(
            ws,
            owner,
            filename=path.name,
            size_bytes=checked.st_size,
            kind=None,
            origin_note=None,
            idempotency_key=key,
        )
        self._held(path, checked)
        stored = self._blobs.adopt(ws, source.id, 1, path)
        try:
            if self._blobs.size_and_sha256(stored)[1] != digest:
                raise Moved
            self._unmoved(path, checked)
        except BaseException:
            self._blobs.erase_originals(ws, source.id)  # the copy is not what was checked
            raise
        job = self._imports.complete(ws, owner, source.id)
        return job.source_id, job.source_id != source.id

    @staticmethod
    def _unmoved(path: Path, checked: Any) -> None:
        now = os.stat(path, follow_symlinks=False)
        if skipped(now) or _identity(now) != _identity(checked):
            raise Moved

    @staticmethod
    def _held(path: Path, checked: Any) -> None:
        """Open the file as the copy will and check the handle is the checked file: not a link
        or reparse point swapped in after the stat, same identity, size and time."""
        with path.open("rb") as handle:
            held = os.fstat(handle.fileno())
        if redirects(held) or _identity(held) != _identity(checked):
            raise Moved
