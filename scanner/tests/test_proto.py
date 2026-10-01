"""Request building and response parsing. Spec section 4 and 5.7."""

from __future__ import annotations

import json

import pytest

from client import proto
from client.models import Action, ScanRecord


def record(nonce="3f8a1c9d2b4e6f70", barcode="041196891010", action=Action.ADD):
    return ScanRecord(nonce=nonce, barcode=barcode, action=action, queued_ms=0)


# --- building -----------------------------------------------------------------


def test_request_matches_the_spec_example():
    body = proto.build_request([record()])
    assert body == {
        "scans": [
            {"nonce": "3f8a1c9d2b4e6f70", "barcode": "041196891010", "action": "add"}
        ]
    }


def test_the_action_serialises_as_a_plain_string():
    """Action subclasses str, so json.dumps must not write 'Action.REMOVE'."""
    body = proto.build_request([record(action=Action.REMOVE)])
    assert json.loads(json.dumps(body))["scans"][0]["action"] == "remove"


def test_queue_order_is_preserved():
    records = [record(nonce=f"{i:016x}") for i in range(5)]
    body = proto.build_request(records)
    assert [s["nonce"] for s in body["scans"]] == [f"{i:016x}" for i in range(5)]


def test_an_empty_batch_is_a_caller_bug():
    with pytest.raises(ValueError):
        proto.build_request([])


def test_an_oversized_batch_is_a_caller_bug():
    with pytest.raises(ValueError):
        proto.build_request([record(nonce=f"{i:016x}") for i in range(101)])


def test_one_hundred_is_allowed():
    assert len(proto.build_request(
        [record(nonce=f"{i:016x}") for i in range(100)])["scans"]) == 100


# --- barcode validation -------------------------------------------------------


@pytest.mark.parametrize(
    ("barcode", "ok"),
    [("041196891010", True), ("a", True), ("A-1", True), ("x" * 32, True),
     ("x" * 33, False), ("", False), ("has space", False), ("sym+bol", False),
     ("trailing\n", False)],
)
def test_barcode_validation_matches_the_server(barcode, ok):
    assert proto.is_valid_barcode(barcode) is ok


# --- parsing ------------------------------------------------------------------


def test_parses_the_spec_example():
    body = {"results": [
        {"nonce": "3f8a1c9d2b4e6f70", "result": "applied", "quantity": 3}
    ]}
    assert proto.parse_results(body) == {"3f8a1c9d2b4e6f70": "applied"}


def test_parses_every_result_kind():
    body = {"results": [
        {"nonce": "0" * 16, "result": "applied", "quantity": 1},
        {"nonce": "1" * 16, "result": "rejected_not_in_stock", "quantity": 0},
        {"nonce": "2" * 16, "result": "duplicate", "quantity": 4},
        {"nonce": "3" * 16, "result": "invalid"},
    ]}
    assert proto.parse_results(body) == {
        "0" * 16: "applied",
        "1" * 16: "rejected_not_in_stock",
        "2" * 16: "duplicate",
        "3" * 16: "invalid",
    }


@pytest.mark.parametrize(
    "body",
    [None, [], "results", {}, {"results": None}, {"results": "nope"},
     {"results": {"nonce": "a"}}, 42],
)
def test_an_unusable_body_is_none_not_an_empty_map(body):
    """None must mean 'no answer', so the caller frees nothing and retries.
    An empty map would mean 'answered, nothing to free' -- a different thing."""
    assert proto.parse_results(body) is None


def test_an_empty_results_list_is_an_answer():
    assert proto.parse_results({"results": []}) == {}


@pytest.mark.parametrize(
    "element",
    [
        "not an object",
        {"result": "applied"},                       # no nonce
        {"nonce": "3f8a1c9d2b4e6f70"},               # no result
        {"nonce": None, "result": "applied"},
        {"nonce": "SHOUTING0BADHEX0", "result": "applied"},  # not lowercase hex
        {"nonce": "tooshort", "result": "applied"},
        {"nonce": "3f8a1c9d2b4e6f70", "result": "made_up"},  # unknown result
        {"nonce": "3f8a1c9d2b4e6f70", "result": 7},
    ],
)
def test_a_bad_element_is_skipped_not_fatal(element):
    """One malformed element must not strand the other 31 scans."""
    good = {"nonce": "0" * 16, "result": "applied", "quantity": 1}
    assert proto.parse_results({"results": [element, good]}) == {"0" * 16: "applied"}


# --- errors -------------------------------------------------------------------


def test_reads_the_error_code():
    body = {"error": {"code": "invalid_token", "message": "Missing or incorrect token"}}
    assert proto.error_code(body) == "invalid_token"


@pytest.mark.parametrize(
    "body", [None, {}, {"error": None}, {"error": "nope"}, {"error": {}},
             {"error": {"code": 5}}, "text"],
)
def test_a_missing_error_code_is_none(body):
    assert proto.error_code(body) is None


# --- feedback -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "action", "expected"),
    [
        ("applied", Action.ADD, "ADD"),
        ("applied", Action.REMOVE, "REMOVE"),
        ("rejected_not_in_stock", Action.ADD, "REJECTED"),
        ("rejected_not_in_stock", Action.REMOVE, "REJECTED"),
        # The server already had it, so the beep already happened.
        ("duplicate", Action.ADD, None),
        # Retrying will never help and there is nothing to tell the user.
        ("invalid", Action.ADD, None),
    ],
)
def test_signal_for_result(result, action, expected):
    assert proto.signal_for_result(result, action) == expected
