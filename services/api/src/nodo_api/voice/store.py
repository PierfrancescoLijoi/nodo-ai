"""Shared state of the voice gateway: who may open which session, who holds it, how far it got.

Raw audio never goes through here. What is stored is small and expires on its own: the session
record, a lease (one gateway replica owns a session at a time), the acknowledged audio offset, the
finalized transcript phrases (so any replica can finish a session another one started) and the
admission counters. `MemorySessionStore` is the single-process and test implementation;
`RedisSessionStore` is the one that lets several replicas cooperate.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

MAX_FINALS = 5000  # a bound, not a target: about 20 hours of continuous speech


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    workspace_id: str
    user_id: str
    mode: str  # "meeting" or "assistant"
    locale: str
    meeting_id: str | None = None
    audio_ack_ms: int = 0
    last_sequence: int = 0
    closed: bool = False


@dataclass(frozen=True, slots=True)
class Limits:
    total: int
    per_workspace: int
    per_user: int


class Admission(StrEnum):
    OK = "ok"
    GLOBAL_FULL = "global_full"
    WORKSPACE_FULL = "workspace_full"
    USER_FULL = "user_full"


class SessionStore(Protocol):
    async def create(self, record: SessionRecord, ttl_seconds: int) -> None: ...

    async def get(self, session_id: str) -> SessionRecord | None: ...

    async def progress(self, session_id: str, audio_ack_ms: int, last_sequence: int) -> None:
        """Both values only ever grow, whichever replica reports them last."""
        ...

    async def close(self, session_id: str) -> None: ...

    async def consume_ticket(self, jti: str, ttl_seconds: int) -> bool:
        """True the first time, False for a ticket that was already used."""
        ...

    async def acquire_lease(self, session_id: str, owner: str, ttl_ms: int) -> bool:
        """Take the session, or renew it when `owner` already holds it."""
        ...

    async def release_lease(self, session_id: str, owner: str) -> None: ...

    async def admit(
        self,
        workspace_id: str,
        user_id: str,
        session_id: str,
        limits: Limits,
        ttl_seconds: int,
    ) -> Admission:
        """Count the session against the limits; an id that is already counted is refreshed."""
        ...

    async def leave(self, workspace_id: str, user_id: str, session_id: str) -> None: ...

    async def put_final(self, session_id: str, start_ms: int, payload: str) -> None:
        """Idempotent per start offset, so a resent stretch of audio can not duplicate a phrase."""
        ...

    async def finals(self, session_id: str) -> list[str]:
        """The stored phrases, ordered by start offset."""
        ...

    async def forget(self, session_id: str) -> None:
        """Drop the finals once the transcript has been handed over."""
        ...


class MemorySessionStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._sessions: dict[str, SessionRecord] = {}
        self._tickets: dict[str, float] = {}
        self._leases: dict[str, tuple[str, float]] = {}
        self._active: dict[str, float] = {}
        self._owner_of: dict[str, tuple[str, str]] = {}  # session -> (workspace, user)
        self._finals: dict[str, dict[int, str]] = {}

    async def create(self, record: SessionRecord, ttl_seconds: int) -> None:
        self._sessions[record.session_id] = record

    async def get(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    async def progress(self, session_id: str, audio_ack_ms: int, last_sequence: int) -> None:
        record = self._sessions.get(session_id)
        if record is not None:
            self._sessions[session_id] = replace(
                record,
                audio_ack_ms=max(record.audio_ack_ms, audio_ack_ms),
                last_sequence=max(record.last_sequence, last_sequence),
            )

    async def close(self, session_id: str) -> None:
        record = self._sessions.get(session_id)
        if record is not None:
            self._sessions[session_id] = replace(record, closed=True)

    async def consume_ticket(self, jti: str, ttl_seconds: int) -> bool:
        now = self._clock()
        self._tickets = {key: until for key, until in self._tickets.items() if until > now}
        if jti in self._tickets:
            return False
        self._tickets[jti] = now + ttl_seconds
        return True

    async def acquire_lease(self, session_id: str, owner: str, ttl_ms: int) -> bool:
        now = self._clock()
        held = self._leases.get(session_id)
        if held is not None and held[1] > now and held[0] != owner:
            return False
        self._leases[session_id] = (owner, now + ttl_ms / 1000)
        return True

    async def release_lease(self, session_id: str, owner: str) -> None:
        held = self._leases.get(session_id)
        if held is not None and held[0] == owner:
            del self._leases[session_id]

    async def admit(
        self,
        workspace_id: str,
        user_id: str,
        session_id: str,
        limits: Limits,
        ttl_seconds: int,
    ) -> Admission:
        now = self._clock()
        self._active = {key: until for key, until in self._active.items() if until > now}
        if session_id not in self._active:
            if len(self._active) >= limits.total:
                return Admission.GLOBAL_FULL
            owners = [self._owner_of.get(key) for key in self._active]
            in_workspace = sum(1 for owner in owners if owner and owner[0] == workspace_id)
            if in_workspace >= limits.per_workspace:
                return Admission.WORKSPACE_FULL
            if sum(1 for owner in owners if owner == (workspace_id, user_id)) >= limits.per_user:
                return Admission.USER_FULL
        self._active[session_id] = now + ttl_seconds
        self._owner_of[session_id] = (workspace_id, user_id)
        return Admission.OK

    async def leave(self, workspace_id: str, user_id: str, session_id: str) -> None:
        self._active.pop(session_id, None)
        self._owner_of.pop(session_id, None)

    async def put_final(self, session_id: str, start_ms: int, payload: str) -> None:
        phrases = self._finals.setdefault(session_id, {})
        if start_ms in phrases or len(phrases) < MAX_FINALS:
            phrases[start_ms] = payload

    async def finals(self, session_id: str) -> list[str]:
        phrases = self._finals.get(session_id, {})
        return [phrases[start] for start in sorted(phrases)]

    async def forget(self, session_id: str) -> None:
        self._finals.pop(session_id, None)
