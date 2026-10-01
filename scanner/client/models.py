"""The record a scan becomes once it has been read off the wire. Spec 5.5."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    """Spec section 4: the only two values the server accepts.

    Subclassing str means json.dumps writes "add", not "Action.ADD".
    """

    ADD = "add"
    REMOVE = "remove"


@dataclass
class ScanRecord:
    """One scan waiting to be acknowledged.

    nonce is the 16 lowercase hex characters the server expects, generated once
    when the barcode was scanned. Spec section 4, client rule 1: it is never
    regenerated on retry. Regenerating it defeats deduplication and silently
    multiplies inventory, which is acceptance criterion 10.

    queued_ms comes from hw.now_ms(), a monotonic clock. The Pi has no
    battery-backed clock and its wall clock jumps when NTP syncs.
    """

    nonce: str
    barcode: str
    action: Action
    queued_ms: int
