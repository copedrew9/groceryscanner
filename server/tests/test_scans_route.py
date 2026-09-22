"""The POST /api/scans envelope and its wiring.

The scan logic itself lives in app/scans.py and is tested in test_core.py.
These tests cover only what the route is responsible for: the envelope check,
the token check, and scheduling lookups.
"""

from __future__ import annotations

import pytest

from app import api

VALID = {"nonce": "3f8a1c9d2b4e6f70", "barcode": "041196891010", "action": "add"}


def error_code(response) -> str:
    body = response.json()
    assert set(body) == {"error"}, body
    return body["error"]["code"]


# --- envelope (spec 6.4) ------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        [VALID],  # a bare list, not an object
        "scans",
        None,
        {},  # no "scans" key
        {"scans": {"nonce": "a" * 16}},  # object, not a list
        {"scans": "not a list"},
        {"scans": 5},
        {"scans": []},  # 0 items
        {"scans": [VALID] * 101},  # 101 items
    ],
)
def test_bad_envelope_is_invalid_request(client, payload):
    response = client.post("/api/scans", json=payload)
    assert response.status_code == 422
    assert error_code(response) == "invalid_request"


def test_malformed_json_is_invalid_request(client):
    response = client.post(
        "/api/scans",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert error_code(response) == "invalid_request"


def test_batch_of_one_hundred_passes_the_envelope(client, monkeypatch):
    """The boundary on the other side of 101: 100 must be accepted."""
    monkeypatch.setattr(api.scans, "process_batch", lambda conn, raw: [])
    response = client.post("/api/scans", json={"scans": [VALID] * 100})
    assert response.status_code == 200


# --- token (acceptance criterion 3) -------------------------------------------


def test_no_token_is_rejected(client_no_auth):
    response = client_no_auth.post("/api/scans", json={"scans": [VALID]})
    assert response.status_code == 401
    assert error_code(response) == "invalid_token"


def test_wrong_token_is_rejected(client_no_auth):
    response = client_no_auth.post(
        "/api/scans",
        json={"scans": [VALID]},
        headers={"Authorization": "Bearer " + "f" * 64},
    )
    assert response.status_code == 401
    assert error_code(response) == "invalid_token"


def test_token_without_the_bearer_prefix_is_rejected(client_no_auth, token):
    response = client_no_auth.post(
        "/api/scans", json={"scans": [VALID]}, headers={"Authorization": token}
    )
    assert response.status_code == 401


def test_the_token_is_checked_before_the_body(client_no_auth):
    """An unauthenticated caller learns nothing about what the body should be."""
    response = client_no_auth.post("/api/scans", json={"nope": True})
    assert response.status_code == 401


def test_no_token_writes_nothing(client_no_auth, conn):
    client_no_auth.post("/api/scans", json={"scans": [VALID]})
    assert conn.execute("SELECT COUNT(*) FROM scan_events").fetchone()[0] == 0


# --- wiring -------------------------------------------------------------------


def test_results_are_passed_through_unchanged(client, monkeypatch):
    """The route hands scans.py the raw list and wraps whatever comes back."""
    captured = {}

    def fake_process_batch(conn, raw_scans):
        captured["raw_scans"] = raw_scans
        return [{"nonce": VALID["nonce"], "result": "applied", "quantity": 1}]

    monkeypatch.setattr(api.scans, "process_batch", fake_process_batch)

    response = client.post("/api/scans", json={"scans": [VALID]})
    assert response.status_code == 200
    assert response.json() == {
        "results": [{"nonce": VALID["nonce"], "result": "applied", "quantity": 1}]
    }
    assert captured["raw_scans"] == [VALID]


def test_lookup_is_scheduled_once_per_distinct_barcode(client, monkeypatch):
    monkeypatch.setattr(api.scans, "process_batch", lambda conn, raw: [])
    asked: list[str] = []
    monkeypatch.setattr(api.lookup, "refresh_if_needed", asked.append)

    client.post(
        "/api/scans",
        json={
            "scans": [
                {"nonce": "a" * 16, "barcode": "111", "action": "add"},
                {"nonce": "b" * 16, "barcode": "111", "action": "add"},
                {"nonce": "c" * 16, "barcode": "222", "action": "remove"},
                {"nonce": "d" * 16, "barcode": "no good!", "action": "add"},
                {"nonce": "e" * 16, "barcode": "", "action": "add"},
                {"nonce": "f" * 16, "barcode": 12345, "action": "add"},
                "not even an object",
            ]
        },
    )
    assert asked == ["111", "222"]


@pytest.mark.parametrize(
    ("barcode", "ok"),
    [
        ("041196891010", True),
        ("a", True),
        ("A-1", True),
        ("x" * 32, True),
        ("x" * 33, False),
        ("", False),
        ("has space", False),
        ("sym+bol", False),
        ("new\nline", False),  # \Z, not $: a trailing newline must not sneak through
    ],
)
def test_barcode_pattern(barcode, ok):
    assert bool(api.BARCODE_PATTERN.match(barcode)) is ok
