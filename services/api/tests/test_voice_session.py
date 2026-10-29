"""A live voice session and the hub without a socket (docs/VOICE-REALTIME-ARCHITECTURE.md)."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass

import pytest

from nodo_api.domain.models import Answerability, Citation
from nodo_api.voice.assistant import Assistant
from nodo_api.voice.hub import SessionHub
from nodo_api.voice.live import CLOSE_BUSY, CLOSE_DRAINING, Busy, SessionState
from nodo_api.voice.protocol import ProtocolError
from nodo_api.voice.speech import ScriptedRecognizer, ToneSynthesizer
from nodo_api.voice.store import Admission, Limits, MemorySessionStore
from tests.voice_support import FakeConnection, FakeFinalizer, deps, frame, record, until


def run(coro):
    return asyncio.run(coro)


REDIS_URL = os.environ.get("NODO_TEST_REDIS_URL")


@pytest.fixture(params=["memory", "redis"])
def shared_store(request):
    """A factory for the store the replicas share (Redis too when NODO_TEST_REDIS_URL is set)."""
    if request.param == "redis" and not REDIS_URL:
        pytest.skip("NODO_TEST_REDIS_URL not set")

    async def make():
        if request.param == "memory":
            return MemorySessionStore()
        from nodo_api.voice.redis_store import RedisSessionStore

        store = RedisSessionStore.from_url(REDIS_URL)
        async for key in store._redis.scan_iter("nodo:voice:*"):
            await store._redis.delete(key)
        return store

    return make


async def feed(live, start_ms: int, total_ms: int, step: int = 500) -> None:
    for offset in range(start_ms, start_ms + total_ms, step):
        await live.on_audio(frame(offset, step))


async def started(hub: SessionHub, rec=None, conn=None, last_ack: int = 0):
    conn = conn or FakeConnection()
    return await hub.attach(rec or record(), conn, last_ack), conn


# ---------------------------------------------------------------------------- live transcription


def test_partials_are_ephemeral_and_finals_are_sequenced_stored_and_acknowledged_in_order():
    async def scenario():
        d = deps()
        hub = SessionHub(d)
        live, conn = await started(hub)

        await feed(live, 0, 3200)
        await until(lambda: len(conn.of("transcript.final")) == 2)

        partials = conn.of("transcript.partial")
        assert partials and all("sequence" not in p for p in partials)
        finals = conn.of("transcript.final")
        assert [f["sequence"] for f in finals] == [1, 2]
        assert (finals[0]["audio_offset_start_ms"], finals[0]["audio_offset_end_ms"]) == (0, 1500)
        assert finals[0]["text"] == "we should deploy on monday"
        stored = [json.loads(item) for item in await d.store.finals("s1")]
        assert [item["start_ms"] for item in stored] == [0, 1500]

    run(scenario())


def test_a_final_is_stored_before_the_client_is_told_about_it():
    async def scenario():
        d = deps()
        await d.store.create(record(), 60)
        order = []
        original = d.store.put_final

        async def spy(session_id, start_ms, payload):
            order.append("stored")
            await original(session_id, start_ms, payload)

        d.store.put_final = spy
        hub = SessionHub(d)
        live, conn = await started(hub)
        await feed(live, 0, 1500)
        await until(lambda: conn.of("transcript.final"))

        assert order == ["stored"]
        assert (await d.store.get("s1")).audio_ack_ms == 1500

    run(scenario())


def test_resume_on_the_same_replica_replays_only_what_was_not_acknowledged():
    async def scenario():
        hub = SessionHub(deps())
        live, conn = await started(hub)
        await feed(live, 0, 4500)
        await until(lambda: len(conn.of("transcript.final")) == 2)
        live.acknowledge(1)
        live.detach(conn)
        assert live.state is SessionState.RECONNECTING

        again = FakeConnection()
        resumed, _ = await started(hub, conn=again, last_ack=1)

        assert resumed is live and live.state is SessionState.ACTIVE
        ready = again.events[0]
        assert ready["type"] == "session.ready" and ready["resumed"] and ready["replayed"] == 1
        assert [e["sequence"] for e in again.events[1:]] == [2]

    run(scenario())


def test_resent_audio_after_a_reconnect_is_not_counted_twice():
    async def scenario():
        d = deps()
        hub = SessionHub(d)
        live, _ = await started(hub)
        pushed = []
        await live.on_audio(frame(0, 1000))
        live._recognizer.push = lambda data, _p=live._recognizer.push: (
            pushed.append(len(data)),
            _p(data),
        )

        await live.on_audio(frame(500, 1000))  # overlaps 500 ms already received
        await live.on_audio(frame(0, 1000))  # entirely duplicate

        assert pushed == [500 * 32]  # only the new half second reaches the recognizer
        assert live.duplicate_ms == 500 + 1000
        assert live._next_offset == 1500

    run(scenario())


def test_grace_expiry_hands_the_stored_finals_to_the_finalizer_and_closes():
    async def scenario():
        finalizer = FakeFinalizer()
        d = deps(finalizer=finalizer, reconnect_grace_seconds=0.05)
        await d.store.create(record(), 60)
        hub = SessionHub(d)
        live, conn = await started(hub)
        await feed(live, 0, 3000)
        await until(lambda: len(conn.of("transcript.final")) == 2)

        live.detach(conn)
        await until(lambda: live.state is SessionState.CLOSED)

        ((rec, finals),) = finalizer.calls
        assert rec.meeting_id == "m1" and [f["text"] for f in finals] == [
            "we should deploy on monday",
            "sara owns the benchmark",
        ]
        assert hub.get("s1") is None
        assert await d.store.finals("s1") == []  # handed over, so forgotten
        assert (await d.store.get("s1")).closed

    run(scenario())


def test_ending_the_session_flushes_the_last_partial_phrase_as_a_final():
    async def scenario():
        finalizer = FakeFinalizer()
        d = deps(finalizer=finalizer)
        hub = SessionHub(d)
        live, conn = await started(hub)
        await feed(live, 0, 2000)  # 1500 final + 500 ms of the second phrase
        await live.end()

        closed = conn.of("session.closed")[0]
        assert closed["reason"] == "client_end" and closed["persisted"] is True
        assert [f["text"] for f in finalizer.calls[0][1]] == [
            "we should deploy on monday",
            "sara owns the benchmark",
        ]
        assert conn.closed == 1000

    run(scenario())


def test_a_failing_finalizer_keeps_the_finals_and_tells_the_client():
    async def scenario():
        class Broken:
            async def finalize(self, *_):
                raise RuntimeError("database down")

        d = deps(finalizer=Broken())
        hub = SessionHub(d)
        live, conn = await started(hub)
        await feed(live, 0, 1500)
        await live.end()

        assert conn.of("session.closed")[0]["persisted"] is False
        assert any(e.get("code") == "persist_failed" for e in conn.of("error"))
        assert await d.store.finals("s1")  # still there for a retry

    run(scenario())


# ---------------------------------------------------------------------------- replicas


def test_a_second_replica_takes_over_from_the_stored_progress_without_duplicating_phrases(
    shared_store,
):
    async def scenario():
        store = await shared_store()
        finalizer = FakeFinalizer()
        first = SessionHub(deps(store=store, replica="r1", finalizer=finalizer))
        second = SessionHub(
            deps(["a new phrase after the move"], store=store, replica="r2", finalizer=finalizer)
        )
        await store.create(record(), 60)
        live, conn = await started(first)
        await feed(live, 0, 3000)
        await until(lambda: len(conn.of("transcript.final")) == 2)
        await first.drain()
        assert conn.closed == CLOSE_DRAINING and first.draining

        moved, again = await started(second, last_ack=2)

        ready = again.events[0]
        assert ready["audio_resume_offset_ms"] == 3000 and ready["resumed"]
        assert ready["next_sequence"] == 3
        await feed(moved, 3000, 1500)
        await moved.end()
        texts = [f["text"] for f in finalizer.calls[-1][1]]
        assert texts == [
            "we should deploy on monday",
            "sara owns the benchmark",
            "a new phrase after the move",
        ]
        assert again.of("transcript.final")[0]["audio_offset_start_ms"] == 3000

    run(scenario())


def test_a_session_held_by_another_replica_is_refused_until_its_lease_lapses(shared_store):
    async def scenario():
        store = await shared_store()
        first = SessionHub(deps(store=store, replica="r1"))
        second = SessionHub(deps(store=store, replica="r2"))
        await started(first)

        with pytest.raises(Busy) as busy:
            await started(second)

        assert busy.value.code == "lease_busy"

    run(scenario())


def test_a_replica_refuses_sessions_over_its_limit_and_while_draining():
    async def scenario():
        hub = SessionHub(deps(max_sessions_per_replica=1))
        await started(hub)
        with pytest.raises(Busy) as full:
            await started(hub, record(session_id="s2"))
        assert full.value.code == "replica_full"

        await hub.drain()
        with pytest.raises(Busy) as draining:
            await hub.admit(record(session_id="s3"))
        assert draining.value.code == "draining"

    run(scenario())


def test_admission_limits_are_shared_through_the_store(shared_store):
    async def scenario():
        store = await shared_store()
        hub = SessionHub(deps(store=store, max_sessions_per_workspace=1))
        await hub.admit(record(session_id="a"))
        with pytest.raises(Busy) as busy:
            await SessionHub(deps(store=store, replica="r2", max_sessions_per_workspace=1)).admit(
                record(session_id="b")
            )
        assert busy.value.code == "workspace_full"

    run(scenario())


def test_losing_the_lease_stops_the_session_without_finalizing_it():
    async def scenario():
        store = MemorySessionStore()
        finalizer = FakeFinalizer()
        hub = SessionHub(deps(store=store, finalizer=finalizer))
        live, conn = await started(hub)
        await store.release_lease("s1", "r1")
        await store.acquire_lease("s1", "r2", 60_000)

        await live.heartbeat()

        assert conn.closed == CLOSE_DRAINING and not finalizer.calls
        assert live.state is SessionState.DRAINING and hub.get("s1") is None

    run(scenario())


# ---------------------------------------------------------------------------- protection


def test_audio_faster_than_the_budget_is_refused():
    async def scenario():
        hub = SessionHub(deps(audio_burst_seconds=5))
        live, _ = await started(hub)
        with pytest.raises(ProtocolError) as error:
            await feed(live, 0, 20_000, step=1000)  # 20 s in no time against a 5 s budget
        assert error.value.code == "audio.too_fast"

    run(scenario())


def test_a_client_that_can_not_keep_up_is_cut_off_and_recovers_the_events_on_resume():
    async def scenario():
        hub = SessionHub(deps())
        slow = FakeConnection(capacity=3)  # session.ready + two events
        live, _ = await started(hub, conn=slow)
        await feed(live, 0, 4500)
        await until(lambda: slow.closed == CLOSE_BUSY)
        assert live.state is SessionState.RECONNECTING

        fresh = FakeConnection()
        await started(hub, conn=fresh, last_ack=0)

        assert [e["sequence"] for e in fresh.events[1:]] == [1, 2]

    run(scenario())


def test_events_older_than_the_replay_buffer_are_reported_as_lost():
    async def scenario():
        hub = SessionHub(deps(replay_events=16))
        live, conn = await started(hub)
        for index in range(40):
            live.emit("assistant.text", turn=1, text=str(index))
        live.detach(conn)

        again = FakeConnection()
        await started(hub, conn=again, last_ack=3)

        assert again.events[0]["events_lost"] is True

    run(scenario())


def test_the_session_ends_by_itself_after_its_maximum_duration():
    async def scenario():
        d = deps(max_session_seconds=60)
        hub = SessionHub(d)
        live, conn = await started(hub)
        live._started -= 61

        await live.heartbeat()

        assert conn.of("session.closed")[0]["reason"] == "max_duration"

    run(scenario())


# ---------------------------------------------------------------------------- push to talk


@dataclass
class Answer:
    answer: str = "The queue decision was taken on Monday. Sara owns the benchmark now."
    answerability: Answerability = Answerability.SUPPORTED
    degraded: bool = False
    citations: tuple = (Citation("m1", "seg-00001", 0, 1500, "move to a queue", "S1"),)


def assistant(answer: Answer | None = None, delay: float = 0.0, fail: bool = False) -> Assistant:
    async def ask(workspace, user, question, locale):
        assert (workspace, user, locale) == ("w1", "u1", "en")
        assert question == "we should deploy on monday"
        if fail:
            raise RuntimeError("model down")
        return answer or Answer()

    return Assistant(ask, ToneSynthesizer(chunk_delay_s=delay))


async def ask_aloud(live) -> None:
    await live.ptt_start(0)
    await feed(live, 0, 1500)
    await live.ptt_end()


def test_push_to_talk_answers_with_citations_text_and_audio_in_order():
    async def scenario():
        hub = SessionHub(deps(assistant=assistant()))
        live, conn = await started(hub, record("assistant"))

        await ask_aloud(live)
        await until(lambda: conn.of("assistant.done"))

        kinds = [
            e["type"]
            for e in conn.events
            if e["type"].startswith(("transcript.final", "assistant"))
        ]
        assert kinds[:3] == ["transcript.final", "assistant.thinking", "assistant.answer"]
        assert kinds[-1] == "assistant.done" and "assistant.audio_start" in kinds
        answer = conn.of("assistant.answer")[0]
        assert (
            answer["answerability"] == "supported"
            and answer["citations"][0]["segment_id"] == "seg-00001"
        )
        assert len(conn.audio) > 0 and [i for _, i, _ in conn.audio] == list(range(len(conn.audio)))

    run(scenario())


def test_audio_outside_the_push_to_talk_window_is_ignored():
    async def scenario():
        hub = SessionHub(deps(assistant=assistant()))
        live, conn = await started(hub, record("assistant"))

        await feed(live, 0, 2000)

        assert live._recognizer is None and not conn.of("transcript.partial")

    run(scenario())


def test_an_unanswerable_question_is_spoken_in_the_session_language():
    async def scenario():
        gap = Answer(answer="", answerability=Answerability.UNANSWERABLE, citations=())
        hub = SessionHub(deps(assistant=assistant(gap)))
        live, conn = await started(hub, record("assistant"))

        await ask_aloud(live)
        await until(lambda: conn.of("assistant.done"))

        assert conn.of("assistant.text")[0]["text"] == "I did not find that in your meetings."

    run(scenario())


def test_when_the_answer_fails_the_user_hears_it_and_the_session_survives():
    async def scenario():
        hub = SessionHub(deps(assistant=assistant(fail=True)))
        live, conn = await started(hub, record("assistant"))

        await ask_aloud(live)
        await until(lambda: conn.of("assistant.done"))

        assert conn.of("assistant.error")[0]["retryable"] is True
        assert live.state is SessionState.ACTIVE and conn.audio

    run(scenario())


def test_barge_in_cancels_the_turn_stops_the_audio_and_does_so_quickly():
    async def scenario():
        long_answer = Answer(answer=" ".join(["A sentence that takes a while to say aloud."] * 30))
        hub = SessionHub(deps(assistant=assistant(long_answer, delay=0.02)))
        live, conn = await started(hub, record("assistant"))
        await ask_aloud(live)
        await until(lambda: len(conn.audio) >= 3)

        begun = time.monotonic()
        await live.barge_in()
        stopped_ms = (time.monotonic() - begun) * 1000
        heard = len(conn.audio)
        await asyncio.sleep(0.15)

        assert conn.of("assistant.cancelled") and not conn.of("assistant.done")
        assert conn.purged == 1 and len(conn.audio) == heard
        assert stopped_ms < 300

    run(scenario())


def test_pressing_the_key_again_while_the_assistant_talks_interrupts_it():
    async def scenario():
        long_answer = Answer(answer=" ".join(["A sentence that takes a while to say aloud."] * 30))
        hub = SessionHub(deps(assistant=assistant(long_answer, delay=0.02)))
        live, conn = await started(hub, record("assistant"))
        await ask_aloud(live)
        await until(lambda: len(conn.audio) >= 2)

        await live.ptt_start(2000)

        assert conn.of("assistant.cancelled")

    run(scenario())


def test_push_to_talk_without_speech_says_so_and_never_calls_the_assistant():
    async def scenario():
        hub = SessionHub(deps(assistant=assistant()))
        live, conn = await started(hub, record("assistant"))

        await live.ptt_start(0)
        await live.ptt_end()

        assert conn.of("error")[0]["code"] == "ptt.empty" and not conn.of("assistant.thinking")

    run(scenario())


def test_push_to_talk_is_not_available_in_a_meeting_session():
    async def scenario():
        hub = SessionHub(deps())
        live, _ = await started(hub)
        with pytest.raises(ProtocolError):
            await live.ptt_start(0)

    run(scenario())


def test_a_store_that_fails_on_release_still_lets_the_replica_forget_the_session():
    async def scenario():
        d = deps()

        async def broken(*_args):
            raise ConnectionError("redis gone")

        d.store.release_lease = broken
        hub = SessionHub(d)
        _, conn = await started(hub)

        await hub.drain()

        assert hub.get("s1") is None and conn.closed == CLOSE_DRAINING

    run(scenario())


# ---------------------------------------------------------------------------- hardening


def test_the_server_keeps_the_lease_alive_and_ends_the_session_without_any_ping():
    async def scenario():
        store = MemorySessionStore()
        finalizer = FakeFinalizer()
        d = deps(store=store, finalizer=finalizer, heartbeat_seconds=0.05, max_session_seconds=0.45)
        await store.create(record(), 60)
        hub = SessionHub(d)
        live, conn = await started(hub)
        await feed(live, 0, 1500)

        await asyncio.sleep(0.3)
        assert not await store.acquire_lease("s1", "r2", 1000)  # renewed by the timer, not a ping

        await until(lambda: conn.of("session.closed"), timeout=1.5)
        assert conn.of("session.closed")[0]["reason"] == "max_duration"
        assert finalizer.calls

    run(scenario())


def test_a_heartbeat_after_the_session_has_ended_takes_nothing_back():
    async def scenario():
        store = MemorySessionStore()
        hub = SessionHub(deps(store=store))
        live, _ = await started(hub)
        await live.end()

        await live.heartbeat()

        assert await store.acquire_lease("s1", "r2", 1000)
        assert await store.admit("w1", "u1", "other", Limits(1, 1, 1), 60) is Admission.OK

    run(scenario())


def test_a_failing_finalization_still_frees_the_lease_the_slot_and_the_hub():
    async def scenario():
        store = MemorySessionStore()

        async def broken(_session_id):
            raise ConnectionError("redis gone")

        store.finals = broken
        hub = SessionHub(deps(store=store, finalizer=FakeFinalizer()))
        await hub.admit(record())
        live, _ = await started(hub)

        await live.end()

        assert hub.get("s1") is None and live.state is SessionState.CLOSED
        assert await store.acquire_lease("s1", "r2", 1000)
        assert await store.admit("w1", "u1", "s2", Limits(1, 1, 1), 60) is Admission.OK

    run(scenario())


def test_two_connections_for_one_session_share_one_live_session():
    async def scenario():
        hub = SessionHub(deps())
        first, second = FakeConnection(), FakeConnection()

        a, b = await asyncio.gather(hub.attach(record(), first, 0), hub.attach(record(), second, 0))

        assert a is b and hub.active == 1
        assert first.closed == 4000  # the older socket is replaced, not orphaned
        a.detach(second)
        await a.finalize("test")
        assert hub.active == 0

    run(scenario())


def test_a_session_that_is_being_suspended_can_not_be_attached():
    async def scenario():
        hub = SessionHub(deps())
        live, _ = await started(hub)
        live.state = SessionState.DRAINING

        with pytest.raises(Busy) as busy:
            await live.attach(FakeConnection(), 0)

        assert busy.value.code == "draining"

    run(scenario())


def test_one_person_can_not_hold_every_slot_of_the_workspace():
    async def scenario():
        hub = SessionHub(deps(max_sessions_per_user=1))
        await hub.admit(record(session_id="a"))

        with pytest.raises(Busy) as busy:
            await hub.admit(record(session_id="b"))
        await hub.admit(record(session_id="c", user_id="u2"))

        assert busy.value.code == "user_full"

    run(scenario())


def test_sockets_waiting_for_their_hello_are_limited():
    hub = SessionHub(deps(max_pending_hello=2))

    assert [hub.begin_hello() for _ in range(3)] == [True, True, False]
    hub.end_hello()
    assert hub.begin_hello() is True


def test_a_recognizer_that_fails_to_stop_is_reported_and_the_session_still_ends():
    async def scenario():
        class Failing(ScriptedRecognizer):
            async def stop(self):
                raise RuntimeError("provider down")

        d = deps()
        d.recognizer_factory = lambda _record: Failing(["a phrase"])
        hub = SessionHub(d)
        live, conn = await started(hub)
        await feed(live, 0, 500)

        await live.end()

        assert any(e.get("code") == "recognizer_failed" for e in conn.of("error"))
        assert conn.of("session.closed") and hub.get("s1") is None

    run(scenario())
