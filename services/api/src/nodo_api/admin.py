"""Operator commands, run as a Container Apps job with the Entra administrator identity.

python -m nodo_api.admin migrate        apply SQL migrations
python -m nodo_api.admin roles          create and grant database roles for the app identities
python -m nodo_api.admin ensure-index   create or update the search index
python -m nodo_api.admin bootstrap      all three, idempotent (what the deploy pipeline runs)
"""

from __future__ import annotations

import logging
import sys

from nodo_api.config import Settings

logger = logging.getLogger("nodo_admin")
COMMANDS = ("migrate", "roles", "ensure-index", "bootstrap")


def run(command: str, settings: Settings) -> None:
    if command not in COMMANDS:
        raise SystemExit(f"Unknown command {command!r}. Use one of: {', '.join(COMMANDS)}")
    if settings.backend != "azure":
        raise SystemExit("The admin commands need NODO_BACKEND=azure")
    wanted = {"migrate", "roles", "ensure-index"} if command == "bootstrap" else {command}
    credential = None
    if "migrate" in wanted or "roles" in wanted:
        from azure.identity import DefaultAzureCredential

        from nodo_api.adapters.azure.migrate import apply_migrations, grant_roles
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


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        raise SystemExit(__doc__)
    run(args[0], Settings(service="admin"))


if __name__ == "__main__":
    main()
