"""Schema + migrations smoke tests."""
from __future__ import annotations

import sqlite3

import pytest

from argus.store.db import SCHEMA_VERSION, open_db


def test_open_db_read_only_rejects_writes(tmp_path):
    # First create + migrate the DB read-write, then close it.
    p = tmp_path / "argus.db"
    rw = open_db(p)
    rw.execute(
        "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('k', 'v')"
    )
    rw.close()

    ro = open_db(p, read_only=True)
    try:
        # Reads work.
        row = ro.execute("SELECT value FROM app_meta WHERE key = 'k'").fetchone()
        assert row["value"] == "v"
        # Writes are rejected by SQLite (mode=ro).
        with pytest.raises(sqlite3.OperationalError):
            ro.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('x', 'y')"
            )
    finally:
        ro.close()


def test_creates_schema_on_first_open(db_path):
    db = open_db(db_path)
    try:
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {r["name"] for r in rows}
        for tbl in {
            "sessions",
            "turns",
            "file_offsets",
            "parse_errors",
            "app_meta",
            "tool_calls",
            "prompts",
            "transcript_segments",
        }:
            assert tbl in names
    finally:
        db.close()


def test_migration_007_adds_prompts_session_id(db_path):
    db = open_db(db_path)
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(prompts)").fetchall()}
        assert "session_id" in cols
        v = db.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
        assert int(v["value"]) >= 7
    finally:
        db.close()


def test_enables_wal_mode(db_path):
    db = open_db(db_path)
    try:
        r = db.execute("PRAGMA journal_mode").fetchone()
        assert r[0] == "wal"
    finally:
        db.close()


def test_is_idempotent(db_path):
    open_db(db_path).close()
    # Second open must not raise.
    db = open_db(db_path)
    db.close()


def test_fts5_compiled_in(db_path):
    db = open_db(db_path)
    try:
        opts = {r[0] for r in db.execute("PRAGMA compile_options").fetchall()}
        assert "ENABLE_FTS5" in opts
    finally:
        db.close()


def test_open_db_creates_alerts_table(db_path):
    conn = open_db(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_open_db_applies_latest_migration(db_path):
    from argus.store.db import SCHEMA_VERSION

    conn = open_db(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM app_meta WHERE key='schema_version'"
        ).fetchone()
        assert row["value"] == str(SCHEMA_VERSION)
    finally:
        conn.close()


def test_migration_006_adds_tool_use_id_column(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(transcript_segments)")}
    assert "tool_use_id" in cols
    row = db.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
    # A fresh DB always lands on the latest version, whatever that is today.
    assert int(row["value"]) == SCHEMA_VERSION


def test_migration_006_index_exists(db):
    names = {
        r["name"]
        for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert "idx_segments_tool_use" in names


def test_open_db_recovers_from_half_applied_migration(db_path):
    """A DB whose column was added but whose schema_version never advanced
    must re-open cleanly, not crash with 'duplicate column name'.

    Reproduces the field failure: MIGRATION_006 added transcript_segments.
    tool_use_id but schema_version stayed < 6 (migrations were committed
    per-statement while the version was written only at the very end). The
    re-run then hit a non-idempotent ALTER TABLE ADD COLUMN.
    """
    # Build a faithfully half-migrated DB: column present, index missing,
    # schema_version pinned back to 5.
    conn = open_db(db_path)
    conn.execute("DROP INDEX IF EXISTS idx_segments_tool_use")
    conn.execute(
        "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('schema_version', '5')"
    )
    conn.close()

    # Re-open must not raise, must finish at the latest version (re-applying
    # 006 and everything after it), and must heal the missing index.
    conn = open_db(db_path)
    try:
        ver = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert int(ver["value"]) == SCHEMA_VERSION
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(transcript_segments)")}
        assert "tool_use_id" in cols
        idx = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
        assert "idx_segments_tool_use" in idx
    finally:
        conn.close()


@pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 35, 0), reason="DROP COLUMN needs SQLite 3.35+")
def test_open_db_heals_version_stamped_ahead_of_schema(db_path):
    """Regression: a dev DB recorded schema_version 7 without prompts.session_id.

    A different branch (feat/workflow-observability) shipped its own
    MIGRATION_007, ran locally, and stamped 7; this branch's 007 was then
    skipped as "already applied" and `argus start` crashed with
    `no such column: prompts.session_id`. The version number alone cannot be
    trusted: open_db must verify each migration's artifact and re-run the
    migration when it is missing (every migration is idempotent).
    """
    conn = open_db(db_path)
    conn.execute("DROP INDEX IF EXISTS idx_prompts_session")
    conn.execute("ALTER TABLE prompts DROP COLUMN session_id")
    ver = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
    assert int(ver["value"]) == SCHEMA_VERSION  # version says "done", schema disagrees
    conn.close()

    conn = open_db(db_path)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(prompts)")}
        assert "session_id" in cols
        idx = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert "idx_prompts_session" in idx
        ver = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
        assert int(ver["value"]) == SCHEMA_VERSION  # never stamped backwards
    finally:
        conn.close()


def test_alerts_has_resolved_at_column(db_path):
    conn = open_db(db_path)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(alerts)").fetchall()}
        assert "resolved_at" in cols
    finally:
        conn.close()


def test_connection_handles_concurrent_same_sql_across_threads(db_path):
    """Two threads hammering the same SELECT must not hit SQLITE_MISUSE.

    Regression for: scheduler thread + server request thread both calling
    ``aggregate_turns_by_day`` (same SQL string) raced on the shared
    Connection's prepared-statement cache and lost with
    ``sqlite3.InterfaceError: bad parameter or other API misuse``. Fix
    was passing ``cached_statements=0`` to ``sqlite3.connect``.
    """
    import threading

    conn = open_db(db_path)
    # Seed a single session so the query has rows to iterate.
    conn.execute(
        "INSERT INTO sessions (id, agent, project_path, started_at, primary_model, "
        "pricing_table_version, computed_at) VALUES (?, 'x', '/p', ?, 'm', 'v', ?)",
        ("s1", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )

    errors: list[Exception] = []

    def hammer():
        try:
            for _ in range(100):
                conn.execute(
                    "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ? OFFSET ?",
                    (100, 0),
                ).fetchall()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    conn.close()
    assert errors == [], f"Concurrent same-SQL execute raised: {errors[:3]}"


def test_alerts_unique_on_detector_and_dedup_key(db_path):
    import sqlite3

    conn = open_db(db_path)
    try:
        conn.execute(
            "INSERT INTO alerts (detector, dedup_key, severity, title, message, metadata, first_seen_at, last_seen_at) "
            "VALUES (?, ?, 'warning', 't', 'm', '{}', '2026-05-26T00:00:00Z', '2026-05-26T00:00:00Z')",
            ("cost_outlier", "2026-05-26"),
        )
        try:
            conn.execute(
                "INSERT INTO alerts (detector, dedup_key, severity, title, message, metadata, first_seen_at, last_seen_at) "
                "VALUES (?, ?, 'warning', 't', 'm', '{}', '2026-05-26T01:00:00Z', '2026-05-26T01:00:00Z')",
                ("cost_outlier", "2026-05-26"),
            )
            raise AssertionError("expected IntegrityError on duplicate (detector, dedup_key)")
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()
