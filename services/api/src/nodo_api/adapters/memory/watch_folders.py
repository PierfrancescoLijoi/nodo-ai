from __future__ import annotations

import threading
from dataclasses import replace

from nodo_api.domain.sources import WatchFile, WatchFolder
from nodo_api.errors import NotFoundError


class MemoryWatchFolderRepository:
    def __init__(self) -> None:
        self._folders: dict[str, WatchFolder] = {}
        self._files: dict[tuple[str, str], WatchFile] = {}
        self._lock = threading.RLock()

    def add(self, folder: WatchFolder) -> WatchFolder:
        with self._lock:
            if folder.id in self._folders or any(
                (f.workspace_id, f.owner_id, f.path)
                == (folder.workspace_id, folder.owner_id, folder.path)
                for f in self._folders.values()
            ):
                raise ValueError("Watch folder already exists")
            self._folders[folder.id] = folder
        return folder

    def list_owned(self, workspace_id: str, owner_id: str) -> tuple[WatchFolder, ...]:
        with self._lock:
            owned = [
                f
                for f in self._folders.values()
                if (f.workspace_id, f.owner_id) == (workspace_id, owner_id)
            ]
        return tuple(sorted(owned, key=lambda f: (f.created_at, f.id)))

    def list_enabled(self) -> tuple[WatchFolder, ...]:
        with self._lock:
            enabled = [f for f in self._folders.values() if f.enabled]
        return tuple(sorted(enabled, key=lambda f: (f.created_at, f.id)))

    def delete_owned(self, workspace_id: str, owner_id: str, folder_id: str) -> None:
        with self._lock:
            folder = self._folders.get(folder_id)
            if folder is None or (folder.workspace_id, folder.owner_id) != (workspace_id, owner_id):
                raise NotFoundError
            del self._folders[folder_id]
            self._files = {k: v for k, v in self._files.items() if k[0] != folder_id}

    def observe_file(self, folder_id: str, rel_path: str, size: int, mtime_ns: int) -> WatchFile:
        with self._lock:
            if folder_id not in self._folders:
                raise NotFoundError  # deleted during the scan (SQLite: the foreign key)
            old = self._files.get((folder_id, rel_path))
            if old is None:
                item = WatchFile(folder_id, rel_path, size, mtime_ns)
            else:
                same = (old.size, old.mtime_ns) == (size, mtime_ns)
                item = replace(
                    old,
                    size=size,
                    mtime_ns=mtime_ns,
                    seen_stable_count=old.seen_stable_count + 1 if same else 1,
                )
            self._files[(folder_id, rel_path)] = item
            return item

    def get_file(self, folder_id: str, rel_path: str) -> WatchFile | None:
        with self._lock:
            return self._files.get((folder_id, rel_path))

    def mark_imported(
        self,
        folder_id: str,
        rel_path: str,
        sha256: str,
        source_id: str | None,
        linked_only: bool = False,
    ) -> None:
        with self._lock:
            old = self._files.get((folder_id, rel_path))
            if old is not None:
                self._files[(folder_id, rel_path)] = replace(
                    old, sha256=sha256, source_id=source_id, linked_only=linked_only
                )

    def unlink_source(self, source_id: str) -> None:
        with self._lock:
            for key, item in self._files.items():
                if item.source_id == source_id:
                    self._files[key] = replace(item, source_id=None, linked_only=False)

    def prune(self, folder_id: str, keep: frozenset[str]) -> None:
        with self._lock:
            self._files = {
                k: v for k, v in self._files.items() if k[0] != folder_id or k[1] in keep
            }

    def reset_stable(self, folder_id: str, rel_path: str) -> None:
        with self._lock:
            old = self._files.get((folder_id, rel_path))
            if old is not None:
                self._files[(folder_id, rel_path)] = replace(old, seen_stable_count=1)
