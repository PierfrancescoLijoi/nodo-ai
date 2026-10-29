"""The session store contract, run on the memory store always and on Redis when
NODO_TEST_REDIS_URL points at one (docker run -p 6390:6379 redis:7-alpine)."""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from nodo_api.voice.store import Admission, Limits, MemorySessionStore, SessionRecord

REDIS_URL = os.environ.get("NODO_TEST_REDIS_URL")


def _record(session_id: str = "s1", workspace: str = "w1") -> SessionRecord:
    return SessionRecord(session_id, workspace, "u1", "meeting", "en", "m1")


@pytest.fixture(params=["memory", "redis"])
def make_store(request):
    if request.param == "redis" and not REDIS_URL:
        pytest.skip("NODO_TEST_REDIS_URL not set")

    async def factory():
        if request.param == "memory":
            return MemorySessionStore()
        from nodo_api.voice.redis_store import RedisSessionStore

        store = RedisSessionStore.from_url(REDIS_URL)
        async for key in store._redis.scan_iter("nodo:voice:*"):
            await store._redis.delete(key)
        return store

    return factory


def run(make_store, scenario):
    async def main():
        store = await make_store()
        try:
            await scenario(store)
        finally:
            close = getattr(store, "aclose", None)
            if close:
                await close()

    asyncio.run(main())


def test_session_record_round_trip_and_monotonic_progress(make_store):
    async def scenario(store):
        await store.create(_record(), 60)
        await store.progress("s1", 5000, 7)
        await store.progress("s1", 3000, 4)  # an older report from a slow replica

        got = await store.get("s1")
        assert (got.audio_ack_ms, got.last_sequence, got.meeting_id, got.closed) == (
            5000,
            7,
            "m1",
            False,
        )
        assert await store.get("missing") is None
        await store.close("s1")
        assert (await store.get("s1")).closed

    run(make_store, scenario)


def test_ticket_is_spent_on_first_use(make_store):
    async def scenario(store):
        assert await store.consume_ticket("jti-1", 60) is True
        assert await store.consume_ticket("jti-1", 60) is False
        assert await store.consume_ticket("jti-2", 60) is True

    run(make_store, scenario)


def test_one_replica_owns_a_session_until_it_releases_or_the_lease_lapses(make_store):
    async def scenario(store):
        assert await store.acquire_lease("s1", "r1", 60_000)
        assert await store.acquire_lease("s1", "r1", 60_000)  # renewal
        assert not await store.acquire_lease("s1", "r2", 60_000)
        await store.release_lease("s1", "r2")  # not the owner: no effect
        assert not await store.acquire_lease("s1", "r2", 60_000)
        await store.release_lease("s1", "r1")
        assert await store.acquire_lease("s1", "r2", 60_000)

    run(make_store, scenario)


def test_a_lapsed_lease_can_be_taken_over(make_store):
    async def scenario(store):
        assert await store.acquire_lease("s1", "r1", 150)
        assert not await store.acquire_lease("s1", "r2", 150)
        await asyncio.sleep(0.3)
        assert await store.acquire_lease("s1", "r2", 150)

    run(make_store, scenario)


def test_admission_enforces_global_workspace_and_user_limits(make_store):
    limits = Limits(total=4, per_workspace=3, per_user=2)

    async def scenario(store):
        assert await store.admit("w1", "u1", "a", limits, 60) is Admission.OK
        assert await store.admit("w1", "u1", "b", limits, 60) is Admission.OK
        assert await store.admit("w1", "u1", "c", limits, 60) is Admission.USER_FULL
        assert await store.admit("w1", "u2", "d", limits, 60) is Admission.OK
        assert await store.admit("w1", "u3", "e", limits, 60) is Admission.WORKSPACE_FULL
        assert (
            await store.admit("w2", "u1", "f", limits, 60) is Admission.OK
        )  # same user id, another tenant
        assert await store.admit("w3", "u9", "g", limits, 60) is Admission.GLOBAL_FULL
        assert (
            await store.admit("w1", "u1", "a", limits, 60) is Admission.OK
        )  # already counted: refresh
        await store.leave("w1", "u1", "a")
        assert await store.admit("w1", "u1", "c", limits, 60) is Admission.OK

    run(make_store, scenario)


def test_a_workspace_or_user_named_like_a_counter_does_not_share_it(make_store):
    limits = Limits(total=100, per_workspace=1, per_user=1)

    async def scenario(store):
        assert await store.admit("w1", "u1", "a", limits, 60) is Admission.OK
        assert await store.admit("all", "all", "b", limits, 60) is Admission.OK
        assert await store.admit("-", "u1", "c", limits, 60) is Admission.OK

    run(make_store, scenario)


def test_admission_expires_when_a_session_stops_reporting(make_store):
    limits = Limits(total=1, per_workspace=1, per_user=1)

    async def scenario(store):
        assert await store.admit("w1", "u1", "a", limits, 1) is Admission.OK
        assert await store.admit("w1", "u1", "b", limits, 1) is not Admission.OK
        await asyncio.sleep(2.2)  # Redis counts whole seconds
        assert await store.admit("w1", "u1", "b", limits, 1) is Admission.OK

    run(make_store, scenario)


def test_a_late_report_does_not_bring_an_expired_session_back(make_store):
    async def scenario(store):
        await store.create(_record("gone"), 60)
        if hasattr(store, "_redis"):
            await store._redis.delete("nodo:voice:session:gone")  # as if its time had run out
        else:
            store._sessions.pop("gone")

        await store.progress("gone", 5000, 9)
        await store.close("gone")

        assert await store.get("gone") is None
        if hasattr(store, "_redis"):
            assert await store._redis.exists("nodo:voice:session:gone") == 0

    run(make_store, scenario)


def test_finals_are_idempotent_per_start_offset_and_ordered(make_store):
    async def scenario(store):
        await store.put_final("s1", 3000, '{"t":"second"}')
        await store.put_final("s1", 0, '{"t":"first"}')
        await store.put_final("s1", 3000, '{"t":"second-again"}')  # a resent stretch of audio

        assert await store.finals("s1") == ['{"t":"first"}', '{"t":"second-again"}']
        await store.forget("s1")
        assert await store.finals("s1") == []

    run(make_store, scenario)


def test_concurrent_lease_attempts_have_one_winner(make_store):
    async def scenario(store):
        results = await asyncio.gather(
            *(store.acquire_lease("s9", f"r{i}", 60_000) for i in range(20))
        )
        assert sum(results) == 1

    run(make_store, scenario)


def test_memory_store_clock_is_injectable():
    clock = [0.0]
    store = MemorySessionStore(clock=lambda: clock[0])

    async def scenario():
        assert await store.acquire_lease("s", "a", 1000)
        clock[0] = 2.0
        assert await store.acquire_lease("s", "b", 1000)

    asyncio.run(scenario())
    assert time.monotonic() > 0
