"""SQLite access for the local backend.

One connection per thread (SQLite connections must not be shared), WAL mode so the API threads and
the job worker read while one of them writes, and explicit transactions: writes take the write lock
up front (BEGIN IMMEDIATE), so a read-then-write sequence inside one transaction can not interleave
with another writer. Transactions nest by joining the outer one."""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%f+00:00"
BUSY_TIMEOUT_MS = 30_000


def to_text(moment: datetime | None) -> str | None:
    """A fixed-width UTC string, so text comparison is time comparison."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        raise ValueError("Timestamps must carry a time zone")
    return moment.astimezone(UTC).strftime(TIME_FORMAT)


def from_text(value: str | None) -> datetime | None:
    return None if value is None else datetime.strptime(value, TIME_FORMAT).replace(tzinfo=UTC)


def now_text() -> str:
    return datetime.now(UTC).strftime(TIME_FORMAT)


class LocalDatabase:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._threads = threading.local()
        self._open: list[sqlite3.Connection] = []
        self._guard = threading.Lock()
        with self.transaction() as conn:
            conn.execute("SELECT 1")
        self._migrate()

    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._threads, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self._path,
                isolation_level=None,
                timeout=BUSY_TIMEOUT_MS / 1000,
                check_same_thread=False,  # each thread keeps its own; close() runs elsewhere
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            self._threads.conn = conn
            self._threads.depth = 0
            with self._guard:
                self._open.append(conn)
        return conn

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        conn = self.connection()
        if self._threads.depth > 0:
            self._threads.depth += 1
            try:
                yield conn
            finally:
                self._threads.depth -= 1
            return
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        self._threads.depth = 1
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
        finally:
            self._threads.depth = 0

    def _migrate(self) -> None:
        conn = self.connection()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        done = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
        for version, name, script in migration_files():
            if version in done:
                continue
            conn.executescript(
                f"BEGIN IMMEDIATE;\n{script}\n"
                f"INSERT INTO schema_migrations VALUES ({version}, '{name}', '{now_text()}');\n"
                "COMMIT;"
            )

    def close(self) -> None:
        with self._guard:
            for conn in self._open:
                with contextlib.suppress(sqlite3.ProgrammingError):
                    conn.close()
            self._open.clear()


def migration_files() -> list[tuple[int, str, str]]:
    root = resources.files("nodo_api.adapters.local").joinpath("schema")
    files = sorted((i for i in root.iterdir() if i.name.endswith(".sql")), key=lambda i: i.name)
    return [(int(f.name.split("_", 1)[0]), f.name, f.read_text(encoding="utf-8")) for f in files]
