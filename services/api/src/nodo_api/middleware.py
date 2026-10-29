"""Edge behaviour of the API process: protection against too much, too big, and too trusting.

Written as a pure ASGI middleware so it sees the raw request before any parsing and, unlike the
decorator style, still adds its headers to responses produced by error handlers.

- **Load shedding.** A replica serves at most `max_in_flight` requests at once. Past that it answers
  503 with `Retry-After` immediately instead of queueing work it can not finish in time (queues turn
  a spike into a latency collapse). The gateway and the platform's autoscaler see the 503s and add
  replicas. Health probes are never shed, or the platform would restart a replica that is merely
  busy.
- **Body size limits** per route family, checked on `Content-Length` and, for chunked bodies, while
  the bytes arrive. Gateways enforce the same limits first; this is the second line.
- **Response headers** that make an API response useless as a page: no sniffing, no framing, no
  caching of private data, no referrers, a content policy that allows nothing.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from nodo_api.telemetry import instruments

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

UNSHED_PATHS = frozenset({"/healthz", "/readyz"})
DEFAULT_BODY_LIMIT = 1024 * 1024
SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
    (b"cross-origin-resource-policy", b"same-origin"),
)


class PayloadTooLarge(Exception):
    pass


def _problem(status: int, code: str, retry_after: int | None = None) -> tuple[Message, Message]:
    body = json.dumps({"error": {"code": code}}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    if retry_after is not None:
        headers.append((b"retry-after", str(retry_after).encode()))
    return (
        {"type": "http.response.start", "status": status, "headers": headers},
        {"type": "http.response.body", "body": body},
    )


class EdgeProtection:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_in_flight: int,
        body_limits: Iterable[tuple[str, int]] = (),
        default_body_limit: int = DEFAULT_BODY_LIMIT,
        security_headers: bool = True,
    ) -> None:
        self._app = app
        self._max = max_in_flight
        self._limits = tuple(body_limits)
        self._default = default_body_limit
        self._headers = security_headers
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def _limit_for(self, path: str) -> int:
        return next(
            (limit for prefix, limit in self._limits if path.startswith(prefix)), self._default
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = scope["path"]
        if path not in UNSHED_PATHS and self._in_flight >= self._max:
            instruments.shed.add(1)
            for message in _problem(503, "overloaded", retry_after=2):
                await send(message)
            return
        limit = self._limit_for(path)
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            for message in _problem(413, "payload_too_large"):
                await send(message)
            return

        started = False
        seen = 0

        async def counting_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise PayloadTooLarge
            return message

        async def secured_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                if self._headers:
                    present = {name for name, _ in message["headers"]}
                    message = {
                        **message,
                        "headers": [
                            *message["headers"],
                            *[(n, v) for n, v in SECURITY_HEADERS if n not in present],
                        ],
                    }
            await send(message)

        self._in_flight += 1
        try:
            await self._app(scope, counting_receive, secured_send)
        except PayloadTooLarge:
            if not started:
                for message in _problem(413, "payload_too_large"):
                    await send(message)
        finally:
            self._in_flight -= 1
