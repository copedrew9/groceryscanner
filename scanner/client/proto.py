"""Wire protocol: building a request and reading a response. Spec section 4.

No clock, no hardware, no network -- just data in and data out. That is what
makes this module testable on a laptop (criterion 13).

v2.0 built the request with snprintf and parsed with cJSON. Python's json does
both, but the discipline the spec asked for still applies: every accessor on
the response is checked, because a malformed response is a failed request, not
a crash.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from client.models import Action, ScanRecord

# Spec section 4, the same constraints the server enforces.
NONCE_PATTERN = re.compile(r"\A[0-9a-f]{16}\Z")
BARCODE_PATTERN = re.compile(r"\A[A-Za-z0-9-]{1,32}\Z")

MAX_BATCH = 100

# Spec section 4, the four values the server can return.
RESULT_APPLIED = "applied"
RESULT_REJECTED = "rejected_not_in_stock"
RESULT_DUPLICATE = "duplicate"
RESULT_INVALID = "invalid"

KNOWN_RESULTS = frozenset(
    {RESULT_APPLIED, RESULT_REJECTED, RESULT_DUPLICATE, RESULT_INVALID}
)


def is_valid_barcode(barcode: str) -> bool:
    """Checked before queueing, so a misread never occupies a slot."""
    return bool(BARCODE_PATTERN.match(barcode))


def build_request(records: Sequence[ScanRecord]) -> dict[str, Any]:
    """The POST body for a batch, in queue order.

    Raises ValueError on an empty batch or one over the protocol maximum:
    both mean the caller has a bug, and sending them would just earn a 422.
    """
    if not records:
        raise ValueError("a batch needs at least one scan")
    if len(records) > MAX_BATCH:
        raise ValueError(f"a batch holds at most {MAX_BATCH} scans, got {len(records)}")

    return {
        "scans": [
            {
                "nonce": record.nonce,
                "barcode": record.barcode,
                "action": Action(record.action).value,
            }
            for record in records
        ]
    }


def parse_results(body: Any) -> dict[str, str] | None:
    """Map nonce -> result from a response body.

    Returns None when the body is not a usable response, which the caller must
    treat as no answer at all: spec section 4 client rule 3 says only the
    absence of a result means retry, so a body we cannot read has to leave
    every slot occupied rather than silently freeing it.

    Elements missing a nonce or carrying an unrecognised result are skipped
    rather than failing the whole response, so one malformed element cannot
    strand the other 31 scans in the buffer.
    """
    if not isinstance(body, dict):
        return None

    results = body.get("results")
    if not isinstance(results, list):
        return None

    parsed: dict[str, str] = {}
    for element in results:
        if not isinstance(element, dict):
            continue
        nonce = element.get("nonce")
        result = element.get("result")
        if not isinstance(nonce, str) or not NONCE_PATTERN.match(nonce):
            continue
        if not isinstance(result, str) or result not in KNOWN_RESULTS:
            continue
        parsed[nonce] = result
    return parsed


def error_code(body: Any) -> str | None:
    """The code from the server's error shape, or None if there isn't one.

    Spec section 4: clients switch on the code, never on the message text.
    """
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


def signal_for_result(result: str, action: Action) -> str | None:
    """Which feedback a result deserves. Spec 5.8.

    Returns the Signal member's name rather than the member itself, so this
    module stays free of any import from hw.
    """
    if result == RESULT_REJECTED:
        return "REJECTED"
    if result == RESULT_APPLIED:
        return "REMOVE" if Action(action) is Action.REMOVE else "ADD"
    # duplicate: the server already had it, so the beep already happened.
    # invalid: retrying will never help, and there is nothing to tell the user.
    return None
