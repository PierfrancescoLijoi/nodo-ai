"""Signing keys of the identity provider, fetched and cached.

The API validates every bearer token against the provider's public keys (JWKS). Fetching them on
each request would put the provider on the hot path and turn its outage into ours; caching them
forever would break at the first key rotation. So:

- keys are cached for `ttl` seconds and refreshed in the background of a request that needs them;
- a token signed with an unknown key id triggers one refresh, at most once per `cooldown` seconds,
  so a flood of forged key ids can not turn into a flood of requests to the provider;
- if the provider is unreachable the last keys keep being served for `stale_for` seconds: a
  provider outage must not log everybody out;
- the key set URL comes from the settings or from the issuer's discovery document.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger("nodo_jwks")

DEFAULT_TTL_SECONDS = 3600
DEFAULT_COOLDOWN_SECONDS = 60
DEFAULT_STALE_SECONDS = 24 * 3600
REQUEST_TIMEOUT_SECONDS = 5.0
MAX_KEYS = 50  # a key set larger than this is not a key set


class JwksUnavailable(RuntimeError):
    pass


class JwksCache:
    def __init__(
        self,
        issuer: str,
        jwks_url: str | None = None,
        *,
        ttl: int = DEFAULT_TTL_SECONDS,
        cooldown: int = DEFAULT_COOLDOWN_SECONDS,
        stale_for: int = DEFAULT_STALE_SECONDS,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._url = jwks_url
        self._ttl = ttl
        self._cooldown = cooldown
        self._stale_for = stale_for
        self._http = http or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False)
        self._clock = clock
        self._keys: dict[str, Any] = {"keys": []}
        self._fetched_at: float | None = None
        self._last_attempt: float | None = None
        self._lock = threading.Lock()

    # The injectable `key_provider` of AuthManager: a callable returning the key set.
    def __call__(self, kid: str | None = None) -> dict[str, Any]:
        return self.keys(kid)

    def keys(self, wanted_kid: str | None = None) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            stale = self._fetched_at is None or now - self._fetched_at > self._ttl
            unknown = wanted_kid is not None and wanted_kid not in self._kids()
            if (stale or unknown) and self._may_try(now, forced=stale and self._fetched_at is None):
                self._refresh(now)
            if self._fetched_at is None:
                raise JwksUnavailable("The signing keys could not be fetched")
            if now - self._fetched_at > self._stale_for:
                raise JwksUnavailable("The signing keys are too old and could not be refreshed")
            return self._keys

    def knows(self, kid: str) -> bool:
        return kid in self._kids()

    def _kids(self) -> set[str]:
        return {str(k.get("kid")) for k in self._keys.get("keys", [])}

    def _may_try(self, now: float, *, forced: bool) -> bool:
        return forced or self._last_attempt is None or now - self._last_attempt >= self._cooldown

    def _refresh(self, now: float) -> None:
        self._last_attempt = now
        try:
            url = self._url or self._discover()
            reply = self._http.get(url)
            reply.raise_for_status()
            keys = reply.json()
            if not isinstance(keys.get("keys"), list) or len(keys["keys"]) > MAX_KEYS:
                raise ValueError("unexpected key set")
            self._url = url
            self._keys, self._fetched_at = keys, now
        except (httpx.HTTPError, ValueError, KeyError) as error:
            logger.warning("jwks_refresh_failed type=%s", type(error).__name__)

    def _discover(self) -> str:
        reply = self._http.get(f"{self._issuer}/.well-known/openid-configuration")
        reply.raise_for_status()
        document = reply.json()
        if str(document.get("issuer", "")).rstrip("/") != self._issuer:
            raise ValueError("the discovery document names a different issuer")
        url = str(document["jwks_uri"])
        if not url.startswith("https://"):
            raise ValueError("the key set must be fetched over https")
        return url
