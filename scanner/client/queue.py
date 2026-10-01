"""In-flight buffer: scans waiting to be acknowledged. Spec 5.5.
Written by hand; do not edit.
"""

from __future__ import annotations

from client.models import ScanRecord

# Spec 5.5. Protocol-visible: the scanner never sends a batch larger than this.
CAPACITY = 32


class ScanQueue:
    """RAM only. Does not survive a power cut, which is fine for a pantry.

    Spec 5.5: on overflow, drop the OLDEST. During an outage the most recent
    scans matter more. The caller logs at ERROR and signals on the buzzer,
    because a full buffer means lost data and the person scanning should know.
    That is acceptance criterion 11.
    """

    def add(self, record: ScanRecord) -> ScanRecord | None:
        """Queue a scan. Returns the record dropped to make room, or None."""
        raise NotImplementedError

    def batch(self, limit: int = CAPACITY) -> list[ScanRecord]:
        """The next scans to send, oldest first. Does not remove them:
        a slot is only freed once the server has answered for it."""
        raise NotImplementedError

    def release(self, nonce: str) -> bool:
        """Free the slot holding this nonce. Spec section 4, client rule 3:
        a result of ANY kind frees the slot, including 'duplicate' and
        'invalid'. Returns whether anything was holding it."""
        raise NotImplementedError

    def __len__(self) -> int:
        raise NotImplementedError
