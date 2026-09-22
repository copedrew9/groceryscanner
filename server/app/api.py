"""API routes (spec 6.6).

The router carries no prefix and no dependencies of its own. main.py mounts it
under /api with the token dependency attached, so every route added here is
protected by construction -- there is no way to forget it on a new route.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException

from app import db, lookup, scans

router = APIRouter()

# Spec section 4: 1-32 chars, [A-Za-z0-9-] only.
BARCODE_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,32}\Z")

MIN_BATCH = 1
MAX_BATCH = 100


def invalid_request(message: str) -> HTTPException:
    """422 in the spec's error shape; main.py maps the status to a code."""
    return HTTPException(status_code=422, detail=message)


def envelope_scans(payload: Any) -> list:
    """Check the envelope only. Elements are validated one by one in scans.py.

    Spec 6.4: rejecting the whole batch over a single bad element would break
    acceptance criterion 2, so nothing here looks inside the list.
    """
    if not isinstance(payload, dict):
        raise invalid_request("Body must be a JSON object")
    raw_scans = payload.get("scans")
    if not isinstance(raw_scans, list):
        raise invalid_request('"scans" must be a list')
    if not MIN_BATCH <= len(raw_scans) <= MAX_BATCH:
        raise invalid_request(
            f'"scans" must hold {MIN_BATCH}-{MAX_BATCH} items, got {len(raw_scans)}'
        )
    return raw_scans


def lookup_barcodes(raw_scans: list) -> list[str]:
    """Distinct well-formed barcodes, in request order.

    Elements that fail validation are skipped: scans.py will reject them, and
    there is no point asking Open Food Facts about a malformed barcode.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for element in raw_scans:
        if not isinstance(element, dict):
            continue
        barcode = element.get("barcode")
        if not isinstance(barcode, str) or not BARCODE_PATTERN.match(barcode):
            continue
        if barcode not in seen:
            seen.add(barcode)
            ordered.append(barcode)
    return ordered


@router.post("/scans")
def post_scans(
    background: BackgroundTasks,
    payload: Any = Body(default=None),
    conn: sqlite3.Connection = Depends(db.get_conn),
) -> dict:
    """Scanner batch, spec section 4."""
    raw_scans = envelope_scans(payload)
    results = scans.process_batch(conn, raw_scans)
    # Spec 6.4: scheduled after the loop, never awaited. refresh_if_needed
    # decides for itself whether a lookup is actually due.
    for barcode in lookup_barcodes(raw_scans):
        background.add_task(lookup.refresh_if_needed, barcode)
    return {"results": results}
