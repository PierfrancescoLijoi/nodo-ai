"""One live voice session: its state machine, replay log, audio accounting and assistant turns.

States follow the lifecycle of the architecture note: ACTIVE while a socket is attached,
RECONNECTING for the grace period after the socket is lost, DRAINING when this replica is being
stopped, FINALIZING while the transcript is handed over, then CLOSED. Everything that has to
outlive the replica (finals, acknowledged offset, lease) goes through the session store.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from nodo_api.voice.assistant import Assistant
from nodo_api.voice.protocol import (
    BYTES_PER_MS,
    AudioFrame,
    ProtocolError,
    encode_audio_out,
    server_event,
)
from nodo_api.voice.settings import VoiceSettings
from nodo_api.voice.speech import Recognition, StreamingRecognizer
from nodo_api.voice.store import SessionRecord, SessionStore

logger = logging.getLogger("nodo_api.voice")

REFILL = 2.0  # accepted audio per second of wall time: faster than real time, but bounded
CLOSE_BUSY, CLOSE_DRAINING, CLOSE_NORMAL, CLOSE_REPLACED = 1013, 1012, 1000, 4000


class SessionState(StrEnum):
    ACTIVE = "active"
    RECONNECTING = "reconnecting"
    DRAINING = "draining"
    FINALIZING = "finalizing"
    CLOSED = "closed"


class SessionClosed(RuntimeError):
    pass


class Busy(RuntimeError):
    """Try again: this replica can not take the session right now."""

    def __init__(self, code: str, retry_after: int = 2) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class Connection(Protocol):
    def send_json(self, data: dict[str, Any]) -> bool:
        """Queue for the client; False when the client is too slow to take it."""
        ...

    def send_bytes(self, data: bytes) -> bool: ...

    def purge_audio(self) -> None:
        """Forget audio that is queued but not yet sent (a barge-in must be heard at once)."""
        ...

    def close(self, code: int) -> None: ...


class Finalizer(Protocol):
    async def finalize(self, record: SessionRecord, finals: list[dict[str, Any]]) -> bool: ...


@dataclass(slots=True)
class Deps:
    store: SessionStore
    settings: VoiceSettings
    replica_id: str
    recognizer_factory: Callable[[SessionRecord], StreamingRecognizer]
    assistant: Assistant | None = None
    finalizer: Finalizer | None = None
    clock: Callable[[], float] = time.monotonic


class LiveSession:
    def __init__(
        self, record: SessionRecord, deps: Deps, on_closed: Callable[[LiveSession], None]
    ) -> None:
        self.record = record
        self.state = SessionState.ACTIVE
        self._deps = deps
        self._on_closed = on_closed
        self._conn: Connection | None = None
        self._log: deque[dict[str, Any]] = deque(maxlen=deps.settings.replay_events)
        self._event_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._next_seq = record.last_sequence + 1
        self._next_offset = record.audio_ack_ms
        self._recognizer: StreamingRecognizer | None = None
        self._base_ms = 0
        self._listening = False
        self._question: list[str] = []
        self._turn = 0
        self._turn_task: asyncio.Task[None] | None = None
        self._grace: asyncio.Task[None] | None = None
        self._finals: asyncio.Queue[tuple[Recognition, int, int]] = asyncio.Queue()
        self._final_worker = asyncio.create_task(self._persist_finals())
        self._keepalive = asyncio.create_task(self._keep_alive())
        self._receipts: deque[tuple[int, float]] = deque(maxlen=2048)
        self._tokens = deps.settings.audio_burst_seconds * 1000.0
        self._refilled = deps.clock()
        self._started = deps.clock()
        self._moving = False  # suspended: what the recognizer still says is resent elsewhere
        self.accepted_ms = 0
        self.duplicate_ms = 0
        self.gap_ms = 0

    # ------------------------------------------------------------------ connection

    async def attach(self, conn: Connection, last_ack: int) -> None:
        if self.state in (SessionState.FINALIZING, SessionState.CLOSED):
            raise SessionClosed
        if self.state is SessionState.DRAINING:
            raise Busy("draining", retry_after=1)
        previous, self._conn = self._conn, conn
        if previous is not None and previous is not conn:
            previous.close(CLOSE_REPLACED)
        self._cancel_grace()
        resumed = self.state is SessionState.RECONNECTING or last_ack > 0
        self.state = SessionState.ACTIVE
        self._next_seq = max(self._next_seq, last_ack + 1)
        self._ack(last_ack)
        await self._deps.store.ack_events(self.record.session_id, last_ack)
        durable = [
            json.loads(payload)
            for payload in await self._deps.store.events_after(self.record.session_id, last_ack)
        ]
        local = [event for event in self._log if event["sequence"] > last_ack]
        replay_by_sequence = {event["sequence"]: event for event in local}
        replay_by_sequence.update({event["sequence"]: event for event in durable})
        replay = [replay_by_sequence[number] for number in sorted(replay_by_sequence)]
        oldest = replay[0]["sequence"] if replay else self._next_seq
        conn.send_json(
            server_event(
                "session.ready",
                self.record.session_id,
                resumed=resumed,
                mode=self.record.mode,
                audio_resume_offset_ms=self._next_offset,
                next_sequence=self._next_seq,
                events_lost=last_ack + 1 < oldest,
                replayed=len(replay),
                heartbeat_seconds=self._deps.settings.heartbeat_seconds,
            )
        )
        for event in replay:
            conn.send_json(event)

    def detach(self, conn: Connection) -> None:
        if self._conn is not conn:
            return
        self._conn = None
        if self.state is SessionState.ACTIVE:
            self.state = SessionState.RECONNECTING
            grace = self._deps.settings.reconnect_grace_seconds
            self._grace = asyncio.create_task(self._expire(grace))

    async def _expire(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        await self.finalize("connection_lost")

    def _cancel_grace(self) -> None:
        if self._grace is not None and self._grace is not asyncio.current_task():
            self._grace.cancel()
        self._grace = None

    def pong(self) -> None:
        if self._conn is not None:
            self._conn.send_json(server_event("pong", self.record.session_id))

    async def _keep_alive(self) -> None:
        """The server keeps the lease and the admission slot alive and enforces the maximum
        duration on its own clock: a client that never pings gains nothing."""
        while True:
            await asyncio.sleep(self._deps.settings.heartbeat_seconds)
            try:
                await self.heartbeat()
            except Exception:
                logger.warning(
                    "voice_keepalive_failed session=%s", self.record.session_id, exc_info=True
                )

    async def heartbeat(self) -> None:
        if self.state not in (SessionState.ACTIVE, SessionState.RECONNECTING):
            return
        settings = self._deps.settings
        if self._deps.clock() - self._started > settings.max_session_seconds:
            await self.finalize("max_duration")
            return
        held = await self._deps.store.acquire_lease(
            self.record.session_id, self._deps.replica_id, settings.lease_ms
        )
        if not held:  # another replica took over: this one must stop without finalizing
            await self.suspend("lease_lost")
            return
        # A session that is still served is not for recovery to finish.
        await self._deps.store.defer_finalization(
            self.record.session_id, settings.finalization_delay_seconds
        )
        await self._deps.store.admit(
            self.record.workspace_id,
            self.record.user_id,
            self.record.session_id,
            settings.limits,
            settings.admission_ttl_seconds,
        )

    # ------------------------------------------------------------------ events out

    def emit(self, type_: str, **fields: Any) -> dict[str, Any]:
        """A reliable event: sequenced, kept for replay until the client acknowledges it."""
        event = server_event(type_, self.record.session_id, self._next_seq, **fields)
        self._next_seq += 1
        self._log.append(event)
        if self._conn is not None and not self._conn.send_json(event):
            self._slow_client()
        return event

    async def emit_reliable(
        self, type_: str, *, audio_ack_ms: int = 0, **fields: Any
    ) -> dict[str, Any]:
        """Persist before publish so a client never acknowledges data held by one replica only."""
        async with self._event_lock:
            event = server_event(type_, self.record.session_id, self._next_seq, **fields)
            await self._deps.store.put_event(
                self.record.session_id,
                self._next_seq,
                json.dumps(event, separators=(",", ":")),
                self._deps.settings.replay_events,
                audio_ack_ms,
            )
            self._next_seq += 1
            self._log.append(event)
        if self._conn is not None and not self._conn.send_json(event):
            self._slow_client()
        return event

    def _ephemeral(self, type_: str, **fields: Any) -> None:
        if self._conn is not None:
            self._conn.send_json(server_event(type_, self.record.session_id, None, **fields))

    def _slow_client(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.close(CLOSE_BUSY)  # the log keeps what it missed; the client resumes
        if self.state is SessionState.ACTIVE:
            self.state = SessionState.RECONNECTING
            self._grace = asyncio.create_task(
                self._expire(self._deps.settings.reconnect_grace_seconds)
            )

    def _ack(self, sequence: int) -> None:
        while self._log and self._log[0]["sequence"] <= sequence:
            self._log.popleft()

    def acknowledge(self, sequence: int) -> None:
        self._ack(sequence)
        self._background(self._ack_durable(sequence))

    def _background(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _ack_durable(self, sequence: int) -> None:
        try:
            await self._deps.store.ack_events(self.record.session_id, sequence)
        except Exception:
            logger.warning(
                "voice_event_ack_failed session=%s", self.record.session_id, exc_info=True
            )

    # ------------------------------------------------------------------ audio in

    async def on_audio(self, frame: AudioFrame) -> None:
        if self.state is not SessionState.ACTIVE:
            return
        if self.record.mode == "assistant" and not self._listening:
            return
        if frame.end_ms <= self._next_offset:
            self.duplicate_ms += frame.end_ms - frame.offset_ms
            return
        pcm, offset = frame.pcm, frame.offset_ms
        if offset < self._next_offset:  # a resent frame that overlaps what the server already has
            skip = (self._next_offset - offset) * BYTES_PER_MS
            self.duplicate_ms += self._next_offset - offset
            pcm, offset = pcm[skip:], self._next_offset
        elif offset > self._next_offset and self._recognizer is not None:
            self.gap_ms += offset - self._next_offset
        self._take_tokens(len(pcm) // BYTES_PER_MS)
        if self._recognizer is None:
            await self._start_recognizer(offset)
        assert self._recognizer is not None
        self._recognizer.push(pcm)
        self.accepted_ms += len(pcm) // BYTES_PER_MS
        self._next_offset = offset + len(pcm) // BYTES_PER_MS
        self._receipts.append((self._next_offset, self._deps.clock()))

    def _take_tokens(self, milliseconds: int) -> None:
        now = self._deps.clock()
        cap = self._deps.settings.audio_burst_seconds * 1000.0
        self._tokens = min(cap, self._tokens + (now - self._refilled) * 1000 * REFILL)
        self._refilled = now
        if milliseconds > self._tokens:
            raise ProtocolError("audio.too_fast")
        self._tokens -= milliseconds

    async def _start_recognizer(self, base_ms: int) -> None:
        recognizer = self._deps.recognizer_factory(self.record)
        self._base_ms = base_ms
        await recognizer.start(self.record.locale, self._on_recognition, base_ms)
        self._recognizer = recognizer

    async def _stop_recognizer(self) -> None:
        recognizer, self._recognizer = self._recognizer, None
        if recognizer is not None:
            try:
                await recognizer.stop()
            except Exception:
                logger.warning("voice_recognizer_stop_failed", exc_info=True)
                self.emit("error", code="recognizer_failed", retryable=True)
        await self._finals.join()

    def _latency_ms(self, end_ms: int) -> int | None:
        for offset, received in self._receipts:
            if offset >= end_ms:
                return int((self._deps.clock() - received) * 1000)
        return None

    # ------------------------------------------------------------------ recognition

    def _on_recognition(self, recognition: Recognition) -> None:
        start, end = recognition.start_ms + self._base_ms, recognition.end_ms + self._base_ms
        if self._moving or not recognition.text.strip():
            return
        if recognition.kind == "partial":
            self._ephemeral(
                "transcript.partial",
                text=recognition.text,
                audio_offset_start_ms=start,
                audio_offset_end_ms=end,
                speaker_id=f"S{recognition.speaker}",
                latency_ms=self._latency_ms(end),
            )
        else:
            self._finals.put_nowait((recognition, start, end))

    async def _persist_finals(self) -> None:
        while True:
            recognition, start, end = await self._finals.get()
            suspend = False
            try:
                payload = {
                    "start_ms": start,
                    "end_ms": end,
                    "text": recognition.text,
                    "speaker": recognition.speaker,
                    "confidence": recognition.confidence,
                    "locale": recognition.locale or self.record.locale,
                }
                if self.record.mode == "meeting":
                    # Stored before it is announced: once the client has seen a final it may
                    # discard that audio, so the phrase has to survive this replica first.
                    await self._deps.store.put_final(
                        self.record.session_id,
                        start,
                        json.dumps(payload),
                        self._deps.settings.finalization_delay_seconds,
                    )
                else:
                    self._question.append(recognition.text)
                await self.emit_reliable(
                    "transcript.final",
                    audio_ack_ms=end if self.record.mode == "meeting" else 0,
                    text=recognition.text,
                    audio_offset_start_ms=start,
                    audio_offset_end_ms=end,
                    speaker_id=f"S{recognition.speaker}",
                    confidence=recognition.confidence,
                    latency_ms=self._latency_ms(end),
                )
            except Exception:
                logger.exception("voice_final_failed session=%s", self.record.session_id)
                self._ephemeral("error", code="persist_failed", retryable=True)
                suspend = True
            finally:
                self._finals.task_done()
            if suspend:
                self._background(self.suspend("store_unavailable"))

    # ------------------------------------------------------------------ push to talk

    async def ptt_start(self, offset_ms: int) -> None:
        if self.record.mode != "assistant":
            raise ProtocolError("ptt.wrong_mode")
        await self.barge_in()
        await self._stop_recognizer()
        self._question = []
        self._next_offset = offset_ms
        self._listening = True

    async def ptt_end(self) -> None:
        if not self._listening:
            return
        self._listening = False
        await self._stop_recognizer()
        question = " ".join(self._question).strip()
        if not question:
            self.emit("error", code="ptt.empty", retryable=False)
            return
        assistant = self._deps.assistant
        if assistant is None:
            self.emit("error", code="assistant.unavailable", retryable=False)
            return
        self._turn += 1
        self._turn_task = asyncio.create_task(self._run_turn(assistant, question, self._turn))

    async def _run_turn(self, assistant: Assistant, question: str, turn: int) -> None:
        try:
            await assistant.respond(
                self.record.workspace_id,
                self.record.user_id,
                self.record.locale,
                question,
                turn,
                self.emit_reliable,
                self._send_audio,
            )
        except asyncio.CancelledError:
            await self.emit_reliable("assistant.cancelled", turn=turn)
            raise
        except Exception:
            logger.exception("voice_assistant_event_failed session=%s", self.record.session_id)
            self._ephemeral("error", code="persist_failed", retryable=True)
            self._background(self.suspend("store_unavailable"))

    async def _send_audio(self, turn: int, index: int, pcm: bytes) -> None:
        data = encode_audio_out(turn, index, pcm)
        while self._conn is not None and not self._conn.send_bytes(data):
            await asyncio.sleep(0.02)  # paced by the client: audio is not buffered without bound

    async def barge_in(self) -> None:
        task, self._turn_task = self._turn_task, None
        if task is None or task.done():
            return
        if self._conn is not None:
            self._conn.purge_audio()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # ------------------------------------------------------------------ ending

    async def end(self) -> None:
        await self.finalize("client_end")

    async def finalize(self, reason: str) -> None:
        if self.state in (SessionState.FINALIZING, SessionState.CLOSED):
            return
        self.state = SessionState.FINALIZING
        self._cancel_grace()
        try:
            await self._finish(reason)
        except Exception:
            logger.exception("voice_finalize_failed session=%s", self.record.session_id)
        finally:
            await self._release()

    async def _finish(self, reason: str) -> None:
        await self.barge_in()
        await self._stop_recognizer()
        persisted = await self._hand_over()
        await self.emit_reliable(
            "session.closed",
            reason=reason,
            persisted=persisted,
            accepted_audio_ms=self.accepted_ms,
            duplicate_audio_ms=self.duplicate_ms,
            gap_audio_ms=self.gap_ms,
        )
        if self._conn is not None:
            self._conn.close(CLOSE_NORMAL)
        await self._deps.store.close(self.record.session_id)
        if not persisted and self.record.mode == "meeting":
            # Whatever is still stored is now orphaned: recovery may take it at once.
            await self._deps.store.defer_finalization(self.record.session_id, 0)

    async def _hand_over(self) -> bool:
        finalizer, store = self._deps.finalizer, self._deps.store
        if self.record.mode != "meeting" or finalizer is None:
            return False
        finals = [json.loads(item) for item in await store.finals(self.record.session_id)]
        if not finals:
            return False
        try:
            done = await finalizer.finalize(self.record, finals)
        except Exception:
            logger.exception("voice_finalize_failed session=%s", self.record.session_id)
            self.emit("error", code="persist_failed", retryable=False)
            return False  # the finals stay in the store until they expire
        if done:
            await store.forget(self.record.session_id)
        return done

    async def suspend(self, reason: str) -> None:
        """Stop on this replica without ending the session: it continues on another one."""
        if self.state in (SessionState.FINALIZING, SessionState.CLOSED):
            return
        self.state = SessionState.DRAINING
        self._moving = True  # nothing half-heard is kept: the client resends from the last final
        self._cancel_grace()
        try:
            await self.barge_in()
            await self._stop_recognizer()
            self._ephemeral("session.draining", reason=reason, retry_after_ms=1000)
            if self._conn is not None:
                self._conn.close(CLOSE_DRAINING)
        except Exception:
            logger.exception("voice_suspend_failed session=%s", self.record.session_id)
        finally:
            await self._release(keep_admission=True)

    async def _release(self, keep_admission: bool = False) -> None:
        self.state = SessionState.CLOSED if not keep_admission else self.state
        store = self._deps.store
        try:
            if keep_admission:
                # Moving to another replica: give the client the whole grace period to arrive
                # before recovery may treat the stored phrases as abandoned.
                await store.defer_finalization(
                    self.record.session_id, self._deps.settings.finalization_delay_seconds
                )
            await store.release_lease(self.record.session_id, self._deps.replica_id)
            if not keep_admission:
                await store.leave(
                    self.record.workspace_id, self.record.user_id, self.record.session_id
                )
        except Exception:
            # The lease and the admission slot lapse by themselves; the replica must not keep a
            # session it has stopped serving.
            logger.warning("voice_release_failed session=%s", self.record.session_id, exc_info=True)
        finally:
            self._final_worker.cancel()
            self._keepalive.cancel()
            self._on_closed(self)
