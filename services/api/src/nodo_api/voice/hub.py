"""The sessions this gateway replica hosts, and the rules for taking one more."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from nodo_api.voice.live import Busy, Connection, Deps, LiveSession
from nodo_api.voice.store import Admission, SessionRecord
from nodo_api.voice.tickets import TicketSigner

logger = logging.getLogger("nodo_api.voice")


@dataclass(slots=True)
class VoiceRuntime:
    deps: Deps
    signer: TicketSigner
    hub: SessionHub

    async def shutdown(self) -> None:
        await self.hub.drain()
        close = getattr(self.deps.store, "aclose", None)
        if close is not None:
            await close()


class SessionHub:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps
        self._sessions: dict[str, LiveSession] = {}
        self._attaching: dict[str, asyncio.Lock] = {}
        self._pending_hello = 0
        self.draining = False

    @property
    def replica_id(self) -> str:
        return self._deps.replica_id

    @property
    def active(self) -> int:
        return len(self._sessions)

    def get(self, session_id: str) -> LiveSession | None:
        return self._sessions.get(session_id)

    async def admit(self, record: SessionRecord) -> None:
        """Count a new session against the limits shared by every replica."""
        if self.draining:
            raise Busy("draining", retry_after=5)
        settings = self._deps.settings
        verdict = await self._deps.store.admit(
            record.workspace_id,
            record.user_id,
            record.session_id,
            settings.limits,
            settings.admission_ttl_seconds,
        )
        if verdict is not Admission.OK:
            raise Busy(str(verdict), retry_after=5)

    async def attach(self, record: SessionRecord, conn: Connection, last_ack: int) -> LiveSession:
        """Connect a socket to its session here: new, resumed on this replica, or taken over
        from another one once that replica's lease has lapsed."""
        if self.draining:
            raise Busy("draining", retry_after=1)
        # One attach at a time per session: two tickets of the same person must not build
        # two sessions around the same stream.
        lock = self._attaching.setdefault(record.session_id, asyncio.Lock())
        async with lock:
            live = await self._session_for(record)
            await live.attach(conn, last_ack)
            return live

    async def _session_for(self, record: SessionRecord) -> LiveSession:
        settings = self._deps.settings
        live = self._sessions.get(record.session_id)
        if live is not None:
            return live
        if len(self._sessions) >= settings.max_sessions_per_replica:
            raise Busy("replica_full", retry_after=2)
        if not await self._deps.store.acquire_lease(
            record.session_id, self._deps.replica_id, settings.lease_ms
        ):
            raise Busy("lease_busy", retry_after=1)
        fresh = await self._deps.store.get(record.session_id) or record
        live = LiveSession(fresh, self._deps, self._forget)
        self._sessions[record.session_id] = live
        return live

    def begin_hello(self) -> bool:
        """Room for one more socket that has not shown a ticket yet."""
        if self._pending_hello >= self._deps.settings.max_pending_hello:
            return False
        self._pending_hello += 1
        return True

    def end_hello(self) -> None:
        self._pending_hello = max(0, self._pending_hello - 1)

    def _forget(self, live: LiveSession) -> None:
        session_id = live.record.session_id
        if self._sessions.get(session_id) is live:  # never the session that replaced it
            del self._sessions[session_id]
            lock = self._attaching.get(session_id)
            if lock is not None and not lock.locked():
                del self._attaching[session_id]

    async def drain(self) -> None:
        """Stop admission, flush every session and ask the clients to reconnect elsewhere."""
        self.draining = True
        sessions = list(self._sessions.values())
        if sessions:
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(
                        *(s.suspend("replica_draining") for s in sessions), return_exceptions=True
                    ),
                    timeout=self._deps.settings.drain_seconds,
                )
                for failure in (r for r in results if isinstance(r, BaseException)):
                    logger.error("voice_suspend_failed", exc_info=failure)
            except TimeoutError:
                # Their leases lapse by themselves; the clients wait for that.
                logger.warning("voice_drain_timeout pending=%d", len(self._sessions))


def new_session_id() -> str:
    return uuid.uuid4().hex
