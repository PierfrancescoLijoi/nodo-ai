"""Failure containment for calls to other services (ADR-017).

Four small mechanisms, composed in `Guard`:

- **Bulkhead**: a dependency gets a fixed number of concurrent calls. When it is slow, the callers
  queue up behind its limit and are turned away, instead of holding every thread of the process.
- **Circuit breaker**: after repeated transient failures calls are refused at once for a cool-off
  period, then one probe call decides whether to close again. A dependency that is down costs the
  caller nothing, and is not hammered while it recovers.
- **Retry** with exponential backoff and full jitter, only for transient errors, and bounded by a
  deadline so retries never outlive the request they serve. Exactly one layer retries: the vendor
  SDK retries are switched off where we retry here, so attempts never multiply.
- **Classification**: only errors that a retry can fix (timeouts, connection errors, 429, 5xx)
  count against a dependency. A 400 or a 403 is our bug or our configuration, not an outage.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

from nodo_api.errors import NodoError
from nodo_api.telemetry import instruments

logger = logging.getLogger("nodo_resilience")
T = TypeVar("T")

TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
TRANSIENT_NAMES = (
    "timeout",
    "connecterror",
    "connectionerror",
    "readerror",
    "remoteprotocolerror",
    "serviceresponseerror",
    "servicerequesterror",
    "ratelimiterror",
    "internalservererror",
    "apiconnectionerror",
    "operationalerror",
    "serviceunavailable",
)


class DependencyUnavailable(NodoError):
    """A dependency is failing and calls to it are being refused or have run out of retries."""

    code = "dependency_unavailable"
    status_code = 503
    public_message = "A required service is temporarily unavailable"

    def __init__(self, dependency: str, retry_after: int = 5) -> None:
        super().__init__(dependency)
        self.dependency = dependency
        self.retry_after = retry_after


class Overloaded(NodoError):
    """Too many calls are already in flight for this dependency or this operation."""

    code = "overloaded"
    status_code = 429
    public_message = "Too many requests, try again shortly"

    def __init__(self, what: str, retry_after: int = 2) -> None:
        super().__init__(what)
        self.what = what
        self.retry_after = retry_after


def is_transient(error: BaseException) -> bool:
    """Would trying again plausibly help? Decided by status code or by the error's name, because
    the vendor SDKs (OpenAI, Azure core, httpx, psycopg) share no base class."""
    if isinstance(error, NodoError):
        return False
    status = getattr(error, "status_code", None) or getattr(
        getattr(error, "response", None), "status_code", None
    )
    if isinstance(status, int):
        return status in TRANSIENT_STATUS
    name = type(error).__name__.lower()
    return isinstance(error, TimeoutError | ConnectionError) or any(
        t in name for t in TRANSIENT_NAMES
    )


class State(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_seconds: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self._threshold = failure_threshold
        self._recovery = recovery_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at = 0.0
        self._state = State.CLOSED
        self._probing = False
        self._lock = threading.Lock()

    @property
    def state(self) -> State:
        with self._lock:
            return self._peek()

    def _peek(self) -> State:
        if self._state is State.OPEN and self._clock() - self._opened_at >= self._recovery:
            return State.HALF_OPEN
        return self._state

    def before_call(self) -> None:
        with self._lock:
            state = self._peek()
            if state is State.OPEN:
                raise DependencyUnavailable(self.name, retry_after=max(1, int(self._recovery)))
            if state is State.HALF_OPEN:
                if self._probing:  # one probe at a time decides for everybody
                    raise DependencyUnavailable(self.name, retry_after=1)
                self._state, self._probing = State.HALF_OPEN, True

    def success(self) -> None:
        with self._lock:
            if self._state is not State.CLOSED:
                logger.info("circuit_closed dependency=%s", self.name)
            self._state, self._failures, self._probing = State.CLOSED, 0, False

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is State.HALF_OPEN or self._failures >= self._threshold:
                if self._state is not State.OPEN:
                    logger.warning("circuit_opened dependency=%s", self.name)
                    instruments.circuit_opened.add(1, {"dependency": self.name})
                self._state, self._opened_at, self._probing = State.OPEN, self._clock(), False

    def neutral(self) -> None:
        """The call failed for a reason that says nothing about the dependency's health."""
        with self._lock:
            self._probing = False


class Bulkhead:
    def __init__(self, name: str, limit: int, wait_seconds: float = 0.5) -> None:
        self.name = name
        self.limit = limit
        self._wait = wait_seconds
        self._slots = threading.BoundedSemaphore(limit)

    def __enter__(self) -> Bulkhead:
        if not self._slots.acquire(timeout=self._wait):
            raise Overloaded(self.name)
        return self

    def __exit__(self, *exc: object) -> None:
        self._slots.release()


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3
    base_seconds: float = 0.2
    max_seconds: float = 4.0
    deadline_seconds: float = 20.0  # all attempts together, sleeps included


@dataclass
class Guard:
    """Everything between a caller and one dependency."""

    name: str
    breaker: CircuitBreaker
    bulkhead: Bulkhead
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    jitter: Callable[[float, float], float] = random.uniform

    def call(self, fn: Callable[[], T]) -> T:
        started = self.clock()
        with self.bulkhead:
            for attempt in range(1, self.retry.attempts + 1):
                self.breaker.before_call()
                try:
                    result = fn()
                except BaseException as error:
                    if not is_transient(error):
                        self.breaker.neutral()
                        raise
                    self.breaker.failure()
                    if attempt == self.retry.attempts:
                        instruments.dependency_failures.add(1, {"dependency": self.name})
                    delay = self._delay(attempt)
                    out_of_time = self.clock() + delay - started > self.retry.deadline_seconds
                    if attempt == self.retry.attempts or out_of_time:
                        raise DependencyUnavailable(self.name) from error
                    self.sleep(delay)
                else:
                    self.breaker.success()
                    return result
        raise DependencyUnavailable(
            self.name
        )  # pragma: no cover - the loop always returns or raises

    def _delay(self, attempt: int) -> float:
        ceiling = min(self.retry.max_seconds, self.retry.base_seconds * 2 ** (attempt - 1))
        return self.jitter(0.0, ceiling)  # full jitter: concurrent callers spread out


class Guarded:
    """Wraps an adapter so listed methods run through a Guard; other attributes pass through."""

    def __init__(self, target: Any, guard: Guard, methods: tuple[str, ...]) -> None:
        self._target = target
        self._guard = guard
        self._methods = frozenset(methods)

    @property
    def target(self) -> Any:
        return self._target

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._target, name)
        if name not in self._methods or not callable(attribute):
            return attribute

        def call(*args: Any, **kwargs: Any) -> Any:
            return self._guard.call(lambda: attribute(*args, **kwargs))

        return call


def make_guard(name: str, *, limit: int, retry: RetryPolicy | None = None, **breaker: Any) -> Guard:
    return Guard(
        name, CircuitBreaker(name, **breaker), Bulkhead(name, limit), retry or RetryPolicy()
    )
