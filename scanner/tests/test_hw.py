"""The hardware seam. Spec 5.9.

The serial tests use a pty, which is a real character device pyserial opens
exactly as it opens /dev/serial0 -- so serial_open's termios configuration is
genuinely exercised, not mocked. The HTTP tests use httpx.MockTransport, so no
test opens a socket. GPIO is absent here, which is itself worth testing: the
client has to run on a laptop.
"""

from __future__ import annotations

import os
import re
import time

import httpx
import pytest

from client import hw


# --- clock and randomness -----------------------------------------------------


def test_now_ms_is_monotonic():
    first = hw.now_ms()
    time.sleep(0.01)
    assert hw.now_ms() >= first


def test_now_ms_is_not_the_wall_clock():
    """A wall-clock reading would be ~1.7e12 and would jump when NTP syncs."""
    assert hw.now_ms() < 10**12


def test_nonce_matches_the_wire_format():
    assert re.fullmatch(r"[0-9a-f]{16}", hw.new_nonce())


def test_nonces_do_not_repeat():
    assert len({hw.new_nonce() for _ in range(2000)}) == 2000


# --- serial -------------------------------------------------------------------


@pytest.fixture
def pty():
    """A pty pair: write to the master, the client reads from the slave."""
    master, slave = os.openpty()
    try:
        yield master, os.ttyname(slave)
    finally:
        os.close(master)
        os.close(slave)


def test_opens_a_real_character_device(pty):
    _, name = pty
    port = hw.serial_open(name)
    assert port is not None
    try:
        assert port.baudrate == 9600
        assert port.bytesize == 8
        assert port.parity == "N"
        assert port.stopbits == 1
        assert port.timeout == 0        # non-blocking
        assert port.xonxoff is False    # or 0x11/0x13 vanish from the data
        assert port.rtscts is False
    finally:
        hw.serial_close(port)


def test_a_missing_device_returns_none_rather_than_raising(caplog):
    """The caller reopens with backoff; it cannot do that if this raises."""
    assert hw.serial_open("/dev/definitely-not-here") is None


def test_reads_nothing_when_nothing_was_sent(pty):
    _, name = pty
    port = hw.serial_open(name)
    try:
        assert hw.serial_read(port) == b""
    finally:
        hw.serial_close(port)


def test_reads_what_was_written(pty):
    master, name = pty
    port = hw.serial_open(name)
    try:
        os.write(master, b"041196891010\r\n")
        time.sleep(0.05)
        assert hw.serial_read(port) == b"041196891010\r\n"
    finally:
        hw.serial_close(port)


def test_control_bytes_survive_the_read(pty):
    """ICRNL would rewrite \\r to \\n; IXON would eat 0x11 and 0x13."""
    master, name = pty
    port = hw.serial_open(name)
    try:
        os.write(master, b"\r\x11\x13\n")
        time.sleep(0.05)
        assert hw.serial_read(port) == b"\r\x11\x13\n"
    finally:
        hw.serial_close(port)


def test_a_long_read_is_chunked_not_truncated(pty):
    master, name = pty
    port = hw.serial_open(name)
    try:
        os.write(master, b"x" * 100)
        time.sleep(0.05)
        assert hw.serial_read(port, max_bytes=10) == b"x" * 10
        assert hw.serial_read(port, max_bytes=100) == b"x" * 90
    finally:
        hw.serial_close(port)


def test_closing_nothing_is_harmless():
    assert hw.serial_close(None) is None


# --- GPIO absent --------------------------------------------------------------


def test_gpio_setup_reports_failure_without_the_library():
    """No gpiod on a laptop. It must report, not raise."""
    assert hw.gpio_setup(17, 27, 22) is False


def test_the_switch_reads_as_add_without_gpio():
    assert hw.switch_is_remove() is False


@pytest.mark.parametrize("sig", list(hw.Signal))
def test_every_signal_is_a_no_op_without_gpio(sig):
    assert hw.signal(sig) is None


@pytest.mark.parametrize("sig", list(hw.Signal))
def test_every_signal_has_a_pattern(sig):
    beeps, on_seconds, _ = hw._PATTERNS[sig]
    assert beeps >= 1 and on_seconds > 0


def test_closing_gpio_that_was_never_opened_is_harmless():
    assert hw.gpio_close() is None


# --- HTTP ---------------------------------------------------------------------


def make_http(handler) -> hw.Http:
    return hw.Http(transport=httpx.MockTransport(handler))


BODY = {"scans": [{"nonce": "0" * 16, "barcode": "111", "action": "add"}]}


def test_posts_the_token_and_the_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["content_type"] = request.headers["content-type"]
        seen["body"] = request.content
        return httpx.Response(200, json={"results": []})

    http = make_http(handler)
    try:
        status, body = http.post_scans("http://x/api/scans", "tok", BODY)
    finally:
        http.close()

    assert status == 200
    assert body == {"results": []}
    assert seen["auth"] == "Bearer tok"
    assert seen["content_type"] == "application/json"
    assert b'"nonce"' in seen["body"]


def test_a_transport_failure_reports_no_status():
    """None means no answer at all, which is the only thing that means retry."""
    def handler(request):
        raise httpx.ConnectError("no route to host")

    http = make_http(handler)
    try:
        assert http.post_scans("http://x/api/scans", "tok", BODY) == (None, None)
    finally:
        http.close()


def test_a_timeout_reports_no_status():
    def handler(request):
        raise httpx.ReadTimeout("too slow")

    http = make_http(handler)
    try:
        assert http.post_scans("http://x/api/scans", "tok", BODY) == (None, None)
    finally:
        http.close()


def test_a_401_reports_its_status_and_body():
    """The caller must be able to tell 401 from a dead network: one is
    permanent, the other is retried. Spec section 4, client rule 4."""
    def handler(request):
        return httpx.Response(
            401, json={"error": {"code": "invalid_token", "message": "nope"}}
        )

    http = make_http(handler)
    try:
        status, body = http.post_scans("http://x/api/scans", "tok", BODY)
    finally:
        http.close()

    assert status == 401
    assert body["error"]["code"] == "invalid_token"


def test_a_body_that_is_not_json_is_not_a_crash():
    """Spec 5.7: a malformed response is a failed request, not a crash."""
    def handler(request):
        return httpx.Response(200, text="<html>502 Bad Gateway</html>")

    http = make_http(handler)
    try:
        assert http.post_scans("http://x/api/scans", "tok", BODY) == (200, None)
    finally:
        http.close()


def test_a_json_body_that_is_not_an_object_is_rejected():
    def handler(request):
        return httpx.Response(200, json=[1, 2, 3])

    http = make_http(handler)
    try:
        assert http.post_scans("http://x/api/scans", "tok", BODY) == (200, None)
    finally:
        http.close()


def test_the_timeouts_are_the_ones_the_spec_named():
    assert hw.HTTP_TIMEOUT.read == 10.0
    assert hw.HTTP_TIMEOUT.connect == 5.0
