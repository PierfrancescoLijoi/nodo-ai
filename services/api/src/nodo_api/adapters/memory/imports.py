"""Import jobs in memory, for tests and the demo backend. Same behaviour as the SQLite store."""

from __future__ import annotations

import threading
from collections.abc import Collection
from dataclasses import replace
from datetime import UTC, datetime

from nodo_api.adapters.memory.sources import MemorySourceRepository
from nodo_api.domain.sources import CLEAR, Clear, ImportJob, SourceKind, check_job_update
from nodo_api.errors import ConflictError, NotFoundError


class MemoryImportJobRepository:
    def __init__(self, sources: MemorySourceRepository) -> None:
        self._sources = sources
        self._jobs: dict[tuple[str, str], ImportJob] = {}
        self._lock = threading.RLock()  # each change reads a job and writes it back

    def create(self, workspace_id: str, owner_id: str, source_id: str) -> ImportJob:
        key = (workspace_id, source_id)
        with self._lock:
            self._create(key, owner_id)
        return self.get_owned(workspace_id, owner_id, source_id)

    def _create(self, key: tuple[str, str], owner_id: str) -> None:
        workspace_id, source_id = key
        if key not in self._jobs:
            if self._sources.kind_of(workspace_id, source_id) in (None, SourceKind.MEETING):
                raise NotFoundError
            live = self._sources.get_for_processing(workspace_id, source_id)  # not deleted
            if live.owner_id != owner_id:
                raise NotFoundError
            now = datetime.now(UTC)
            self._jobs[key] = ImportJob(
                workspace_id, owner_id, source_id, "queued", None, 0, 0, 0, None, False, now, now
            )

    def get_owned(self, workspace_id: str, user_id: str, source_id: str) -> ImportJob:
        job = self._jobs.get((workspace_id, source_id))
        if job is None or job.owner_id != user_id:
            raise NotFoundError
        return job

    def get_for_processing(self, workspace_id: str, source_id: str) -> ImportJob:
        job = self._jobs.get((workspace_id, source_id))
        if job is None:
            raise NotFoundError
        return job

    def update(
        self,
        workspace_id: str,
        source_id: str,
        *,
        state: str | None = None,
        step: str | Clear | None = None,
        done: int | None = None,
        total: int | None = None,
        attempt: int | None = None,
        error_code: str | Clear | None = None,
        cancel_requested: bool | None = None,
        expected_state: str | Collection[str] | None = None,
    ) -> ImportJob:
        wanted = check_job_update(state, step, (done, total, attempt), error_code, expected_state)
        # None leaves a field as it is; CLEAR sets step or error_code to None.
        changes = {
            name: (None if value is CLEAR else value)
            for name, value in (
                ("state", state),
                ("step", step),
                ("done", done),
                ("total", total),
                ("attempt", attempt),
                ("error_code", error_code),
                ("cancel_requested", cancel_requested),
            )
            if value is not None
        }
        with self._lock:
            job = self.get_for_processing(workspace_id, source_id)
            if wanted is not None and job.state not in wanted:
                raise ConflictError
            job = replace(job, **changes, updated_at=datetime.now(UTC))
            self._jobs[(workspace_id, source_id)] = job
            return job

    def request_cancel(self, workspace_id: str, user_id: str, source_id: str) -> ImportJob:
        with self._lock:
            job = replace(
                self.get_owned(workspace_id, user_id, source_id),
                cancel_requested=True,
                updated_at=datetime.now(UTC),
            )
            self._jobs[(workspace_id, source_id)] = job
            return job

    def is_cancel_requested(self, workspace_id: str, source_id: str) -> bool:
        job = self._jobs.get((workspace_id, source_id))
        return job is not None and job.cancel_requested

    def delete(self, workspace_id: str, source_id: str) -> None:
        self._jobs.pop((workspace_id, source_id), None)
