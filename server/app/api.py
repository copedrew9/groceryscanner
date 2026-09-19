"""API routes (spec 6.6).

The router carries no prefix and no dependencies of its own. main.py mounts it
under /api with the token dependency attached, so every route added here is
protected by construction -- there is no way to forget it on a new route.

Routes arrive in later stages; the wiring exists now so the token check has
something to guard.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
