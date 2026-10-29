"""Redis implementation of the session store (Azure Managed Redis in the cloud).

Every compound step is a Lua script, so two replicas can not interleave inside it, and the clock is
Redis's own (TIME): replica clocks do not have to agree for a lease to expire correctly.
"""

from __future__ import annotations

from typing import Any

from nodo_api.voice.store import MAX_FINALS, Admission, Limits, SessionRecord

PREFIX = "nodo:voice:"
SESSION_TTL_SECONDS = 6 * 3600

_ACQUIRE = """
local current = redis.call('GET', KEYS[1])
if not current then
  redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2]) return 1
end
if current == ARGV[1] then redis.call('PEXPIRE', KEYS[1], ARGV[2]) return 1 end
return 0
"""

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""

# A session that has expired is not recreated by a late report; a live one gets more time.
# ARGV: ttl seconds, then field and value pairs.
_PROGRESS = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
for index = 2, #ARGV, 2 do
  local field, value = ARGV[index], tonumber(ARGV[index + 1])
  if value > (tonumber(redis.call('HGET', KEYS[1], field)) or 0) then
    redis.call('HSET', KEYS[1], field, value)
  end
end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
return 1
"""

_CLOSE = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
redis.call('HSET', KEYS[1], 'closed', 1)
return 1
"""

# KEYS: global set, workspace set, user set. ARGV: session id, the three limits, ttl seconds.
_ADMIT = """
local now = tonumber(redis.call('TIME')[1])
for index = 1, 3 do redis.call('ZREMRANGEBYSCORE', KEYS[index], '-inf', now) end
if not redis.call('ZSCORE', KEYS[1], ARGV[1]) then
  if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 1 end
  if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[3]) then return 2 end
  if redis.call('ZCARD', KEYS[3]) >= tonumber(ARGV[4]) then return 3 end
end
local until_ = now + tonumber(ARGV[5])
for index = 1, 3 do
  redis.call('ZADD', KEYS[index], until_, ARGV[1])
  redis.call('EXPIRE', KEYS[index], tonumber(ARGV[5]) * 2)
end
return 0
"""

_FINAL = """
local known = redis.call('HEXISTS', KEYS[1], ARGV[1]) == 1
if not known and redis.call('HLEN', KEYS[1]) >= tonumber(ARGV[3]) then return 0 end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
local now = tonumber(redis.call('TIME')[1])
redis.call('ZADD', KEYS[2], now + tonumber(ARGV[6]), ARGV[5])
return 1
"""

# Moves the time a session with pending finals becomes eligible for recovery.
_DEFER = """
if not redis.call('ZSCORE', KEYS[1], ARGV[1]) then return 0 end
local now = tonumber(redis.call('TIME')[1])
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[1])
return 1
"""

_EVENT = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
redis.call('ZADD', KEYS[2], tonumber(ARGV[1]), ARGV[2])
local excess = redis.call('ZCARD', KEYS[2]) - tonumber(ARGV[3])
if excess > 0 then redis.call('ZREMRANGEBYRANK', KEYS[2], 0, excess - 1) end
local current = tonumber(redis.call('HGET', KEYS[1], 'last_sequence')) or 0
if tonumber(ARGV[1]) > current then
  redis.call('HSET', KEYS[1], 'last_sequence', tonumber(ARGV[1]))
end
local audio = tonumber(redis.call('HGET', KEYS[1], 'audio_ack_ms')) or 0
if tonumber(ARGV[5]) > audio then
  redis.call('HSET', KEYS[1], 'audio_ack_ms', tonumber(ARGV[5]))
end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4]))
return 1
"""

_ADMISSION = {
    0: Admission.OK,
    1: Admission.GLOBAL_FULL,
    2: Admission.WORKSPACE_FULL,
    3: Admission.USER_FULL,
}


class RedisSessionStore:
    def __init__(self, client: Any) -> None:
        """`client` is a redis.asyncio.Redis (decode_responses=True)."""
        self._redis = client
        self._acquire = client.register_script(_ACQUIRE)
        self._release = client.register_script(_RELEASE)
        self._progress = client.register_script(_PROGRESS)
        self._close = client.register_script(_CLOSE)
        self._admit = client.register_script(_ADMIT)
        self._final = client.register_script(_FINAL)
        self._event = client.register_script(_EVENT)
        self._defer = client.register_script(_DEFER)

    @classmethod
    def from_url(cls, url: str, max_connections: int = 64) -> RedisSessionStore:
        """A blocking pool: a burst of sessions waits for a connection instead of failing (the
        default pool raises MaxConnectionsError, which a load test showed under 120 sessions)."""
        from redis.asyncio import BlockingConnectionPool, Redis

        pool = BlockingConnectionPool.from_url(
            url,
            decode_responses=True,
            socket_timeout=2.0,
            max_connections=max_connections,
            timeout=2.0,
        )
        return cls(Redis(connection_pool=pool))

    async def aclose(self) -> None:
        await self._redis.aclose()

    async def ping(self) -> None:
        await self._redis.ping()

    @staticmethod
    def _key(kind: str, ident: str) -> str:
        return f"{PREFIX}{kind}:{ident}"

    async def create(self, record: SessionRecord, ttl_seconds: int) -> None:
        key = self._key("session", record.session_id)
        await self._redis.hset(
            key,
            mapping={
                "workspace_id": record.workspace_id,
                "user_id": record.user_id,
                "mode": record.mode,
                "locale": record.locale,
                "meeting_id": record.meeting_id or "",
                "audio_ack_ms": record.audio_ack_ms,
                "last_sequence": record.last_sequence,
                "closed": int(record.closed),
            },
        )
        await self._redis.expire(key, max(ttl_seconds, SESSION_TTL_SECONDS))

    async def get(self, session_id: str) -> SessionRecord | None:
        fields = await self._redis.hgetall(self._key("session", session_id))
        if not {"workspace_id", "user_id", "mode", "locale"} <= fields.keys():
            return None  # absent, or only the remains of a record that has expired
        return SessionRecord(
            session_id=session_id,
            workspace_id=fields["workspace_id"],
            user_id=fields["user_id"],
            mode=fields["mode"],
            locale=fields["locale"],
            meeting_id=fields["meeting_id"] or None,
            audio_ack_ms=int(fields.get("audio_ack_ms", 0)),
            last_sequence=int(fields.get("last_sequence", 0)),
            closed=fields.get("closed") == "1",
        )

    async def progress(self, session_id: str, audio_ack_ms: int, last_sequence: int) -> None:
        await self._progress(
            keys=[self._key("session", session_id)],
            args=[
                SESSION_TTL_SECONDS,
                "audio_ack_ms",
                audio_ack_ms,
                "last_sequence",
                last_sequence,
            ],
        )

    async def close(self, session_id: str) -> None:
        await self._close(keys=[self._key("session", session_id)])

    async def consume_ticket(self, jti: str, ttl_seconds: int) -> bool:
        return bool(await self._redis.set(self._key("ticket", jti), 1, nx=True, ex=ttl_seconds))

    async def acquire_lease(self, session_id: str, owner: str, ttl_ms: int) -> bool:
        return bool(
            await self._acquire(keys=[self._key("lease", session_id)], args=[owner, ttl_ms])
        )

    async def release_lease(self, session_id: str, owner: str) -> None:
        await self._release(keys=[self._key("lease", session_id)], args=[owner])

    def _active_keys(self, workspace_id: str, user_id: str) -> list[str]:
        # Distinct kinds, so no workspace or user id can ever name another set.
        return [
            self._key("active-all", "-"),
            self._key("active-ws", workspace_id),
            self._key("active-user", f"{workspace_id}:{user_id}"),
        ]

    async def admit(
        self,
        workspace_id: str,
        user_id: str,
        session_id: str,
        limits: Limits,
        ttl_seconds: int,
    ) -> Admission:
        code = await self._admit(
            keys=self._active_keys(workspace_id, user_id),
            args=[session_id, limits.total, limits.per_workspace, limits.per_user, ttl_seconds],
        )
        return _ADMISSION[int(code)]

    async def leave(self, workspace_id: str, user_id: str, session_id: str) -> None:
        for key in self._active_keys(workspace_id, user_id):
            await self._redis.zrem(key, session_id)

    async def put_final(
        self, session_id: str, start_ms: int, payload: str, due_in_seconds: int = 0
    ) -> None:
        stored = await self._final(
            keys=[self._key("finals", session_id), self._key("pending-finals", "-")],
            args=[start_ms, payload, MAX_FINALS, SESSION_TTL_SECONDS, session_id, due_in_seconds],
        )
        if not stored:
            raise OverflowError("voice final limit reached")

    async def finals(self, session_id: str) -> list[str]:
        phrases = await self._redis.hgetall(self._key("finals", session_id))
        return [phrases[start] for start in sorted(phrases, key=int)]

    async def forget(self, session_id: str) -> None:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(self._key("finals", session_id))
            pipe.delete(self._key("events", session_id))
            pipe.zrem(self._key("pending-finals", "-"), session_id)
            await pipe.execute()

    async def defer_finalization(self, session_id: str, seconds: int) -> None:
        await self._defer(keys=[self._key("pending-finals", "-")], args=[session_id, seconds])

    async def pending_finalizations(self, limit: int) -> list[str]:
        now = int((await self._redis.time())[0])
        due = await self._redis.zrangebyscore(
            self._key("pending-finals", "-"), "-inf", now, start=0, num=max(1, limit)
        )
        return list(due)

    async def put_event(
        self,
        session_id: str,
        sequence: int,
        payload: str,
        limit: int,
        audio_ack_ms: int = 0,
    ) -> None:
        stored = await self._event(
            keys=[self._key("session", session_id), self._key("events", session_id)],
            args=[sequence, payload, limit, SESSION_TTL_SECONDS, audio_ack_ms],
        )
        if not stored:
            raise RuntimeError("voice session expired")

    async def events_after(self, session_id: str, sequence: int) -> list[str]:
        return list(
            await self._redis.zrangebyscore(self._key("events", session_id), f"({sequence}", "+inf")
        )

    async def ack_events(self, session_id: str, sequence: int) -> None:
        await self._redis.zremrangebyscore(self._key("events", session_id), "-inf", sequence)
