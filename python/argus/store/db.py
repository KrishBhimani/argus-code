"""SQLite connection opener + migration runner.

Verifies FTS5 is compiled in at startup; failing fast with a clear error
is better than a confusing OperationalError deep in searchPrompts later.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .migrations.inline import (
    MIGRATION_001,
    MIGRATION_002,
    MIGRATION_003,
    MIGRATION_004,
    MIGRATION_005,
    MIGRATION_006,
    MIGRATION_007,
)

SCHEMA_VERSION = 7


class FTS5NotAvailableError(RuntimeError):
    """Raised when the Python sqlite3 build lacks FTS5 support."""


def _assert_fts5(conn: sqlite3.Connection) -> None:
    """Fail loudly if FTS5 isn't compiled into this Python's sqlite3.

    CPython's standard distributions for Windows / macOS / Linux all ship
    FTS5 since 3.11. Alpine minimal builds and some custom builds don't.
    """
    cur = conn.execute("PRAGMA compile_options")
    opts = {row[0] for row in cur.fetchall()}
    if "ENABLE_FTS5" not in opts:
        raise FTS5NotAvailableError(
            "Your Python's sqlite3 was built without FTS5 (ENABLE_FTS5). "
            "Argus requires FTS5 for prompt and transcript search. "
            "Use the official CPython distribution or build sqlite with FTS5."
        )


_BLOCK_KW = re.compile(r"\b(BEGIN|CASE|END)\b", re.IGNORECASE)


def _split_statements(script: str) -> list[str]:
    """Split a migration script into individual SQL statements.

    SQLite separates statements with ';', but a ``CREATE TRIGGER`` body holds
    its own ';'-terminated statements between ``BEGIN`` and ``END``. We track
    nesting so a trigger is emitted as a single statement rather than being
    chopped mid-body. ``CASE`` is counted as an opener too: it also closes with
    ``END``, so counting it keeps ENDs balanced whether a ``CASE`` appears at
    top level (MIGRATION_002's UPDATE) or inside a future trigger body.
    """
    statements: list[str] = []
    buf: list[str] = []
    depth = 0
    for chunk in script.split(";"):
        buf.append(chunk)
        for kw in _BLOCK_KW.findall(chunk):
            if kw.upper() in ("BEGIN", "CASE"):
                depth += 1
            elif depth > 0:
                depth -= 1
        if depth == 0:
            stmt = ";".join(buf).strip()
            buf = []
            if stmt:
                statements.append(stmt)
    tail = ";".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _run_migration(conn: sqlite3.Connection, version: int, sql: str) -> None:
    """Apply one versioned migration and record schema_version atomically.

    The migration's statements and the schema_version bump run inside a single
    explicit transaction, so a crash mid-migration can never again leave the
    DB with applied DDL but a stale recorded version (the bug that produced
    ``duplicate column name`` on restart).

    Recovery: SQLite has no ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``, so a
    DB left half-migrated by the old non-atomic runner re-reports
    ``duplicate column name``. Because the column is already present, we skip
    just that statement and continue — every other migration statement is
    ``IF NOT EXISTS`` / ``INSERT OR REPLACE`` / idempotent ``UPDATE``. This is
    non-destructive: no row is dropped, deleted, or rewritten.
    """
    conn.execute("BEGIN")
    try:
        for stmt in _split_statements(sql):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    continue  # column already added by a prior partial run
                raise
        conn.execute(
            "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('schema_version', ?)",
            (str(version),),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def open_db(path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open the Argus SQLite DB at ``path``.

    Read-write (default) applies migrations 1..N. Read-only opens with the
    ``mode=ro`` URI (defense in depth — a bug in a read path cannot write
    through the connection) and skips migrations and the WAL pragma: the
    live writer that justifies read-only mode has already created and
    migrated the schema, and journal mode cannot be set on a ro connection.
    """
    p = Path(path)

    if read_only:
        uri = Path(p).resolve(strict=False).as_uri() + "?mode=ro"
        conn = sqlite3.connect(
            uri,
            uri=True,
            check_same_thread=False,
            isolation_level=None,
            cached_statements=0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        _assert_fts5(conn)
        return conn

    p.parent.mkdir(parents=True, exist_ok=True)

    # check_same_thread=False because the Repository may be accessed from
    # the request thread, the watcher thread, the first-run worker, and
    # the scheduler thread. SQLite itself is thread-safe in serialized
    # mode; better-sqlite3 in TS makes the same assumption.
    #
    # cached_statements=0 disables Python sqlite3's per-Connection
    # prepared-statement cache. With the cache enabled, two threads that
    # call execute(SAME_SQL, ...) concurrently can both pull the cached
    # sqlite3_stmt*, both call reset+bind on it, and one loses with
    # SQLITE_MISUSE ("bad parameter or other API misuse"). The cost of
    # disabling the cache is ~10µs of statement prep per query, which is
    # well below the cost of the queries themselves. SQLite's own internal
    # compilation cache (sqlite3_prepare_v2 fast path) still applies.
    conn = sqlite3.connect(
        str(p),
        check_same_thread=False,
        isolation_level=None,
        cached_statements=0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")

    _assert_fts5(conn)

    # MIGRATION_001 is fully idempotent (CREATE TABLE IF NOT EXISTS) so we
    # always run it. After this point app_meta is guaranteed to exist.
    conn.executescript(MIGRATION_001)

    # Versioned migrations: ALTER TABLE has no IF NOT EXISTS in SQLite, so
    # we gate later migrations on a schema_version row. Fresh DBs start at
    # 1 (everything in MIGRATION_001 has been applied) and step up.
    row = conn.execute(
        "SELECT value FROM app_meta WHERE key = 'schema_version'"
    ).fetchone()
    current = int(row["value"]) if row else 1

    # Each migration commits its DDL and its schema_version bump together, so
    # the recorded version always matches the applied schema. See _run_migration.
    versioned = (
        (2, MIGRATION_002),
        (3, MIGRATION_003),
        (4, MIGRATION_004),
        (5, MIGRATION_005),
        (6, MIGRATION_006),
        (7, MIGRATION_007),
    )
    for version, sql in versioned:
        if current < version:
            _run_migration(conn, version, sql)
            current = version

    return conn
