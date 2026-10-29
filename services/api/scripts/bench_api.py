"""API efficiency benchmark: HTTP latency of the app itself and the meeting queries on a big table.

    NODO_BENCH_DSN=postgresql://postgres:postgres@127.0.0.1:55432/postgres \
        python scripts/bench_api.py [meetings]

Part 1 starts the real server (uvicorn, in-memory adapters, demo data) and measures end-to-end HTTP
latency with 1 and 16 concurrent clients, so it shows the cost of FastAPI, auth and validation and
nothing else. Part 2 (only with NODO_BENCH_DSN, drops the public schema) loads many meetings for
one user and times the repository calls the app makes on every request.
"""

from __future__ import annotations

import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx
import uvicorn

from nodo_api.config import Settings
from nodo_api.main import create_app

TOKEN = "bench-dev-token-0123456789"
PORT = 8765


def percentiles(samples: list[float]) -> str:
    samples = sorted(samples)
    p95 = samples[max(int(len(samples) * 0.95) - 1, 0)]
    return f"p50 {statistics.median(samples):6.2f} ms   p95 {p95:6.2f} ms"


def http_part() -> None:
    settings = Settings(
        environment="test",
        auth_mode="dev",
        seed_demo_data=True,
        dev_token=TOKEN,
        dev_user_id="user-a",
        dev_workspace_id="workspace-a",
        attestation_hmac_key="bench-only-attestation-key-0123456789",
    )
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), port=PORT, log_level="error", access_log=False)
    )
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    calls = {
        "GET /healthz": ("GET", "/healthz", None),
        "GET /v1/meetings": ("GET", "/v1/meetings?limit=20", None),
        "GET /v1/meetings/{id}": ("GET", "/v1/meetings/seed-m3", None),
        "GET /v1/graph/subgraph": ("GET", "/v1/graph/subgraph?depth=2", None),
        "POST /v1/query": (
            "POST",
            "/v1/query",
            {"question": "What changed in ingestion?", "ui_locale": "en"},
        ),
    }
    base = f"http://127.0.0.1:{PORT}"
    for label, (method, path, body) in calls.items():
        for clients, total in ((1, 300), (16, 1600)):
            # a client per request would measure TCP setup; reuse one connection per worker instead
            local = threading.local()

            def pooled(_: int, method=method, path=path, body=body) -> float:
                if not hasattr(local, "client"):
                    local.client = httpx.Client(base_url=base, headers=headers)
                start = time.perf_counter()
                response = local.client.request(method, path, json=body)
                assert response.status_code == 200, (path, response.status_code)
                return (time.perf_counter() - start) * 1000

            for _ in range(30):
                pooled(0)
            started = time.perf_counter()
            with ThreadPoolExecutor(clients) as pool:
                samples = list(pool.map(pooled, range(total)))
            rps = total / (time.perf_counter() - started)
            print(f"{label:26} c={clients:<2} {percentiles(samples)}   {rps:6.0f} req/s")
    server.should_exit = True


def db_part(count: int) -> None:
    from nodo_api.adapters.azure.migrate import apply_migrations
    from nodo_api.adapters.azure.postgres import PgMeetingRepository, PostgresDatabase

    admin = PostgresDatabase.from_dsn(os.environ["NODO_BENCH_DSN"])
    with admin.admin() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    with admin.admin() as conn:
        apply_migrations(conn)
        started = time.perf_counter()
        conn.execute(
            "INSERT INTO meetings (workspace_id, id, owner_id, state, created_at, title, duration_ms)"
            " SELECT 'ws', 'm' || n, CASE WHEN n %% 10 = 0 THEN 'bob' ELSE 'alice' END, 'ready',"
            " now() - make_interval(secs => n), 'Meeting ' || n, 3600000 FROM generate_series(1, %s) n",
            (count,),
        )
        conn.execute(
            "INSERT INTO recording_attestations (workspace_id, meeting_id, user_id, notice_version,"
            " locale, retention_policy_id, acknowledged_at, audit_event_id, signature)"
            " SELECT workspace_id, id, owner_id, 'v1', 'en', 'r', created_at, 'a', 's' FROM meetings",
        )
        conn.execute("ANALYZE")
    print(f"loaded {count} meetings in {time.perf_counter() - started:.0f}s")
    repo = PgMeetingRepository(admin)
    deep = (datetime(2020, 1, 1, tzinfo=UTC), "m0")
    page = repo.list_owned("ws", "alice", 20, None)
    last = (page[-1].created_at, page[-1].id)
    # the deepest page: the cursor of the oldest meetings
    oldest = repo.list_owned("ws", "alice", 1, (datetime.now(UTC), "zzz"))[0]
    print("\nrepository calls on one owner")
    for label, call in {
        "list first page (20)": lambda: repo.list_owned("ws", "alice", 20, None),
        "list page after a cursor": lambda: repo.list_owned("ws", "alice", 20, last),
        "list page at the very end": lambda: repo.list_owned("ws", "alice", 20, deep),
        "get_owned": lambda: repo.get_owned("ws", "alice", oldest.id),
        "owned_ids (all ids)": lambda: repo.owned_ids("ws", "alice"),
    }.items():
        samples = []
        for _ in range(20):
            start = time.perf_counter()
            call()
            samples.append((time.perf_counter() - start) * 1000)
        print(f"{label:28} {percentiles(samples)}")


if __name__ == "__main__":
    http_part()
    if os.environ.get("NODO_BENCH_DSN"):
        db_part(int(sys.argv[1]) if len(sys.argv) > 1 else 100_000)
