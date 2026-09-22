"""Scan handling, spec section 6.4. Written by hand; do not edit."""
import sqlite3


def process_batch(conn: sqlite3.Connection, raw_scans: list) -> list[dict]:
    """Process each element in its own transaction, in request order.

    raw_scans: the unvalidated list from the request body. The route has
    already checked that it is a list of 1-100 items; the elements themselves
    are not validated yet.

    Returns one dict per element, in order:
        {"nonce": ..., "result": ..., "quantity": ...}
    with "quantity" omitted when result is "invalid".
    """
    raise NotImplementedError
