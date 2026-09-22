"""GET /api/events and its paging. Spec 6.6."""

from __future__ import annotations

import pytest


def error_code(response) -> str:
    body = response.json()
    assert set(body) == {"error"}, body
    return body["error"]["code"]


def seed(conn, count: int, barcode: str = "111") -> list[int]:
    """Insert `count` applied scan events and return their ids, oldest first."""
    ids = []
    for index in range(count):
        cursor = conn.execute(
            """
            INSERT INTO scan_events
                (source, nonce, barcode, action, quantity_delta, result, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "scanner",
                bytes([index % 256]) * 8,
                barcode,
                "add",
                1,
                "applied",
                f"2026-01-01T00:00:{index:02d}.000Z",
            ),
        )
        ids.append(cursor.lastrowid)
    return ids


def test_no_events(client):
    assert client.get("/api/events").json() == {"events": [], "next_before": None}


def test_newest_first(client, conn):
    ids = seed(conn, 3)
    events = client.get("/api/events").json()["events"]
    assert [event["id"] for event in events] == list(reversed(ids))


def test_events_need_a_token(client_no_auth):
    response = client_no_auth.get("/api/events")
    assert response.status_code == 401
    assert error_code(response) == "invalid_token"


# --- paging boundaries --------------------------------------------------------


def test_fewer_than_limit_has_no_next_page(client, conn):
    seed(conn, 3)
    body = client.get("/api/events?limit=10").json()
    assert len(body["events"]) == 3
    assert body["next_before"] is None


def test_exactly_limit_has_no_next_page(client, conn):
    """The boundary that an off-by-one gets wrong: a full page is not proof
    that another page exists."""
    seed(conn, 5)
    body = client.get("/api/events?limit=5").json()
    assert len(body["events"]) == 5
    assert body["next_before"] is None


def test_one_more_than_limit_has_a_next_page(client, conn):
    ids = seed(conn, 6)
    body = client.get("/api/events?limit=5").json()
    assert [event["id"] for event in body["events"]] == list(reversed(ids[1:]))
    assert body["next_before"] == ids[1]  # the smallest id returned


def test_before_excludes_the_cursor_itself(client, conn):
    ids = seed(conn, 6)
    body = client.get(f"/api/events?limit=5&before={ids[1]}").json()
    assert [event["id"] for event in body["events"]] == [ids[0]]
    assert body["next_before"] is None


def test_walking_every_page_sees_each_event_once(client, conn):
    ids = seed(conn, 23)

    seen: list[int] = []
    cursor = None
    pages = 0
    while True:
        query = "/api/events?limit=5" + (f"&before={cursor}" if cursor else "")
        body = client.get(query).json()
        seen.extend(event["id"] for event in body["events"])
        pages += 1
        cursor = body["next_before"]
        if cursor is None:
            break
        assert pages < 20, "paging did not terminate"

    assert seen == list(reversed(ids))
    assert len(seen) == len(set(seen))  # nothing repeated
    assert pages == 5  # 23 events at 5 per page


def test_before_past_the_oldest_event_is_empty(client, conn):
    ids = seed(conn, 3)
    body = client.get(f"/api/events?limit=5&before={ids[0]}").json()
    assert body == {"events": [], "next_before": None}


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=-1", "before=0", "before=-5"])
def test_bad_paging_arguments_are_invalid_request(client, query):
    response = client.get(f"/api/events?{query}")
    assert response.status_code == 422
    assert error_code(response) == "invalid_request"


@pytest.mark.parametrize("query", ["limit=abc", "before=abc"])
def test_non_numeric_paging_arguments_are_invalid_request(client, query):
    """FastAPI's own validation error, reshaped by our handler."""
    response = client.get(f"/api/events?{query}")
    assert response.status_code == 422
    assert error_code(response) == "invalid_request"


# --- shape --------------------------------------------------------------------


def test_events_carry_the_product_name_when_there_is_one(client, conn):
    conn.execute(
        "INSERT INTO products (barcode, name, brand, source) VALUES (?, ?, ?, ?)",
        ("111", "Baked Beans", "Bush's", "openfoodfacts"),
    )
    seed(conn, 1)
    event = client.get("/api/events").json()["events"][0]
    assert event["name"] == "Baked Beans"
    assert event["brand"] == "Bush's"


def test_events_without_a_product_row_still_return(client, conn):
    seed(conn, 1)
    event = client.get("/api/events").json()["events"][0]
    assert event["name"] is None
    assert set(event) == {
        "id", "source", "barcode", "action", "quantity_delta",
        "result", "received_at", "name", "brand",
    }


def test_rejected_events_are_visible(client, conn):
    """Acceptance criterion 5: a rejected remove shows up in the log."""
    conn.execute(
        """
        INSERT INTO scan_events
            (source, nonce, barcode, action, quantity_delta, result, received_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("scanner", b"z" * 8, "111", "remove", 0, "rejected_not_in_stock", "2026-01-01T00:00:00.000Z"),
    )
    event = client.get("/api/events").json()["events"][0]
    assert event["result"] == "rejected_not_in_stock"
    assert event["quantity_delta"] == 0


def test_manual_edits_appear_in_the_log(client):
    client.patch("/api/inventory/111", json={"delta": 2})
    event = client.get("/api/events").json()["events"][0]
    assert event["source"] == "manual"
    assert event["action"] == "adjust"
    assert event["quantity_delta"] == 2
