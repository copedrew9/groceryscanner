"""Open Food Facts product lookup, spec 6.7.

Stub until stage 4. The route already schedules it as a background task, so
wiring it up later changes nothing about how it is called.
"""

from __future__ import annotations


def refresh_if_needed(barcode: str) -> None:
    """Decide whether this barcode needs a lookup, and do it if so."""
    return None
