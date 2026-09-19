"""Shared fixtures.

API_TOKEN is set before app.main is imported, because that module reads it at
import time and refuses to load without it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# pytest puts tests/ on sys.path, not server/, so point it at server/ ourselves.
SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

TEST_TOKEN = "0123456789abcdef" * 4  # 64 chars, comfortably over the minimum
os.environ["API_TOKEN"] = TEST_TOKEN

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402


@pytest.fixture
def db_file(tmp_path, monkeypatch) -> Path:
    """A temp database with migrations applied, wired up as DB_PATH."""
    path = tmp_path / "inventory.db"
    monkeypatch.setenv("DB_PATH", str(path))
    db.initialize(str(path))
    return path


@pytest.fixture
def conn(db_file):
    """A connection to the temp database, closed at the end of the test."""
    connection = db.connect(str(db_file))
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def client(db_file):
    """TestClient with the token header set."""
    from app.main import app

    # The context manager runs the lifespan, so startup is exercised too.
    with TestClient(app) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {TEST_TOKEN}"})
        yield test_client


@pytest.fixture
def client_no_auth(db_file):
    """TestClient with no Authorization header."""
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
