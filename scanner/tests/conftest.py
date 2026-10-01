"""Shared fixtures. Puts scanner/ on sys.path so `client` imports."""

from __future__ import annotations

import sys
from pathlib import Path

SCANNER_DIR = Path(__file__).resolve().parents[1]
if str(SCANNER_DIR) not in sys.path:
    sys.path.insert(0, str(SCANNER_DIR))
