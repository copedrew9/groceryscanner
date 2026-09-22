"""Database connection, migrations, and all SQL. Spec sections 6.2 and 6.3.

Every SQL statement in the server lives here, with one documented exception:
app/scans.py holds its own SQL (spec 6.4), and imports utc_now() from here.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

# migrations/ sits next to app/, so climb out of app/ to find it.
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

DEFAULT_DB_PATH = "inventory.db"

# Spec 6.2: set on every connection. SQLite does not accept bound parameters in
# a PRAGMA, so this constant is interpolated instead of passed as "?".
BUSY_TIMEOUT_MS = 5000


def db_path() -> str:
    """Read DB_PATH fresh each call so tests can point it at a temp file."""
    return os.environ.get("DB_PATH") or DEFAULT_DB_PATH


def utc_now() -> str:
    """Spec 6.3: ISO-8601 UTC text ending in Z.

    Milliseconds are kept so events inside one batch stay distinguishable.
    """
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return stamp.replace("+00:00", "Z")


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open one connection with the settings spec 6.3 requires."""
    conn = sqlite3.connect(
        path if path is not None else db_path(),
        isolation_level=None,  # Python's implicit transactions off; we write BEGIN/COMMIT ourselves.
        # FastAPI can run a sync dependency and the sync handler that uses it on
        # different threadpool threads. Safe here: one connection per request,
        # used by one thread at a time.
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request, closed when it ends."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


# --- startup -----------------------------------------------------------------


def set_wal(path: str | None = None) -> str:
    """Spec 6.2: set once at startup, before migrations. Persists in the file.

    journal_mode cannot be changed inside a transaction, so this runs on its own
    connection before anything else touches the database.
    """
    conn = connect(path)
    try:
        return conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    finally:
        conn.close()


def migration_files() -> list[tuple[int, Path]]:
    """Every migrations/NNN_*.sql file, lowest number first."""
    found: list[tuple[int, Path]] = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        number = int(path.name.split("_", 1)[0])
        found.append((number, path))
    found.sort()
    return found


def user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply every migration numbered above user_version. Returns what ran.

    Each file runs in its own transaction together with its user_version bump,
    so a half-applied migration can never look applied. BEGIN/COMMIT are part of
    the script because sqlite3.executescript() commits any transaction that is
    already open before it starts.
    """
    current = user_version(conn)
    applied: list[int] = []
    for number, path in migration_files():
        if number <= current:
            continue
        script = path.read_text(encoding="utf-8")
        # PRAGMA user_version takes no bound parameter; number came from a
        # filename we parsed as int, so there is nothing injectable left.
        try:
            conn.executescript(
                f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;"
            )
        except Exception:
            # A failed statement aborts the script but leaves the transaction
            # open, still holding the write lock. Close it before re-raising so
            # a caller that keeps the connection is not stuck with a half-open
            # migration.
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(number)
    return applied


def initialize(path: str | None = None) -> list[int]:
    """WAL first, then migrations. Used by app startup and by the CLI."""
    target = path if path is not None else db_path()
    set_wal(target)
    conn = connect(target)
    try:
        return apply_migrations(conn)
    finally:
        conn.close()


# --- queries -----------------------------------------------------------------


def replay_events(conn: sqlite3.Connection) -> int:
    """Rebuild inventory from scan_events. Spec 6.2, acceptance criterion 7.

    One transaction: drop every inventory row, then re-derive one row per
    barcode from the applied events. Rows summing to 0 are kept, because live
    inventory keeps a row at quantity 0 once an item has been removed; dropping
    them here would make replay differ from live.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM inventory")
        cursor = conn.execute(
            """
            INSERT INTO inventory (barcode, quantity, updated_at)
            SELECT barcode, SUM(quantity_delta), MAX(received_at)
              FROM scan_events
             WHERE result = ?
             GROUP BY barcode
            """,
            ("applied",),
        )
        rebuilt = cursor.rowcount
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return rebuilt


def list_inventory(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every inventory row with whatever product details we have. Spec 6.6.

    LEFT JOIN, not JOIN: a barcode always has an inventory row before it has a
    products row, and unnamed items still have to show up on the page.
    """
    return conn.execute(
        """
        SELECT i.barcode, i.quantity, i.updated_at,
               p.name, p.brand, p.image_url
          FROM inventory AS i
          LEFT JOIN products AS p ON p.barcode = i.barcode
         ORDER BY COALESCE(p.name, i.barcode) COLLATE NOCASE, i.barcode
        """
    ).fetchall()


def adjust_inventory(
    conn: sqlite3.Connection,
    barcode: str,
    *,
    quantity: int | None = None,
    delta: int | None = None,
) -> dict:
    """Apply a manual edit. Spec 6.6, acceptance criterion 6.

    Exactly one of quantity or delta; the caller has already checked that. The
    event row and the inventory row are written in one transaction so the log
    can never disagree with the count.
    """
    conn.execute("BEGIN IMMEDIATE")  # take the write lock before doing any work
    try:
        row = conn.execute(
            "SELECT quantity FROM inventory WHERE barcode = ?", (barcode,)
        ).fetchone()
        current = row["quantity"] if row is not None else 0

        target = quantity if quantity is not None else current + (delta or 0)
        new_quantity = max(0, target)  # spec 6.6: the result is floored at 0
        # What actually happened, which is not what was asked for when the
        # floor kicks in. Storing the real change keeps criterion 7 true:
        # quantity always equals the sum of its deltas.
        applied_delta = new_quantity - current

        now = utc_now()
        conn.execute(
            """
            INSERT INTO scan_events
                (source, nonce, barcode, action, quantity_delta, result, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("manual", None, barcode, "adjust", applied_delta, "applied", now),
        )
        conn.execute(
            """
            INSERT INTO inventory (barcode, quantity, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(barcode) DO UPDATE
                SET quantity = excluded.quantity,
                    updated_at = excluded.updated_at
            """,
            (barcode, new_quantity, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {"barcode": barcode, "quantity": new_quantity, "updated_at": now}
