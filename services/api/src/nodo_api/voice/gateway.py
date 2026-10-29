"""HTTP and WebSocket surface of the voice gateway.

    POST /v1/voice/sessions               open a session, get a one-use ticket (normal auth)
    POST /v1/voice/sessions/{id}/tickets  a new ticket for a reconnect (same person only)
    WS   /v1/voice/stream                 first message `hello` carries the ticket

The ticket travels in the first message, not in the address, so it is not written to access logs.
The gateway checks the person and the meeting again when the socket opens: it does not rely on the
edge having done it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from pydantic import Field

from nodo_api.api.v1.routes import PrincipalDep
from nodo_api.api.v1.schemas import FrozenModel
from nodo_api.domain.models import Locale, MeetingState
from nodo_api.errors import ConflictError, NotFoundError, ValidationFailed
from nodo_api.resilience import Overloaded
from nodo_api.voice.hub import Busy, VoiceRuntime, new_session_id
from nodo_api.voice.live import LiveSession, SessionClosed, SessionState
from nodo_api.voice.protocol import (
    PROTOCOL_VERSION,
    Ack,
    BargeIn,
    End,
    Hello,
    Ping,
    ProtocolError,
    PttEnd,
    PttStart,
    parse_audio_frame,
    parse_client_message,
    server_event,
)
from nodo_api.voice.store import SessionRecord
from nodo_api.voice.tickets import TicketError

logger = logging.getLogger("nodo_api.voice")

router = APIRouter(prefix="/v1/voice")
HELLO_TIMEOUT_SECONDS = 5.0
MAX_PENDING_AUDIO = 64  # chunks of 40 ms or more waiting for a slow listener


class SessionCreate(FrozenModel):
    mode: Literal["meeting", "assistant"]
    locale: Locale
    meeting_id: Annotated[str | None, Field(max_length=64)] = None


class SessionOutput(FrozenModel):
    session_id: str
    ticket: str
    ticket_expires_in: int
    protocol_version: int
    stream_path: str
    heartbeat_seconds: int
    reconnect_grace_seconds: int


class TicketOutput(FrozenModel):
    ticket: str
    ticket_expires_in: int


def _runtime(request: Request) -> VoiceRuntime:
    return request.app.state.voice


def _busy(error: Busy) -> Overloaded:
    return Overloaded(error.code, retry_after=error.retry_after)


@router.post("/sessions", response_model=SessionOutput, status_code=201)
async def open_session(payload: SessionCreate, actor: PrincipalDep, request: Request):
    runtime = _runtime(request)
    if payload.mode == "meeting":
        if payload.meeting_id is None:
            raise ValidationFailed("A live meeting needs the meeting it records into")
        meeting = await _owned_meeting(
            request, actor.workspace_id, actor.user_id, payload.meeting_id
        )
        if meeting.state is not MeetingState.UPLOADING:
            raise ConflictError
    elif payload.meeting_id is not None:
        raise ValidationFailed("The assistant does not record into a meeting")
    record = SessionRecord(
        new_session_id(),
        actor.workspace_id,
        actor.user_id,
        payload.mode,
        payload.locale.value,
        payload.meeting_id,
    )
    try:
        await runtime.hub.admit(record)
    except Busy as error:
        raise _busy(error) from error
    try:
        await runtime.deps.store.create(record, runtime.deps.settings.admission_ttl_seconds)
    except Exception:
        await runtime.deps.store.leave(record.workspace_id, record.user_id, record.session_id)
        raise
    return SessionOutput(
        session_id=record.session_id,
        ticket=runtime.signer.issue(record.session_id, actor.user_id, actor.workspace_id),
        ticket_expires_in=runtime.signer.ttl_seconds,
        protocol_version=PROTOCOL_VERSION,
        stream_path="/v1/voice/stream",
        heartbeat_seconds=runtime.deps.settings.heartbeat_seconds,
        reconnect_grace_seconds=runtime.deps.settings.reconnect_grace_seconds,
    )


@router.post("/sessions/{session_id}/tickets", response_model=TicketOutput)
async def new_ticket(session_id: str, actor: PrincipalDep, request: Request):
    runtime = _runtime(request)
    record = await runtime.deps.store.get(session_id)
    if (
        record is None
        or record.closed
        or (record.user_id, record.workspace_id) != (actor.user_id, actor.workspace_id)
    ):
        raise NotFoundError
    if runtime.hub.draining:
        raise _busy(Busy("draining", retry_after=1))
    return TicketOutput(
        ticket=runtime.signer.issue(session_id, actor.user_id, actor.workspace_id),
        ticket_expires_in=runtime.signer.ttl_seconds,
    )


async def _owned_meeting(request: Request, workspace_id: str, user_id: str, meeting_id: str):
    meetings = request.app.state.container.meetings
    return await run_in_threadpool(meetings.get_owned, workspace_id, user_id, meeting_id)


class SocketConnection:
    """Bounded outgoing queue and a writer task. `close` is queued behind what is already waiting,
    so the last events (such as session.closed) are delivered before the socket goes."""

    def __init__(self, ws: WebSocket, size: int) -> None:
        self._ws = ws
        self._size = size
        self._items: deque[tuple[str, Any]] = deque()
        self._wake = asyncio.Event()
        self._closing = False

    def send_json(self, data: dict[str, Any]) -> bool:
        if self._closing:
            return True
        if len(self._items) >= self._size:
            return False
        self._items.append(("json", data))
        self._wake.set()
        return True

    def send_bytes(self, data: bytes) -> bool:
        if self._closing:
            return True
        if sum(1 for kind, _ in self._items if kind == "bytes") >= MAX_PENDING_AUDIO:
            return False
        self._items.append(("bytes", data))
        self._wake.set()
        return True

    def purge_audio(self) -> None:
        self._items = deque(item for item in self._items if item[0] != "bytes")

    def close(self, code: int) -> None:
        if not self._closing:
            self._closing = True
            self._items.append(("close", code))
            self._wake.set()

    async def run(self) -> None:
        try:
            while True:
                while not self._items:
                    self._wake.clear()
                    await self._wake.wait()
                kind, value = self._items.popleft()
                if kind == "json":
                    await self._ws.send_json(value)
                elif kind == "bytes":
                    await self._ws.send_bytes(value)
                else:
                    await self._ws.close(code=value)
                    return
        except Exception:  # the client went away; the reader notices and detaches the session
            self._closing = True


@router.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    runtime: VoiceRuntime = ws.app.state.voice
    if not runtime.hub.begin_hello():  # too many sockets are still waiting to authenticate
        await ws.close(code=1013)
        return
    await ws.accept()
    conn = SocketConnection(ws, runtime.deps.settings.send_queue)
    writer = asyncio.create_task(conn.run())
    live: LiveSession | None = None
    try:
        try:
            live = await _handshake(ws, runtime, conn)
        finally:
            runtime.hub.end_hello()
        await _pump(ws, live, runtime)
    except ProtocolError as error:
        conn.send_json(server_event("error", "-", None, code=error.code, retryable=False))
        conn.close(1002)
    except Busy as error:
        conn.send_json(
            server_event(
                "error", "-", None, code=error.code, retryable=True, retry_after=error.retry_after
            )
        )
        conn.close(1013)
    except (WebSocketDisconnect, TimeoutError):
        pass
    finally:
        if live is not None:
            live.detach(conn)
        conn.close(1000)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(writer, timeout=2)
        writer.cancel()


async def _handshake(ws: WebSocket, runtime: VoiceRuntime, conn: SocketConnection) -> LiveSession:
    try:
        hello = parse_client_message(
            await asyncio.wait_for(ws.receive_text(), HELLO_TIMEOUT_SECONDS)
        )
    except (KeyError, RuntimeError) as error:  # a binary frame or a close before hello
        raise ProtocolError("hello.required") from error
    if not isinstance(hello, Hello):
        raise ProtocolError("hello.required")
    try:
        ticket = runtime.signer.verify(hello.ticket)
    except TicketError as error:
        raise ProtocolError("ticket.invalid") from error
    store = runtime.deps.store
    record = await store.get(ticket.session_id)
    if (
        record is None
        or record.closed
        or (record.user_id, record.workspace_id) != (ticket.user_id, ticket.workspace_id)
    ):
        raise ProtocolError("ticket.invalid")
    if not await store.consume_ticket(ticket.jti, runtime.signer.ttl_seconds * 2):
        raise ProtocolError("ticket.used")
    if record.meeting_id is not None:
        try:
            meetings = ws.app.state.container.meetings
            await run_in_threadpool(
                meetings.get_owned, record.workspace_id, record.user_id, record.meeting_id
            )
        except NotFoundError as error:
            raise ProtocolError("session.forbidden") from error
    try:
        return await runtime.hub.attach(record, conn, hello.last_ack)
    except SessionClosed as error:
        raise ProtocolError("session.closed") from error


async def _pump(ws: WebSocket, live: LiveSession, runtime: VoiceRuntime) -> None:
    idle = runtime.deps.settings.heartbeat_seconds * 2.5
    while live.state not in (SessionState.CLOSED, SessionState.DRAINING, SessionState.FINALIZING):
        message = await asyncio.wait_for(ws.receive(), idle)
        if message["type"] == "websocket.disconnect":
            return
        if message.get("bytes") is not None:
            await live.on_audio(parse_audio_frame(message["bytes"]))
            continue
        control = parse_client_message(message.get("text") or "")
        match control:
            case Ack():
                live.acknowledge(control.sequence)
            case Ping():
                live.pong()
            case PttStart():
                await live.ptt_start(control.offset_ms)
            case PttEnd():
                await live.ptt_end()
            case BargeIn():
                await live.barge_in()
            case End():
                await live.end()
                return
            case _:
                raise ProtocolError("message.unexpected")
