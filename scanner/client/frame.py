"""Framing: bytes off the serial port become whole barcodes. Spec 5.3.
Written by hand; do not edit.
"""

from __future__ import annotations

BUFFER_BYTES = 64

# Spec 5.3: discard buffered bytes older than this with no terminator, or a
# partial read gets glued onto the front of the next barcode.
STALE_FRAME_MS = 200


class Framer:
    """Accumulates bytes and hands back complete barcodes.

    Takes the clock as a parameter and never reads it, so tests can pass
    whatever times they need (criterion 13).

    Rules from spec 5.3:
      - accept both CR and LF as terminators, so CRLF, CR-only and LF-only
        module settings all work
      - swallow empty frames
      - discard non-printable bytes
      - on input longer than BUFFER_BYTES, discard and resync at the next
        terminator
      - drop buffered bytes older than STALE_FRAME_MS with no terminator
    """

    def feed(self, chunk: bytes, now_ms: int) -> list[str]:
        """Add bytes read at now_ms; return every barcode completed by them."""
        raise NotImplementedError
