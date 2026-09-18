"""Every error comes back in the shape from spec section 4."""

from __future__ import annotations


def error_body(response) -> dict:
    body = response.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "message"}, body
    return body["error"]


def test_unknown_path_is_not_found(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert error_body(response)["code"] == "not_found"


def test_unknown_path_outside_api_is_not_found(client):
    # No static mount yet, so anything off /api/ should still use our shape.
    response = client.get("/nope")
    assert response.status_code == 404
    assert error_body(response)["code"] == "not_found"


def test_error_message_is_a_string(client):
    error = error_body(client.get("/api/nope"))
    assert isinstance(error["message"], str)
    assert error["message"]
