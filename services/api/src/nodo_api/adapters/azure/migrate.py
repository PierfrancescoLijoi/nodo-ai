"""Schema migrations and database roles, run by the bootstrap job as the Entra administrator.

Migrations are plain SQL files shipped inside the package, applied once and in order under an
advisory lock so two concurrent deploys cannot interleave. Data roles for the managed identities
are created with the Azure extension function `pgaadauth_create_principal` and receive table
rights only."""

from __future__ import annotations

from importlib import resources
from typing import Any

from psycopg import sql

LOCK_KEY = 7_240_001
AUDIT_TABLE = "audit_events"


def migration_files() -> list[tuple[int, str, str]]:
    root = resources.files("nodo_api.migrations")
    files = sorted(
        (item for item in root.iterdir() if item.name.endswith(".sql")), key=lambda i: i.name
    )
    return [(int(f.name.split("_", 1)[0]), f.name, f.read_text(encoding="utf-8")) for f in files]


def apply_migrations(conn: Any) -> list[str]:
    """Apply every migration that has not run yet and return the names applied."""
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version integer PRIMARY KEY, name text NOT NULL,"
        " applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {
        row["version"] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    applied = []
    for version, name, script in migration_files():
        if version in done:
            continue
        conn.execute(script)
        conn.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (version, name)
        )
        applied.append(name)
    return applied


def grant_roles(conn: Any, roles: tuple[str, ...], database: str) -> None:
    """Create a login role per managed identity (if missing) and grant data rights, not DDL."""
    for role in roles:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if not exists:
            conn.execute("SELECT * FROM pgaadauth_create_principal(%s, false, false)", (role,))
        ident = sql.Identifier(role)
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), ident)
        )
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident))
        conn.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
            ).format(ident)
        )
        # The archive cursor belongs to the audit job alone: moving it would hide events.
        conn.execute(sql.SQL("REVOKE ALL ON audit_archive_state FROM {}").format(ident))
        # The audit trail is append-only for the application roles.
        conn.execute(
            sql.SQL("REVOKE UPDATE, DELETE ON {} FROM {}").format(
                sql.Identifier(AUDIT_TABLE), ident
            )
        )


def grant_audit_roles(conn: Any, roles: tuple[str, ...], database: str) -> None:
    """The audit archive identity gets the role of migration 0009 and nothing else: it reads audit
    events across workspaces and keeps its own cursor."""
    for role in roles:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if not exists:
            conn.execute("SELECT * FROM pgaadauth_create_principal(%s, false, false)", (role,))
        ident = sql.Identifier(role)
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), ident)
        )
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident))
        conn.execute(sql.SQL("GRANT nodo_audit TO {}").format(ident))


def grant_maintenance_roles(conn: Any, roles: tuple[str, ...], database: str) -> None:
    """The index maintenance identity gets the role of migration 0007 and nothing else: it lists
    meetings across workspaces, and reads or repairs the rest one workspace at a time."""
    for role in roles:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if not exists:
            conn.execute("SELECT * FROM pgaadauth_create_principal(%s, false, false)", (role,))
        ident = sql.Identifier(role)
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), ident)
        )
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident))
        conn.execute(sql.SQL("GRANT nodo_maintenance TO {}").format(ident))


def grant_retention_roles(conn: Any, roles: tuple[str, ...], database: str) -> None:
    """The retention job's identity gets the cross-workspace role of migration 0004 and the right to
    append audit events, and no other table rights (it never touches transcripts or the graph)."""
    for role in roles:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if not exists:
            conn.execute("SELECT * FROM pgaadauth_create_principal(%s, false, false)", (role,))
        ident = sql.Identifier(role)
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), ident)
        )
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident))
        conn.execute(sql.SQL("GRANT nodo_retention TO {}").format(ident))
        conn.execute(sql.SQL("GRANT INSERT ON {} TO {}").format(sql.Identifier(AUDIT_TABLE), ident))
