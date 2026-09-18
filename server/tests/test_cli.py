"""replay-events runs on an empty database. Spec 6.2."""

from __future__ import annotations

from app import cli, db


def test_replay_on_empty_database_writes_nothing(db_file, conn):
    assert cli.replay_events() == 0
    assert conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0] == 0


def test_replay_on_a_database_that_does_not_exist_yet(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "brand-new.db"))
    assert cli.main(["replay-events"]) == 0
    assert "rebuilt 0" in capsys.readouterr().out


def test_replay_rebuilds_from_applied_events(db_file, conn):
    # Two applied adds and one rejected remove: the rejected row must not count.
    rows = [
        ("scanner", b"\x01" * 8, "111", "add", 1, "applied", "2026-01-01T00:00:00.000Z"),
        ("scanner", b"\x02" * 8, "111", "add", 1, "applied", "2026-01-02T00:00:00.000Z"),
        ("scanner", b"\x03" * 8, "222", "remove", 0, "rejected_not_in_stock", "2026-01-03T00:00:00.000Z"),
    ]
    conn.executemany(
        """
        INSERT INTO scan_events
            (source, nonce, barcode, action, quantity_delta, result, received_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )

    assert cli.replay_events() == 1

    inventory = conn.execute("SELECT * FROM inventory").fetchall()
    assert len(inventory) == 1
    assert inventory[0]["barcode"] == "111"
    assert inventory[0]["quantity"] == 2
    assert inventory[0]["updated_at"] == "2026-01-02T00:00:00.000Z"


def test_replay_is_idempotent(db_file, conn):
    conn.execute(
        """
        INSERT INTO scan_events
            (source, nonce, barcode, action, quantity_delta, result, received_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("manual", None, "333", "adjust", 4, "applied", db.utc_now()),
    )
    assert cli.replay_events() == 1
    assert cli.replay_events() == 1
    assert conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0] == 1
