"""GET /api/inventory and PATCH /api/inventory/{barcode}. Spec 6.6."""

from __future__ import annotations

import pytest


def error_code(response) -> str:
    body = response.json()
    assert set(body) == {"error"}, body
    return body["error"]["code"]


def quantity_of(conn, barcode: str):
    row = conn.execute(
        "SELECT quantity FROM inventory WHERE barcode = ?", (barcode,)
    ).fetchone()
    return None if row is None else row["quantity"]


def events_for(conn, barcode: str) -> list:
    return conn.execute(
        "SELECT * FROM scan_events WHERE barcode = ? ORDER BY id", (barcode,)
    ).fetchall()


# --- PATCH --------------------------------------------------------------------


def test_patch_with_quantity_sets_it(client, conn):
    response = client.patch("/api/inventory/111", json={"quantity": 7})
    assert response.status_code == 200
    assert response.json()["quantity"] == 7
    assert quantity_of(conn, "111") == 7


def test_patch_with_delta_adds_to_it(client, conn):
    client.patch("/api/inventory/111", json={"quantity": 2})
    response = client.patch("/api/inventory/111", json={"delta": 3})
    assert response.json()["quantity"] == 5
    assert quantity_of(conn, "111") == 5


def test_patch_creates_the_row_when_absent(client, conn):
    assert quantity_of(conn, "999") is None
    client.patch("/api/inventory/999", json={"delta": 2})
    assert quantity_of(conn, "999") == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"quantity": 1, "delta": 1},  # both
        {},  # neither
        {"nothing": 1},
        {"quantity": None},
        {"delta": "2"},  # a string, not a number
        {"delta": 1.5},  # not a whole number
        {"delta": True},  # JSON true is an int in Python; it must not mean 1
        [1],  # not an object
        None,
    ],
)
def test_patch_with_a_bad_body_is_invalid_request(client, conn, payload):
    response = client.patch("/api/inventory/111", json=payload)
    assert response.status_code == 422
    assert error_code(response) == "invalid_request"
    assert quantity_of(conn, "111") is None  # and nothing was written


def test_patch_with_a_bad_barcode_is_invalid_request(client, conn):
    response = client.patch("/api/inventory/not%20a%20barcode", json={"delta": 1})
    assert response.status_code == 422
    assert error_code(response) == "invalid_request"
    assert conn.execute("SELECT COUNT(*) FROM scan_events").fetchone()[0] == 0


def test_patch_needs_a_token(client_no_auth, conn):
    response = client_no_auth.patch("/api/inventory/111", json={"delta": 1})
    assert response.status_code == 401
    assert error_code(response) == "invalid_token"
    assert quantity_of(conn, "111") is None


# --- flooring (acceptance criterion 4) ----------------------------------------


def test_a_delta_below_zero_floors_at_zero(client, conn):
    client.patch("/api/inventory/111", json={"quantity": 2})
    response = client.patch("/api/inventory/111", json={"delta": -5})
    assert response.json()["quantity"] == 0
    assert quantity_of(conn, "111") == 0


def test_a_negative_quantity_floors_at_zero(client, conn):
    response = client.patch("/api/inventory/111", json={"quantity": -3})
    assert response.json()["quantity"] == 0
    assert quantity_of(conn, "111") == 0


def test_the_floored_event_records_the_change_that_happened(client, conn):
    """Criterion 7 depends on this: quantity is the sum of its deltas."""
    client.patch("/api/inventory/111", json={"quantity": 2})
    client.patch("/api/inventory/111", json={"delta": -5})

    deltas = [event["quantity_delta"] for event in events_for(conn, "111")]
    assert deltas == [2, -2]  # -2 applied, not the -5 that was asked for
    assert sum(deltas) == quantity_of(conn, "111")


# --- the manual event row (acceptance criterion 6) ----------------------------


def test_a_manual_edit_writes_a_manual_event(client, conn):
    client.patch("/api/inventory/111", json={"delta": 4})

    events = events_for(conn, "111")
    assert len(events) == 1
    event = events[0]
    assert event["source"] == "manual"
    assert event["action"] == "adjust"
    assert event["nonce"] is None
    assert event["quantity_delta"] == 4
    assert event["result"] == "applied"
    assert event["received_at"].endswith("Z")


def test_manual_events_never_collide_on_the_null_nonce(client, conn):
    """SQLite allows many NULLs in a UNIQUE column; this proves it. Spec 6.2."""
    for _ in range(5):
        assert client.patch("/api/inventory/111", json={"delta": 1}).status_code == 200
    assert len(events_for(conn, "111")) == 5
    assert quantity_of(conn, "111") == 5


def test_a_no_op_edit_still_writes_an_event(client, conn):
    """The log stays complete, and replay can still recreate the row."""
    client.patch("/api/inventory/111", json={"quantity": 0})
    events = events_for(conn, "111")
    assert len(events) == 1
    assert events[0]["quantity_delta"] == 0
    assert quantity_of(conn, "111") == 0


# --- GET ----------------------------------------------------------------------


def test_inventory_starts_empty(client):
    assert client.get("/api/inventory").json() == {"items": []}


def test_inventory_needs_a_token(client_no_auth):
    response = client_no_auth.get("/api/inventory")
    assert response.status_code == 401
    assert error_code(response) == "invalid_token"


def test_inventory_joins_product_details(client, conn):
    conn.execute(
        """
        INSERT INTO products (barcode, name, brand, image_url, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("111", "Baked Beans", "Bush's", "http://img", "openfoodfacts", "2026-01-01T00:00:00.000Z"),
    )
    client.patch("/api/inventory/111", json={"quantity": 3})

    items = client.get("/api/inventory").json()["items"]
    assert len(items) == 1
    assert items[0] == {
        "barcode": "111",
        "quantity": 3,
        "updated_at": items[0]["updated_at"],
        "name": "Baked Beans",
        "brand": "Bush's",
        "image_url": "http://img",
    }


def test_inventory_lists_items_with_no_product_row(client):
    client.patch("/api/inventory/111", json={"quantity": 1})
    item = client.get("/api/inventory").json()["items"][0]
    assert item["name"] is None
    assert item["brand"] is None


def test_inventory_keeps_rows_at_zero(client):
    """Being out of something is worth seeing on the page."""
    client.patch("/api/inventory/111", json={"quantity": 0})
    assert [i["quantity"] for i in client.get("/api/inventory").json()["items"]] == [0]


def test_inventory_sorts_by_name_then_barcode(client, conn):
    conn.executemany(
        "INSERT INTO products (barcode, name, source) VALUES (?, ?, ?)",
        [("111", "zucchini", "manual"), ("222", "Apples", "manual")],
    )
    for barcode in ("111", "222", "333"):
        client.patch(f"/api/inventory/{barcode}", json={"quantity": 1})

    items = client.get("/api/inventory").json()["items"]
    # "333" has no name, so it sorts under its barcode; the sort ignores case.
    assert [i["barcode"] for i in items] == ["333", "222", "111"]


# --- the page -----------------------------------------------------------------


def test_the_page_is_served_without_a_token(client_no_auth):
    response = client_no_auth.get("/")
    assert response.status_code == 200
    assert "<title>Pantry</title>" in response.text


def test_the_static_mount_does_not_swallow_the_api(client_no_auth):
    """If the mount were registered first, this would be a 404 from the page."""
    assert client_no_auth.get("/api/inventory").status_code == 401
