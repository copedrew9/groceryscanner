"""Command line entry points. Spec 6.2.

Run from the server/ directory:
    python -m app.cli replay-events
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from app import db


def replay_events() -> int:
    """Rebuild inventory from the event log. Returns rows written."""
    # A fresh checkout may have no database file yet, so make sure the schema is
    # there before replaying. Both steps are no-ops on an up-to-date database.
    db.initialize()
    conn = db.connect()
    try:
        return db.replay_events(conn)
    finally:
        conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "replay-events",
        help="rebuild the inventory table from scan_events",
    )
    args = parser.parse_args(argv)

    if args.command == "replay-events":
        rebuilt = replay_events()
        print(f"replay-events: rebuilt {rebuilt} inventory row(s) from {db.db_path()}")
        return 0

    parser.error(f"unknown command: {args.command}")  # argparse exits non-zero
    return 2


if __name__ == "__main__":
    sys.exit(main())
