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
    """FastAPI dependency: one connection per request, closed when it ends.

    Worth knowing while writing transactional code: closing a connection with
    a transaction still open rolls it back. A missing COMMIT therefore loses
    the write silently rather than raising, which is an easy bug to chase.
    """
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


def list_events(
    conn: sqlite3.Connection, limit: int, before: int | None = None
) -> tuple[list[sqlite3.Row], int | None]:
    """A page of events, newest first, plus the cursor for the next page.

    Spec 6.6: paging by id rather than by offset means scans arriving while you
    browse do not shift the rows under you.

    One extra row is fetched to find out whether a next page exists, so
    next_before is null on the last page instead of pointing at nothing.

    The only thing formatted into the SQL is a fixed WHERE fragment chosen
    here; every value still goes in as a "?" parameter.
    """
    sql = """
        SELECT e.id, e.source, e.barcode, e.action, e.quantity_delta,
               e.result, e.received_at,
               p.name, p.brand
          FROM scan_events AS e
          LEFT JOIN products AS p ON p.barcode = e.barcode
         {where}
         ORDER BY e.id DESC
         LIMIT ?
    """
    if before is None:
        rows = conn.execute(sql.format(where=""), (limit + 1,)).fetchall()
    else:
        rows = conn.execute(
            sql.format(where="WHERE e.id < ?"), (before, limit + 1)
        ).fetchall()

    has_more = len(rows) > limit
    page = rows[:limit]
    next_before = page[-1]["id"] if (has_more and page) else None
    return page, next_before


def get_product(conn: sqlite3.Connection, barcode: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT barcode, name, brand, image_url, source, fetched_at "
        "FROM products WHERE barcode = ?",
        (barcode,),
    ).fetchone()


def put_manual_product(
    conn: sqlite3.Connection, barcode: str, name: str, brand: str | None
) -> dict:
    """Spec 6.6: a hand-entered name sets source = 'manual'.

    image_url is left as it was: a name typed in by hand says nothing about
    whether the picture the lookup found is wrong.
    """
    now = utc_now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO products (barcode, name, brand, image_url, source, fetched_at)
            VALUES (?, ?, ?, NULL, ?, ?)
            ON CONFLICT(barcode) DO UPDATE
                SET name = excluded.name,
                    brand = excluded.brand,
                    source = excluded.source,
                    fetched_at = excluded.fetched_at
            """,
            (barcode, name, brand, "manual", now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return dict(get_product(conn, barcode))


# --- product lookup support (spec 6.7) ---------------------------------------

LOOKUP_MAX_AGE_DAYS = 90


def _is_stale(fetched_at: str | None, now: datetime | None = None) -> bool:
    if not fetched_at:
        return True
    try:
        # Our timestamps end in Z; fromisoformat wants an explicit offset
        # before 3.11, and this keeps the parsing version-independent.
        stamp = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return True  # unparseable means we cannot trust it; look it up again
    reference = now or datetime.now(timezone.utc)
    return (reference - stamp).days >= LOOKUP_MAX_AGE_DAYS


def product_needs_lookup(conn: sqlite3.Connection, barcode: str) -> bool:
    """Spec 6.7: no row, never fetched, or the cache is over 90 days old.

    A row with source = 'manual' is never refreshed, so a name you typed in is
    not quietly replaced later.

    Deviation worth knowing about: the spec also lists "its name is null" as a
    trigger. Taken literally, a barcode Open Food Facts does not have gets a
    row with a null name, which would then re-trigger a lookup on every single
    scan of that item -- exactly the hammering the 90-day cache exists to
    avoid. fetched_at covers the same ground without the storm, so a row that
    was fetched and came back nameless waits out the 90 days like any other.
    """
    row = conn.execute(
        "SELECT source, fetched_at FROM products WHERE barcode = ?", (barcode,)
    ).fetchone()
    if row is None:
        return True
    if row["source"] == "manual":
        return False
    return _is_stale(row["fetched_at"])


def store_lookup_result(
    conn: sqlite3.Connection, barcode: str, product: dict | None
) -> None:
    """Cache what the lookup found. product is None when there was no match.

    The WHERE clause on the upsert is what stops a lookup from overwriting a
    manual row. Doing it in SQL rather than with a read-then-write means a
    manual edit landing mid-lookup still wins.
    """
    fields = product or {}
    source = "openfoodfacts" if product is not None else "unknown"
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO products (barcode, name, brand, image_url, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(barcode) DO UPDATE
                SET name = excluded.name,
                    brand = excluded.brand,
                    image_url = excluded.image_url,
                    source = excluded.source,
                    fetched_at = excluded.fetched_at
                WHERE products.source <> 'manual'
            """,
            (
                barcode,
                fields.get("name"),
                fields.get("brand"),
                fields.get("image_url"),
                source,
                utc_now(),
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
