from __future__ import annotations

import threading

import pytest

from nodo_api.errors import NotFoundError
from nodo_api.resilience import (
    Bulkhead,
    CircuitBreaker,
    DependencyUnavailable,
    Guard,
    Guarded,
    Overloaded,
    RetryPolicy,
    State,
    is_transient,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Boom(Exception):
    def __init__(self, status: int | None = None) -> None:
        super().__init__("boom")
        self.status_code = status


class ReadTimeout(Exception):
    pass


def _guard(clock: Clock, *, limit: int = 4, attempts: int = 3, threshold: int = 3) -> Guard:
    return Guard(
        "dep",
        CircuitBreaker("dep", failure_threshold=threshold, recovery_seconds=10, clock=clock),
        Bulkhead("dep", limit, wait_seconds=0.01),
        RetryPolicy(attempts=attempts, base_seconds=1, max_seconds=8, deadline_seconds=30),
        sleep=clock.sleep,
        clock=clock,
        jitter=lambda low, high: high,  # the longest wait: easy to assert
    )


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        (Boom(429), True),
        (Boom(503), True),
        (Boom(400), False),
        (Boom(403), False),
        (ReadTimeout(), True),
        (TimeoutError(), True),
        (ConnectionResetError(), True),
        (ValueError("bad input"), False),
        (NotFoundError(), False),
    ],
)
def test_only_errors_a_retry_can_fix_count_as_transient(error, transient) -> None:
    assert is_transient(error) is transient


def test_a_transient_failure_is_retried_with_growing_waits_and_then_succeeds() -> None:
    clock, calls = Clock(), []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise Boom(503)
        return "ok"

    assert _guard(clock).call(flaky) == "ok"
    assert len(calls) == 3 and clock.slept == [1, 2]


def test_running_out_of_attempts_is_a_dependency_error_not_the_raw_vendor_error() -> None:
    clock = Clock()
    with pytest.raises(DependencyUnavailable) as caught:
        _guard(clock).call(lambda: (_ for _ in ()).throw(Boom(503)))
    assert caught.value.dependency == "dep" and caught.value.status_code == 503


def test_a_client_error_is_raised_at_once_and_does_not_count_against_the_dependency() -> None:
    clock, calls = Clock(), []
    guard = _guard(clock, threshold=1)

    def bad_request():
        calls.append(1)
        raise Boom(400)

    for _ in range(5):
        with pytest.raises(Boom):
            guard.call(bad_request)
    assert len(calls) == 5 and guard.breaker.state is State.CLOSED and clock.slept == []


def test_retries_stop_at_the_deadline() -> None:
    clock = Clock()
    guard = _guard(clock, attempts=10, threshold=100)
    guard.retry = RetryPolicy(attempts=10, base_seconds=10, max_seconds=10, deadline_seconds=25)
    calls = []

    def down():
        calls.append(1)
        raise Boom(503)

    with pytest.raises(DependencyUnavailable):
        guard.call(down)
    assert len(calls) == 3  # 0 + 10 + 10 = 20 s used, the next wait would pass the 25 s budget


def test_the_circuit_opens_refuses_calls_then_probes_once_and_closes() -> None:
    clock, calls = Clock(), []
    guard = _guard(clock, attempts=1, threshold=3)

    def down():
        calls.append(1)
        raise Boom(503)

    for _ in range(3):
        with pytest.raises(DependencyUnavailable):
            guard.call(down)
    assert guard.breaker.state is State.OPEN
    with pytest.raises(DependencyUnavailable) as refused:
        guard.call(down)
    assert len(calls) == 3 and refused.value.retry_after == 10  # refused without a call
    clock.now += 11
    assert guard.breaker.state is State.HALF_OPEN
    assert guard.call(lambda: "back") == "back"
    assert guard.breaker.state is State.CLOSED


def test_a_failed_probe_reopens_the_circuit() -> None:
    clock = Clock()
    guard = _guard(clock, attempts=1, threshold=1)
    with pytest.raises(DependencyUnavailable):
        guard.call(lambda: (_ for _ in ()).throw(Boom(503)))
    clock.now += 11
    with pytest.raises(DependencyUnavailable):
        guard.call(lambda: (_ for _ in ()).throw(Boom(503)))
    assert guard.breaker.state is State.OPEN


def test_only_one_probe_runs_while_half_open() -> None:
    clock = Clock()
    breaker = CircuitBreaker("dep", failure_threshold=1, recovery_seconds=5, clock=clock)
    breaker.before_call()
    breaker.failure()
    clock.now += 6
    breaker.before_call()  # the probe
    with pytest.raises(DependencyUnavailable):
        breaker.before_call()  # everyone else is turned away


def test_the_bulkhead_turns_callers_away_when_the_dependency_is_saturated() -> None:
    gate, started = threading.Event(), threading.Event()
    bulkhead = Bulkhead("dep", limit=2, wait_seconds=0.05)

    def hold():
        with bulkhead:
            started.set()
            gate.wait(2)

    holders = [threading.Thread(target=hold) for _ in range(2)]
    for thread in holders:
        thread.start()
    started.wait(1)
    with pytest.raises(Overloaded), bulkhead:
        pass
    gate.set()
    for thread in holders:
        thread.join()
    with bulkhead:  # the slots came back
        pass


def test_guarded_wraps_only_the_listed_methods() -> None:
    clock, calls = Clock(), []

    class Adapter:
        label = "adapter"

        def search(self, text):
            calls.append(text)
            if len(calls) == 1:
                raise Boom(503)
            return [text]

        def other(self):
            raise Boom(503)

    wrapped = Guarded(Adapter(), _guard(clock), ("search",))
    assert wrapped.search("q") == ["q"] and len(calls) == 2  # retried through the guard
    assert wrapped.label == "adapter"
    with pytest.raises(Boom):
        wrapped.other()  # not guarded: the raw error comes through
