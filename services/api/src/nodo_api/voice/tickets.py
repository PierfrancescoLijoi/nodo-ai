"""Short-lived, single-session tickets for the voice gateway.

The WebSocket handshake of a browser can not carry an Authorization header and a token in the
address would end up in logs, so the client first obtains a ticket over the normal authenticated
REST call and presents it as the first message on the socket. A ticket names one session and one
person, expires in about a minute and is spent on first use (the store remembers its id).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

MIN_KEY_BYTES = 32


class TicketError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Ticket:
    session_id: str
    user_id: str
    workspace_id: str
    jti: str
    expires_at: int


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class TicketSigner:
    def __init__(
        self, key: bytes, ttl_seconds: int, clock: Callable[[], float] = time.time
    ) -> None:
        if len(key) < MIN_KEY_BYTES:
            raise ValueError("The ticket key must be at least 32 bytes")
        self._key = key
        self.ttl_seconds = ttl_seconds
        self._clock = clock

    def issue(self, session_id: str, user_id: str, workspace_id: str) -> str:
        payload = _b64(
            json.dumps(
                {
                    "v": 1,
                    "sid": session_id,
                    "uid": user_id,
                    "wid": workspace_id,
                    "jti": secrets.token_urlsafe(16),
                    "exp": int(self._clock()) + self.ttl_seconds,
                },
                separators=(",", ":"),
            ).encode()
        )
        return f"{payload}.{_b64(self._sign(payload))}"

    def verify(self, token: str) -> Ticket:
        try:
            payload, signature = token.split(".")
            if not hmac.compare_digest(_unb64(signature), self._sign(payload)):
                raise TicketError("Invalid ticket")
            claims = json.loads(_unb64(payload))
            ticket = Ticket(
                claims["sid"], claims["uid"], claims["wid"], claims["jti"], int(claims["exp"])
            )
        except (ValueError, KeyError, TypeError) as error:
            raise TicketError("Invalid ticket") from error
        if claims.get("v") != 1 or ticket.expires_at < self._clock():
            raise TicketError("Ticket expired")
        return ticket

    def _sign(self, payload: str) -> bytes:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).digest()
