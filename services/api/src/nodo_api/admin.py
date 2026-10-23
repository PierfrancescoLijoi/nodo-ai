"""Operator commands, run as a Container Apps job with the Entra administrator identity.

python -m nodo_api.admin migrate        apply SQL migrations
python -m nodo_api.admin roles          create and grant database roles for the app identities
python -m nodo_api.admin ensure-index   create or update the search index
python -m nodo_api.admin bootstrap      all three, idempotent (what the deploy pipeline runs)
python -m nodo_api.admin retention      erase audio whose retention period ended (daily job)
python -m nodo_api.admin verify-index   compare the search index and the graph with the store;
                                        exit 1 if they differ (weekly job, read only)
python -m nodo_api.admin reindex        repair the difference (after an outage or a failover)
python -m nodo_api.admin sweep          send again jobs nobody sent, restart meetings stuck
                                        in processing (every 30 minutes)
python -m nodo_api.admin audit-archive  copy audit events to the immutable account (hourly)
python -m nodo_api.admin preflight api|worker
                                        check this deployment end to end as that identity
                                        (after the first deployment and after any change)

The index commands also run against the local engine with NODO_BACKEND=local.
"""

from __future__ import annotations

import logging
import sys

from nodo_api.config import Settings

logger = logging.getLogger("nodo_admin")
COMMANDS = (
    "migrate",
    "roles",
    "ensure-index",
    "bootstrap",
    "retention",
    "verify-index",
    "reindex",
    "sweep",
    "audit-archive",
    "preflight",
)


def run(command: str, settings: Settings, argument: str | None = None) -> None:
    if command not in COMMANDS:
        raise SystemExit(f"Unknown command {command!r}. Use one of: {', '.join(COMMANDS)}")
    if command == "preflight":
        if settings.backend != "azure" or argument not in ("api", "worker"):
            raise SystemExit(
                "usage: NODO_BACKEND=azure python -m nodo_api.admin preflight api|worker"
            )
        _preflight(settings, argument)
        return
    if command == "audit-archive":
        if settings.backend != "azure":
            raise SystemExit("The audit archive needs NODO_BACKEND=azure")
        _audit_archive(settings)
        return
    if command == "sweep":
        if settings.backend not in ("azure", "local"):
            raise SystemExit("The sweep needs NODO_BACKEND=azure or local")
        _sweep(settings)
        return
    if command in ("verify-index", "reindex"):
        if settings.backend not in ("azure", "local"):
            raise SystemExit("The index commands need NODO_BACKEND=azure or local")
        _index(settings, repair=command == "reindex")
        return
    if settings.backend != "azure":
        raise SystemExit("The admin commands need NODO_BACKEND=azure")
    if command == "retention":
        _retention(settings)
        return
    wanted = {"migrate", "roles", "ensure-index"} if command == "bootstrap" else {command}
    credential = None
    if "migrate" in wanted or "roles" in wanted:
        from azure.identity import DefaultAzureCredential

        from nodo_api.adapters.azure.migrate import (
            apply_migrations,
            grant_audit_roles,
            grant_maintenance_roles,
            grant_retention_roles,
            grant_roles,
        )
        from nodo_api.bootstrap import postgres_database

        credential = DefaultAzureCredential()
        db = postgres_database(settings, credential)
        try:
            with db.admin() as conn:
                if "migrate" in wanted:
                    logger.info("migrations applied: %s", apply_migrations(conn) or "none")
                if "roles" in wanted:
                    grant_roles(conn, settings.azure_db_roles, settings.azure_postgres_database)
                    logger.info("roles granted: %s", ", ".join(settings.azure_db_roles))
                    grant_retention_roles(
                        conn, settings.azure_retention_roles, settings.azure_postgres_database
                    )
                    grant_maintenance_roles(
                        conn, settings.azure_maintenance_roles, settings.azure_postgres_database
                    )
                    grant_audit_roles(
                        conn, settings.azure_audit_roles, settings.azure_postgres_database
                    )
        finally:
            db.close()
    if "ensure-index" in wanted:
        from azure.identity import DefaultAzureCredential
        from azure.search.documents.indexes import SearchIndexClient

        from nodo_api.adapters.azure.search import ensure_index

        client = SearchIndexClient(
            settings.azure_search_endpoint, credential=credential or DefaultAzureCredential()
        )
        ensure_index(client, settings.search_index_name, settings.openai_embedding_dimensions)
        logger.info("search index ready: %s", settings.search_index_name)


def _preflight(settings: Settings, role: str) -> None:
    from nodo_api.preflight import Status, build_context, render, run_checks

    results = run_checks(build_context(settings, role))
    print(render(results))
    if any(r.status is Status.FAIL for r in results):
        raise SystemExit(1)


def _audit_archive(settings: Settings) -> None:
    """Hourly. A failure leaves the cursor where it was, so the next run continues; the job failing
    is the alert."""
    from nodo_api.bootstrap import build_audit_archive

    build_audit_archive(settings).run()


def _sweep(settings: Settings) -> None:
    """Every 30 minutes. A job it could not send makes the job fail, so an alert fires."""
    from nodo_api.bootstrap import build_adapters
    from nodo_api.services.sweep import SweepService

    a = build_adapters(settings)
    report = SweepService(a.maintenance, a.jobs, a.audit).run()
    if report.failed:
        raise SystemExit(1)


def _index(settings: Settings, *, repair: bool) -> None:
    """Weekly verify and on-demand reindex. Exits 1 when the index and the store still differ
    or when a meeting could not be repaired (reindex), so a failed job is the alert."""
    from nodo_api.bootstrap import build_adapters
    from nodo_api.services.index_maintenance import IndexMaintenanceService

    a = build_adapters(settings)
    service = IndexMaintenanceService(
        a.maintenance, a.transcripts, a.retriever, a.graph, a.jobs, a.audit
    )
    report = service.repair() if repair else service.verify()
    if report.failed or (not repair and not report.clean):
        raise SystemExit(1)


def _retention(settings: Settings) -> None:
    """The daily job. A meeting it could not clean makes the job fail, so an alert fires."""
    from nodo_api.bootstrap import build_adapters
    from nodo_api.services.retention import RetentionService

    adapters = build_adapters(settings)
    result = RetentionService(adapters.retention, adapters.blobs, adapters.audit).sweep_all()
    logger.info("retention erased=%s failed=%s", result.erased, result.failed)
    if result.failed:
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    args = argv if argv is not None else sys.argv[1:]
    if len(args) not in (1, 2) or (len(args) == 2 and args[0] != "preflight"):
        raise SystemExit(__doc__)
    run(args[0], Settings(service="admin"), args[1] if len(args) == 2 else None)


if __name__ == "__main__":
    main()
