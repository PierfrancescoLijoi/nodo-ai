"""A request for a copy of one's data (ADR-019)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

EXPORT_TTL_HOURS = 24
FORMAT = "nodo-export-v1"


class ExportState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Export:
    id: str
    workspace_id: str
    owner_id: str
    meeting_id: str | None
    state: ExportState
    created_at: datetime
    expires_at: datetime
    blob_name: str | None = None
    size_bytes: int | None = None
