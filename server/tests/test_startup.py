"""The server refuses to start without a long enough token. Spec 6.1."""

from __future__ import annotations

import pytest

from app import main


@pytest.mark.parametrize("token", ["", "short", "0123456789abcdef0123456789abcde"])
def test_missing_or_short_token_refuses(monkeypatch, token):
    monkeypatch.setenv("API_TOKEN", token)
    with pytest.raises(RuntimeError):
        main.load_api_token()


def test_unset_token_refuses(monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        main.load_api_token()


def test_token_of_minimum_length_is_accepted(monkeypatch):
    token = "a" * main.MIN_TOKEN_LENGTH
    monkeypatch.setenv("API_TOKEN", token)
    assert main.load_api_token() == token
