"""Reading and validating .env. Spec 5.10."""

from __future__ import annotations

import pytest

from client.config import Config, ConfigError, load, parse_env_file

MINIMAL = "SERVER_URL=http://miniserver.local:8080\nAPI_TOKEN=" + "a" * 64 + "\n"


def write_env(tmp_path, text: str):
    path = tmp_path / ".env"
    path.write_text(text)
    return path


def test_parses_key_values():
    values = parse_env_file("SERVER_URL=http://x:8080\nAPI_TOKEN=abc\n")
    assert values == {"SERVER_URL": "http://x:8080", "API_TOKEN": "abc"}


def test_ignores_comments_and_blank_lines():
    values = parse_env_file("# a comment\n\n  \nAPI_TOKEN=abc\n# trailing\n")
    assert values == {"API_TOKEN": "abc"}


def test_strips_surrounding_quotes():
    """A token pasted out of a shell often arrives wrapped."""
    assert parse_env_file('API_TOKEN="abc"\n')["API_TOKEN"] == "abc"
    assert parse_env_file("API_TOKEN='abc'\n")["API_TOKEN"] == "abc"


def test_keeps_an_equals_sign_inside_a_value():
    assert parse_env_file("API_TOKEN=a=b=c\n")["API_TOKEN"] == "a=b=c"


def test_ignores_a_line_with_no_equals():
    assert parse_env_file("nonsense\nAPI_TOKEN=abc\n") == {"API_TOKEN": "abc"}


def test_defaults_match_the_spec(tmp_path, monkeypatch):
    for key in ("SERIAL_PORT", "SWITCH_GPIO", "BUZZER_GPIO", "LED_GPIO",
                "FEEDBACK_ENABLED", "SERVER_URL", "API_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    config = load(write_env(tmp_path, MINIMAL))
    assert config.serial_port == "/dev/serial0"
    assert (config.switch_gpio, config.buzzer_gpio, config.led_gpio) == (17, 27, 22)
    assert config.feedback_enabled is True


def test_scans_url_is_built_from_server_url(tmp_path, monkeypatch):
    monkeypatch.delenv("SERVER_URL", raising=False)
    config = load(write_env(tmp_path, MINIMAL))
    assert config.scans_url == "http://miniserver.local:8080/api/scans"


def test_a_trailing_slash_does_not_double_up(tmp_path, monkeypatch):
    monkeypatch.delenv("SERVER_URL", raising=False)
    config = load(write_env(tmp_path, "SERVER_URL=http://x:8080/\nAPI_TOKEN=abc\n"))
    assert config.scans_url == "http://x:8080/api/scans"


@pytest.mark.parametrize("missing", ["SERVER_URL", "API_TOKEN"])
def test_a_missing_required_key_refuses(tmp_path, monkeypatch, missing):
    monkeypatch.delenv(missing, raising=False)
    text = "".join(line + "\n" for line in MINIMAL.splitlines()
                   if not line.startswith(missing))
    with pytest.raises(ConfigError):
        load(write_env(tmp_path, text))


def test_a_server_url_without_a_scheme_refuses(tmp_path, monkeypatch):
    monkeypatch.delenv("SERVER_URL", raising=False)
    with pytest.raises(ConfigError):
        load(write_env(tmp_path, "SERVER_URL=miniserver.local:8080\nAPI_TOKEN=abc\n"))


@pytest.mark.parametrize("value", ["nineteen", "-1", "99"])
def test_a_bad_gpio_number_refuses(tmp_path, monkeypatch, value):
    monkeypatch.delenv("SWITCH_GPIO", raising=False)
    with pytest.raises(ConfigError):
        load(write_env(tmp_path, MINIMAL + f"SWITCH_GPIO={value}\n"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), ("true", True), ("YES", True), ("on", True),
     ("0", False), ("false", False), ("no", False), ("", True)],
)
def test_feedback_flag(tmp_path, monkeypatch, raw, expected):
    monkeypatch.delenv("FEEDBACK_ENABLED", raising=False)
    config = load(write_env(tmp_path, MINIMAL + f"FEEDBACK_ENABLED={raw}\n"))
    assert config.feedback_enabled is expected


def test_the_environment_beats_the_file(tmp_path, monkeypatch):
    """So a systemd unit can override one setting without rewriting .env."""
    monkeypatch.setenv("SERIAL_PORT", "/dev/ttyUSB0")
    config = load(write_env(tmp_path, MINIMAL + "SERIAL_PORT=/dev/serial0\n"))
    assert config.serial_port == "/dev/ttyUSB0"


def test_a_missing_file_is_fine_when_the_environment_has_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVER_URL", "http://x:8080")
    monkeypatch.setenv("API_TOKEN", "abc")
    config = load(tmp_path / "does-not-exist")
    assert isinstance(config, Config)
