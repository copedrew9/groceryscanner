"""Retry timing. Spec 5.6.
Written by hand; do not edit.
"""

from __future__ import annotations

# Spec 5.6, verbatim.
BACKOFF_MS = (0, 1000, 2000, 5000, 15000, 30000, 60000, 120000)

# Spec 5.1: the loop's timeout when nothing is due.
IDLE_TIMEOUT_MS = 60000


class Backoff:
    """Decides when the next send attempt is due.

    Takes the clock as a parameter and never reads it (criterion 13).

    Spec 5.6:
      - the index advances on failure and resets to 0 on success
      - a new scan sets the next attempt to NOW but does NOT reset the index

    That second rule is the subtle one. Resetting the index on a new scan means
    scanning repeatedly during a real outage produces a flood of requests
    instead of backing off.
    """

    def on_success(self, now_ms: int) -> None:
        raise NotImplementedError

    def on_failure(self, now_ms: int) -> None:
        raise NotImplementedError

    def on_new_scan(self, now_ms: int) -> None:
        """Next attempt is due now. The index is left where it is."""
        raise NotImplementedError

    def is_due(self, now_ms: int) -> bool:
        raise NotImplementedError

    def ms_until_due(self, now_ms: int) -> int:
        """What to hand the selectors loop as its timeout. IDLE_TIMEOUT_MS
        when nothing is queued."""
        raise NotImplementedError
