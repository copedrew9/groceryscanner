"""The migration runner applies each file once and tracks it in user_version."""

from __future__ import annotations

import sqlite3

import pytest

from app import db

TABLES = {"products", "inventory", "scan_events"}


def table_names(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = ?", ("table",))
    return {row["name"] for row in rows}


def test_migrations_create_the_schema(conn):
    assert TABLES <= table_names(conn)


def test_user_version_matches_the_highest_migration(conn):
    highest = max(number for number, _ in db.migration_files())
    assert db.user_version(conn) == highest


def test_second_run_applies_nothing(db_file, conn):
    # db_file already ran them once; a second pass must be a no-op, not an error.
    assert db.apply_migrations(conn) == []
    assert TABLES <= table_names(conn)


def test_first_run_reports_what_it_applied(tmp_path):
    fresh = tmp_path / "fresh.db"
    applied = db.initialize(str(fresh))
    assert applied == [number for number, _ in db.migration_files()]
    assert db.initialize(str(fresh)) == []


def test_wal_is_enabled(db_file):
    assert db.set_wal(str(db_file)).lower() == "wal"


def test_a_failing_migration_leaves_nothing_behind(tmp_path, monkeypatch):
    """Each migration is atomic: a bad file must not half-apply or bump the version."""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_ok.sql").write_text("CREATE TABLE a (x TEXT) STRICT;")
    (migrations / "002_bad.sql").write_text(
        "CREATE TABLE b (y TEXT) STRICT;\nTHIS IS NOT SQL;"
    )
    monkeypatch.setattr(db, "MIGRATIONS_DIR", migrations)

    path = str(tmp_path / "broken.db")
    conn = db.connect(path)
    try:
        with pytest.raises(sqlite3.Error):
            db.apply_migrations(conn)
        # The write lock must be released, not held until the connection closes.
        assert not conn.in_transaction
    finally:
        conn.close()

    reopened = db.connect(path)
    try:
        assert db.user_version(reopened) == 1
        assert table_names(reopened) == {"a"}
    finally:
        reopened.close()
