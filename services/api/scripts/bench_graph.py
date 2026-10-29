"""Graph traversal benchmark against a throwaway PostgreSQL (it drops the public schema).

    NODO_BENCH_DSN=postgresql://postgres:postgres@127.0.0.1:55432/postgres \
        python scripts/bench_graph.py [entities] [relations]

One large owner (skewed degree, so a few hubs) next to many small workspaces. Prints latency
percentiles for the calls the app makes and the plan of the traversal query, so a missing index
shows up as a sequential scan.
"""

from __future__ import annotations

import os
import statistics
import sys
import time

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from nodo_api.adapters.azure.graph_pg import _LEVEL_SQL, PgGraphStore
from nodo_api.adapters.azure.migrate import apply_migrations
from nodo_api.adapters.azure.postgres import PostgresDatabase

BIG, OWNER, ROLE, PASSWORD = "ws-big", "u1", "nodo_bench_app", "bench-password"
SMALL_WORKSPACES = 40


def load(db: PostgresDatabase, entities: int, relations: int) -> None:
    with db.admin() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute(f"DROP ROLE IF EXISTS {ROLE}")
        conn.execute(f"CREATE ROLE {ROLE} LOGIN PASSWORD '{PASSWORD}'")
    with db.admin() as conn:
        apply_migrations(conn)
        conn.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
        conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROLE}"
        )
        for workspace, owner, n_ent, n_rel in [
            (BIG, OWNER, entities, relations),
            *[(f"ws-{i}", "u1", 2000, 8000) for i in range(SMALL_WORKSPACES)],
        ]:
            conn.execute(
                "INSERT INTO entities (workspace_id, owner_id, id, type, label, normalized)"
                " SELECT %(w)s, %(o)s, md5(%(w)s || n::text)::uuid,"
                " (ARRAY['person','project','system','topic','decision','action'])[1 + n %% 6],"
                " 'Entity ' || n, 'entity ' || n FROM generate_series(1, %(n)s) n",
                {"w": workspace, "o": owner, "n": n_ent},
            )
            # target index is skewed towards low numbers: a few hubs with very high degree
            conn.execute(
                "INSERT INTO relations (workspace_id, owner_id, id, source_id, target_id, kind, status,"
                " confidence) SELECT %(w)s, %(o)s, md5(%(w)s || 'r' || n::text)::uuid,"
                " md5(%(w)s || s::text)::uuid, md5(%(w)s || t::text)::uuid,"
                " (ARRAY['ABOUT','DEPENDS_ON','ASSIGNED_TO','PROPOSED_BY'])[1 + n %% 4],"
                " CASE WHEN n %% 10 = 0 THEN 'proposed' ELSE 'approved' END, random()"
                " FROM (SELECT n, 1 + (random() * (%(e)s - 1))::int AS s,"
                " 1 + ((random() ^ 3) * (%(e)s - 1))::int AS t FROM generate_series(1, %(n)s) n) g"
                " WHERE s <> t",
                {"w": workspace, "o": owner, "e": n_ent, "n": n_rel},
            )
        conn.execute(
            "INSERT INTO relation_evidence SELECT workspace_id, owner_id, id, 'm' || (hashtext(id::text) % 500),"
            " 'seg-1' FROM relations"
        )
        conn.execute("ANALYZE")


def timed(fn, runs: int = 100) -> str:
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    return f"p50 {statistics.median(samples):7.2f} ms   p95 {samples[int(runs * 0.95) - 1]:7.2f} ms"


def main() -> None:
    entities = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000
    relations = int(sys.argv[2]) if len(sys.argv) > 2 else 1_000_000
    dsn = os.environ["NODO_BENCH_DSN"]
    admin = PostgresDatabase.from_dsn(dsn)
    started = time.perf_counter()
    if not os.environ.get("NODO_BENCH_SKIP_LOAD"):
        load(admin, entities, relations)
    print(
        f"loaded {entities} entities and {relations} relations (+ noise) in {time.perf_counter() - started:.0f}s"
    )
    params = conninfo_to_dict(dsn)
    params.update(user=ROLE, password=PASSWORD)
    app = PostgresDatabase.from_dsn(make_conninfo(**params))
    store = PgGraphStore(app)

    with admin.admin() as conn:
        sizes = conn.execute(
            "SELECT pg_size_pretty(sum(pg_total_relation_size(c.oid))) AS size FROM pg_class c"
            " WHERE c.relname ~ '^(entities|relations|relation_evidence)_p'"
        ).fetchone()
    print("graph tables incl. indexes:", sizes["size"])

    hub = store.find_entities(BIG, OWNER, ["entity 1"], 1)[0].id
    mid = store.find_entities(BIG, OWNER, [f"entity {entities // 2}"], 1)[0].id
    kwargs = dict(node_limit=60, fanout=15, include_proposed=False, as_of=None)
    print()
    print(
        "find_entities (substring)  ",
        timed(lambda: store.find_entities(BIG, OWNER, ["entity 1234"], 5)),
    )
    print("overview (60)              ", timed(lambda: store.overview(BIG, OWNER, 60)))
    print(
        "depth 1 from the biggest hub",
        timed(lambda: store.neighborhood(BIG, OWNER, [hub], depth=1, **kwargs)),
    )
    print(
        "depth 2 from the biggest hub",
        timed(lambda: store.neighborhood(BIG, OWNER, [hub], depth=2, **kwargs)),
    )
    print(
        "depth 2 from an ordinary node",
        timed(lambda: store.neighborhood(BIG, OWNER, [mid], depth=2, **kwargs)),
    )
    sub = store.neighborhood(BIG, OWNER, [hub], depth=2, **kwargs)
    print(
        f"  hub subgraph: {len(sub.nodes)} nodes, {len(sub.edges)} edges, truncated={sub.truncated}"
    )

    with app.workspace(BIG) as conn:
        frontier = [hub, mid]
        plan = conn.execute(
            "EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) " + _LEVEL_SQL,
            {
                "frontier": frontier,
                "ws": BIG,
                "owner": OWNER,
                "statuses": ["approved"],
                "at": __import__("datetime").datetime.now(__import__("datetime").UTC),
                "lim": 16,
            },
        ).fetchall()
    text = "\n".join(next(iter(row.values())) for row in plan)
    print("\ntraversal plan:\n" + text)
    print("\nsequential scans in plan:", "Seq Scan" in text)


if __name__ == "__main__":
    main()
