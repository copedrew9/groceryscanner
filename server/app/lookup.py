"""Open Food Facts product lookup, spec 6.7.

Runs from BackgroundTasks after the response has been sent, so nothing here is
allowed to raise: a scanner waiting on a batch must never be held up, or
failed, by a product name it does not need.
"""

from __future__ import annotations

import logging
import os

import httpx

from app import db

log = logging.getLogger(__name__)

PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"

# Spec 6.7: explicit, rather than whatever httpx defaults to this release.
TIMEOUT_SECONDS = 10.0

APP_NAME = "PantryInventory/2.0"


def user_agent() -> str | None:
    """Open Food Facts asks for an app name and a contact address.

    Returns None when CONTACT_EMAIL is unset. We skip the lookup rather than
    send an anonymous one, because sending it is what gets an app blocked.
    """
    email = os.environ.get("CONTACT_EMAIL", "").strip()
    if not email:
        return None
    return f"{APP_NAME} ({email})"


def text_or_none(value: object) -> str | None:
    """Open Food Facts returns "" for fields it does not have."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def fetch_product(barcode: str, agent: str) -> dict | None:
    """Ask Open Food Facts. Returns the fields, or None when there is no match."""
    response = httpx.get(
        PRODUCT_URL.format(barcode=barcode),
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": agent},
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()

    body = response.json()
    if not isinstance(body, dict) or body.get("status") != 1:
        return None

    product = body.get("product")
    if not isinstance(product, dict):
        return None

    return {
        "name": text_or_none(product.get("product_name")),
        "brand": text_or_none(product.get("brands")),
        "image_url": text_or_none(product.get("image_url")),
    }


def refresh_if_needed(barcode: str) -> None:
    """Look the barcode up if the cache says it is due, and store the result."""
    try:
        # Its own connection: the request's connection was closed when the
        # response was sent. Opened and closed around the network call rather
        # than held across it, so a slow reply does not sit on the database.
        conn = db.connect()
        try:
            if not db.product_needs_lookup(conn, barcode):
                return
        finally:
            conn.close()

        agent = user_agent()
        if agent is None:
            log.warning(
                "CONTACT_EMAIL is not set, so product lookups are disabled; "
                "not looking up %s",
                barcode,
            )
            return

        try:
            product = fetch_product(barcode, agent)
        except httpx.HTTPError as error:
            # A lookup that fails is not an error the user needs to see. The
            # barcode stays uncached and the next scan tries again.
            log.warning("product lookup for %s failed: %s", barcode, error)
            return

        conn = db.connect()
        try:
            db.store_lookup_result(conn, barcode, product)
        finally:
            conn.close()

    except Exception:  # noqa: BLE001 - a background task has nobody to raise to
        log.exception("product lookup for %s failed unexpectedly", barcode)
