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


def adjustment(payload: Any) -> tuple[int | None, int | None]:
    """Spec 6.6: PATCH /inventory takes exactly one of quantity or delta."""
    if not isinstance(payload, dict):
        raise invalid_request("Body must be a JSON object")

    has_quantity = "quantity" in payload
    has_delta = "delta" in payload
    if has_quantity == has_delta:  # both, or neither
        raise invalid_request('Send exactly one of "quantity" or "delta"')

    field = "quantity" if has_quantity else "delta"
    value = payload[field]
    # JSON true is an int in Python, so bool has to be excluded by hand or
    # {"delta": true} would quietly mean {"delta": 1}.
    if not isinstance(value, int) or isinstance(value, bool):
        raise invalid_request(f'"{field}" must be a whole number')

    return (value, None) if has_quantity else (None, value)


def checked_barcode(barcode: str) -> str:
    """Path barcodes get the same validation as the ones in a scan batch."""
    if not BARCODE_PATTERN.match(barcode):
        raise invalid_request("Barcode must be 1-32 characters of A-Z, a-z, 0-9 or -")
    return barcode


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


@router.get("/inventory")
def get_inventory(conn: sqlite3.Connection = Depends(db.get_conn)) -> dict:
    """Everything in the pantry, joined with product names. Spec 6.6."""
    return {"items": [dict(row) for row in db.list_inventory(conn)]}


@router.patch("/inventory/{barcode}")
def patch_inventory(
    barcode: str,
    payload: Any = Body(default=None),
    conn: sqlite3.Connection = Depends(db.get_conn),
) -> dict:
    """A manual edit, which also writes a scan_events row. Spec 6.6."""
    quantity, delta = adjustment(payload)
    return db.adjust_inventory(
        conn, checked_barcode(barcode), quantity=quantity, delta=delta
    )


DEFAULT_EVENT_LIMIT = 50
MAX_EVENT_LIMIT = 100


@router.get("/events")
def get_events(
    limit: int = DEFAULT_EVENT_LIMIT,
    before: int | None = None,
    conn: sqlite3.Connection = Depends(db.get_conn),
) -> dict:
    """Scan events, newest first, paged by id. Spec 6.6."""
    if not 1 <= limit <= MAX_EVENT_LIMIT:
        raise invalid_request(f"limit must be between 1 and {MAX_EVENT_LIMIT}")
    if before is not None and before < 1:
        raise invalid_request("before must be a positive id")

    rows, next_before = db.list_events(conn, limit, before)
    return {"events": [dict(row) for row in rows], "next_before": next_before}


@router.get("/products/{barcode}")
def get_product(
    barcode: str, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict:
    row = db.get_product(conn, checked_barcode(barcode))
    if row is None:
        raise HTTPException(status_code=404, detail="No product with that barcode")
    return dict(row)


@router.put("/products/{barcode}")
def put_product(
    barcode: str,
    payload: Any = Body(default=None),
    conn: sqlite3.Connection = Depends(db.get_conn),
) -> dict:
    """Name a product by hand. Sets source = 'manual'. Spec 6.6."""
    if not isinstance(payload, dict):
        raise invalid_request("Body must be a JSON object")

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise invalid_request('"name" must be a non-empty string')

    brand = payload.get("brand")
    if brand is not None and not isinstance(brand, str):
        raise invalid_request('"brand" must be a string or null')
    brand = brand.strip() if isinstance(brand, str) else None

    return db.put_manual_product(
        conn, checked_barcode(barcode), name.strip(), brand or None
    )
