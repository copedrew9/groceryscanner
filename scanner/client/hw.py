"""Hardware and OS seam. Spec 5.9, as restated by addendum 15.2.

pyserial, gpiod and httpx appear in this file and nowhere else. The logic
modules -- frame, queue, backoff, proto -- never read the clock and never touch
hardware; they take the current time as a parameter. That is what lets them be
unit-tested on a laptop with no Pi attached (criterion 13).

Everything here degrades to a no-op rather than raising when the hardware is
absent, so the same code runs on a laptop for development.
"""

from __future__ import annotations

import logging
import secrets
import time
from enum import Enum, auto

import httpx
import serial

log = logging.getLogger(__name__)

# Spec 5.7: both timeouts explicit. Without them a dead server hangs the loop.
# These are the two curl options the v2.0 spec named, in httpx's form.
HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

SERIAL_BAUD = 9600
READ_CHUNK_BYTES = 256


class Signal(Enum):
    """Spec 5.8. What the buzzer and LED are saying."""

    ADD = auto()
    REMOVE = auto()
    REJECTED = auto()
    QUEUED = auto()
    OVERFLOW = auto()
    AUTH_FAILED = auto()


# (beeps, seconds on, seconds off). Spec 5.8's table, made concrete.
_PATTERNS: dict[Signal, tuple[int, float, float]] = {
    Signal.ADD: (1, 0.06, 0.0),
    Signal.REMOVE: (2, 0.06, 0.08),
    Signal.REJECTED: (1, 0.60, 0.0),
    Signal.QUEUED: (2, 0.04, 0.30),
    Signal.OVERFLOW: (3, 0.04, 0.04),
    Signal.AUTH_FAILED: (3, 0.70, 0.25),
}


# --- clock and randomness ----------------------------------------------------


def now_ms() -> int:
    """Milliseconds from a monotonic clock.

    Never the wall clock: the Pi has no battery-backed clock, so its wall time
    jumps when NTP syncs. A jump backwards would make a queued scan look like
    it arrived in the future and stall every retry.
    """
    return time.monotonic_ns() // 1_000_000


def new_nonce() -> str:
    """16 lowercase hex characters, from 8 cryptographically random bytes.

    Spec section 4. Called once when a barcode completes, never again on
    retry -- that is the client's whole half of deduplication.
    """
    return secrets.token_hex(8)


# --- serial ------------------------------------------------------------------


def serial_open(path: str) -> serial.Serial | None:
    """Open the scan module's port, or return None so the caller can back off.

    timeout=0 makes reads non-blocking, which is what lets one selectors loop
    own both the serial port and the retry schedule. pyserial configures the
    port raw, so the termios flags v2.0 spelled out (ICANON, ICRNL, IXON) are
    already clear.
    """
    try:
        port = serial.Serial(
            port=path,
            baudrate=SERIAL_BAUD,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0,          # non-blocking read
            write_timeout=0,
            rtscts=False,       # no flow control; the module has no handshake lines
            dsrdtr=False,
            xonxoff=False,      # or 0x11/0x13 would be eaten out of the data
        )
    except (serial.SerialException, OSError) as error:
        log.error("could not open %s: %s", path, error)
        return None
    log.info("serial port %s open at %d baud", path, SERIAL_BAUD)
    return port


def serial_read(port: serial.Serial, max_bytes: int = READ_CHUNK_BYTES) -> bytes:
    """Read whatever is waiting. b"" means nothing yet, not an error.

    Raises SerialException on hangup so the caller can close and reopen with
    backoff -- that is acceptance criterion 12, recovering from the module
    being unplugged and plugged back in.
    """
    waiting = port.in_waiting
    if waiting == 0:
        return b""
    return port.read(min(waiting, max_bytes))


def serial_close(port: serial.Serial | None) -> None:
    if port is None:
        return
    try:
        port.close()
    except Exception:  # noqa: BLE001 - closing a dead port must never propagate
        log.debug("ignoring error while closing serial port", exc_info=True)


# --- GPIO --------------------------------------------------------------------

# gpiod is imported lazily so this module still imports on a laptop, where the
# library is not installed and there are no GPIO lines to request.
_gpio_backend: "_GpioLines | None" = None
_gpio_tried = False


class _GpioLines:
    """Holds the requested lines for the life of the process.

    libgpiod releases a line as soon as its request is dropped, so these are
    kept rather than re-requested per read, which would race with anything else
    on the pin.

    buzzer and led share one request because they are both outputs on the same
    chip, and one request can set both in a single call.
    """

    def __init__(self, switch_request, output_request,
                 switch_pin: int, buzzer_pin: int, led_pin: int) -> None:
        self.switch = switch_request
        self.outputs = output_request
        self.switch_pin = switch_pin
        self.buzzer_pin = buzzer_pin
        self.led_pin = led_pin


def gpio_setup(switch_pin: int, buzzer_pin: int, led_pin: int) -> bool:
    """Request the three lines. False means run without GPIO.

    Returning False rather than raising is deliberate: a scanner with a broken
    buzzer should still count groceries.
    """
    global _gpio_backend, _gpio_tried
    _gpio_tried = True
    try:
        import gpiod  # noqa: PLC0415 - optional, Pi-only
    except ImportError:
        log.warning("gpiod is not installed; switch reads as 'add' and feedback is off")
        return False

    try:
        chip = gpiod.Chip("gpiochip0")
        # Pull-up on the switch: spec 3 wires one pole to ground, so closed
        # reads low and means remove.
        switch = chip.request_lines(
            consumer="pantry-scanner",
            config={switch_pin: gpiod.LineSettings(
                direction=gpiod.line.Direction.INPUT,
                bias=gpiod.line.Bias.PULL_UP,
            )},
        )
        outputs = chip.request_lines(
            consumer="pantry-scanner",
            config={
                buzzer_pin: gpiod.LineSettings(direction=gpiod.line.Direction.OUTPUT),
                led_pin: gpiod.LineSettings(direction=gpiod.line.Direction.OUTPUT),
            },
        )
        _gpio_backend = _GpioLines(switch, outputs, switch_pin, buzzer_pin, led_pin)
    except Exception as error:  # noqa: BLE001 - any GPIO failure means run without it
        log.warning("GPIO unavailable (%s); continuing without switch or feedback", error)
        _gpio_backend = None
        return False

    log.info("GPIO ready: switch=%d buzzer=%d led=%d", switch_pin, buzzer_pin, led_pin)
    return True


def switch_is_remove() -> bool:
    """Spec 5.4: a level read, taken at the moment a barcode completes.

    No edge detection and no debounce. The switch has been in position since
    before the barcode was aimed, so the level is stable; an interrupt handler
    would add a race for nothing.

    Reads 'add' when there is no GPIO, so a laptop without hardware still works.
    """
    if _gpio_backend is None:
        return False
    try:
        import gpiod  # noqa: PLC0415

        value = _gpio_backend.switch.get_value(_gpio_backend.switch_pin)
        # Pulled up, switched to ground: closed (low) means remove.
        return value == gpiod.line.Value.INACTIVE
    except Exception:  # noqa: BLE001
        log.warning("could not read the mode switch; treating as 'add'", exc_info=True)
        return False


def signal(sig: Signal, enabled: bool = True) -> None:
    """Spec 5.8. Blocking, because the longest pattern is under three seconds
    and the loop has nothing useful to do while the person is still scanning.
    """
    if not enabled or _gpio_backend is None:
        return
    beeps, on_seconds, off_seconds = _PATTERNS[sig]
    try:
        import gpiod  # noqa: PLC0415

        on = gpiod.line.Value.ACTIVE
        off = gpiod.line.Value.INACTIVE
        lines = _gpio_backend.outputs
        for index in range(beeps):
            lines.set_values({
                _gpio_backend.buzzer_pin: on, _gpio_backend.led_pin: on,
            })
            time.sleep(on_seconds)
            lines.set_values({
                _gpio_backend.buzzer_pin: off, _gpio_backend.led_pin: off,
            })
            if off_seconds and index < beeps - 1:
                time.sleep(off_seconds)
    except Exception:  # noqa: BLE001 - feedback is never worth crashing over
        log.debug("signal %s failed", sig, exc_info=True)


def gpio_close() -> None:
    global _gpio_backend
    if _gpio_backend is None:
        return
    for request in (_gpio_backend.switch, _gpio_backend.outputs):
        try:
            request.release()
        except Exception:  # noqa: BLE001
            log.debug("ignoring error while releasing GPIO", exc_info=True)
    _gpio_backend = None


# --- HTTP --------------------------------------------------------------------


class Http:
    """One client for the life of the process, so the TCP connection is reused.

    Spec 5.7 asked for a single reused curl easy handle for exactly this
    reason: a new connection per batch costs a handshake every retry.
    """

    def __init__(
        self,
        timeout: httpx.Timeout | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # transport is a seam for tests, which hand in httpx.MockTransport so
        # no test ever opens a socket.
        self._client = httpx.Client(
            timeout=timeout or HTTP_TIMEOUT, transport=transport
        )

    def post_scans(self, url: str, token: str, body: dict) -> tuple[int | None, dict | None]:
        """POST one batch.

        Returns (status, parsed body). status is None when the request never
        got an answer -- spec section 4 client rule 3: only the absence of a
        result means retry, so a transport failure and an HTTP error are
        different things and the caller is told which happened.
        """
        try:
            response = self._client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as error:
            log.warning("POST %s failed: %s", url, error)
            return None, None

        try:
            parsed = response.json()
        except ValueError:
            # A malformed response is a failed request, not a crash. Spec 5.7.
            log.warning("POST %s returned %d with a body that is not JSON",
                        url, response.status_code)
            return response.status_code, None

        return response.status_code, parsed if isinstance(parsed, dict) else None

    def close(self) -> None:
        self._client.close()
