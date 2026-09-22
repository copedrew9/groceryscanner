"""GET and PUT /api/products/{barcode}, and the Open Food Facts lookup.

No test here touches the network: httpx.get is replaced in every case.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import db, lookup


def error_code(response) -> str:
    body = response.json()
    assert set(body) == {"error"}, body
    return body["error"]["code"]


def product_row(conn, barcode: str):
    return conn.execute(
        "SELECT * FROM products WHERE barcode = ?", (barcode,)
    ).fetchone()


def days_ago(days: int) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(days=days)
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --- GET / PUT ----------------------------------------------------------------


def test_get_missing_product_is_not_found(client):
    response = client.get("/api/products/111")
    assert response.status_code == 404
    assert error_code(response) == "not_found"


def test_put_creates_a_manual_product(client, conn):
    response = client.put("/api/products/111", json={"name": "Rice", "brand": "Acme"})
    assert response.status_code == 200
    assert response.json()["name"] == "Rice"
    assert response.json()["source"] == "manual"

    row = product_row(conn, "111")
    assert row["source"] == "manual"
    assert row["brand"] == "Acme"
    assert row["fetched_at"].endswith("Z")


def test_put_overwrites_a_looked_up_product(client, conn):
    conn.execute(
        "INSERT INTO products (barcode, name, brand, source, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("111", "PRODUIT INCONNU", "", "openfoodfacts", days_ago(1)),
    )
    client.put("/api/products/111", json={"name": "Rice"})

    row = product_row(conn, "111")
    assert row["name"] == "Rice"
    assert row["source"] == "manual"


def test_put_then_get_round_trips(client):
    client.put("/api/products/111", json={"name": "Rice", "brand": None})
    body = client.get("/api/products/111").json()
    assert body["name"] == "Rice"
    assert body["brand"] is None


def test_put_trims_whitespace(client, conn):
    client.put("/api/products/111", json={"name": "  Rice  ", "brand": "  "})
    row = product_row(conn, "111")
    assert row["name"] == "Rice"
    assert row["brand"] is None  # a blank brand is no brand


@pytest.mark.parametrize(
    "payload",
    [{}, {"name": ""}, {"name": "   "}, {"name": None}, {"name": 5}, {"brand": "Acme"},
     {"name": "Rice", "brand": 5}, [1], None],
)
def test_put_with_a_bad_body_is_invalid_request(client, conn, payload):
    response = client.put("/api/products/111", json=payload)
    assert response.status_code == 422
    assert error_code(response) == "invalid_request"
    assert product_row(conn, "111") is None


def test_products_need_a_token(client_no_auth, conn):
    assert client_no_auth.get("/api/products/111").status_code == 401
    assert client_no_auth.put("/api/products/111", json={"name": "Rice"}).status_code == 401
    assert product_row(conn, "111") is None


# --- the lookup ---------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: object = None):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self) -> object:
        return self._payload


def off_hit(name="Baked Beans", brand="Bush's", image="http://img/1.jpg") -> dict:
    return {
        "status": 1,
        "product": {"product_name": name, "brands": brand, "image_url": image},
    }


class FakeHttp:
    """Stands in for httpx.get: records every call, replays queued outcomes.

    An outcome may be a FakeResponse or an exception to raise. The last one
    queued is reused if the lookup asks more times than we queued answers.
    """

    def __init__(self):
        self.calls: list[dict] = []
        self.queue: list[object] = [FakeResponse(200, off_hit())]

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        outcome = self.queue[0] if len(self.queue) == 1 else self.queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def http(monkeypatch) -> FakeHttp:
    """Replace httpx.get and give CONTACT_EMAIL a value."""
    fake = FakeHttp()
    monkeypatch.setattr(httpx, "get", fake.get)
    monkeypatch.setenv("CONTACT_EMAIL", "me@example.com")
    return fake


def test_lookup_stores_what_it_finds(db_file, conn, http):
    lookup.refresh_if_needed("111")

    row = product_row(conn, "111")
    assert row["name"] == "Baked Beans"
    assert row["brand"] == "Bush's"
    assert row["image_url"] == "http://img/1.jpg"
    assert row["source"] == "openfoodfacts"
    assert row["fetched_at"].endswith("Z")


def test_lookup_sends_the_required_headers_and_timeout(db_file, http):
    lookup.refresh_if_needed("111")

    assert len(http.calls) == 1
    assert http.calls[0]["url"] == "https://world.openfoodfacts.org/api/v2/product/111.json"
    assert http.calls[0]["timeout"] == 10.0
    agent = http.calls[0]["headers"]["User-Agent"]
    assert "me@example.com" in agent and "Pantry" in agent


def test_lookup_skips_a_fresh_row(db_file, conn, http):
    conn.execute(
        "INSERT INTO products (barcode, name, source, fetched_at) VALUES (?, ?, ?, ?)",
        ("111", "Cached", "openfoodfacts", days_ago(1)),
    )
    lookup.refresh_if_needed("111")
    assert http.calls == []
    assert product_row(conn, "111")["name"] == "Cached"


def test_lookup_refreshes_a_row_over_ninety_days_old(db_file, conn, http):
    conn.execute(
        "INSERT INTO products (barcode, name, source, fetched_at) VALUES (?, ?, ?, ?)",
        ("111", "Stale", "openfoodfacts", days_ago(91)),
    )
    lookup.refresh_if_needed("111")
    assert len(http.calls) == 1
    assert product_row(conn, "111")["name"] == "Baked Beans"


def test_lookup_skips_a_row_just_under_ninety_days_old(db_file, conn, http):
    conn.execute(
        "INSERT INTO products (barcode, name, source, fetched_at) VALUES (?, ?, ?, ?)",
        ("111", "Cached", "openfoodfacts", days_ago(89)),
    )
    lookup.refresh_if_needed("111")
    assert http.calls == []


def test_lookup_never_overwrites_a_manual_row(db_file, conn, http):
    """Spec 6.7 and the reason the guard is in SQL, not in Python."""
    conn.execute(
        "INSERT INTO products (barcode, name, brand, source, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("111", "My Name For It", "My Brand", "manual", days_ago(400)),
    )
    lookup.refresh_if_needed("111")

    assert http.calls == []  # it does not even ask
    row = product_row(conn, "111")
    assert row["name"] == "My Name For It"
    assert row["source"] == "manual"


def test_a_manual_row_survives_a_write_that_gets_through(db_file, conn):
    """Belt and braces: even called directly, the upsert leaves manual alone."""
    conn.execute(
        "INSERT INTO products (barcode, name, source, fetched_at) VALUES (?, ?, ?, ?)",
        ("111", "My Name For It", "manual", days_ago(400)),
    )
    db.store_lookup_result(
        conn, "111", {"name": "Robot Name", "brand": None, "image_url": None}
    )
    assert product_row(conn, "111")["name"] == "My Name For It"


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(404, None),
        FakeResponse(200, {"status": 0}),
        FakeResponse(200, {"status": 1}),  # no product object
        FakeResponse(200, "not a dict"),
    ],
)
def test_no_match_is_stored_as_unknown(db_file, conn, http, response):
    http.queue[:] = [response]
    lookup.refresh_if_needed("111")

    row = product_row(conn, "111")
    assert row["source"] == "unknown"
    assert row["name"] is None


def test_an_empty_product_name_is_stored_as_null(db_file, conn, http):
    http.queue[:] = [FakeResponse(200, off_hit(name="", brand="", image=""))]
    lookup.refresh_if_needed("111")

    row = product_row(conn, "111")
    assert row["name"] is None
    assert row["brand"] is None
    assert row["source"] == "openfoodfacts"


def test_an_unknown_row_is_not_looked_up_again_straight_away(db_file, conn, http):
    """Otherwise every scan of an item OFF does not have hits the network."""
    http.queue[:] = [FakeResponse(404, None)]
    lookup.refresh_if_needed("111")
    assert len(http.calls) == 1

    lookup.refresh_if_needed("111")
    assert len(http.calls) == 1  # still one


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("no route to host"),
        httpx.ReadTimeout("too slow"),
        httpx.HTTPStatusError("500", request=None, response=None),
    ],
)
def test_network_errors_are_swallowed(db_file, conn, http, failure):
    http.queue[:] = [failure]
    lookup.refresh_if_needed("111")  # must not raise
    assert product_row(conn, "111") is None  # and must not cache a failure


def test_a_server_error_is_swallowed(db_file, conn, http):
    http.queue[:] = [FakeResponse(500, None)]
    lookup.refresh_if_needed("111")
    assert product_row(conn, "111") is None


def test_without_a_contact_email_nothing_is_sent(db_file, conn, http, monkeypatch):
    monkeypatch.delenv("CONTACT_EMAIL", raising=False)
    lookup.refresh_if_needed("111")
    assert http.calls == []
    assert product_row(conn, "111") is None


def test_a_blank_contact_email_counts_as_unset(monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "   ")
    assert lookup.user_agent() is None


# --- the staleness rule -------------------------------------------------------


@pytest.mark.parametrize(
    ("fetched_at", "stale"),
    [
        (None, True),
        ("", True),
        ("not a timestamp", True),
        (days_ago(0), False),
        (days_ago(89), False),
        (days_ago(90), True),
        (days_ago(500), True),
    ],
)
def test_staleness(fetched_at, stale):
    assert db._is_stale(fetched_at) is stale
