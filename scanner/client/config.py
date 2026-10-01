"""Configuration read from .env at startup. Spec 5.10.

Parsed by hand rather than with python-dotenv: the file has seven keys and a
fixed shape, and one less dependency on the Pi is one less thing to install.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ENV_PATH = Path(".env")


@dataclass(frozen=True)
class Config:
    server_url: str
    api_token: str
    serial_port: str
    switch_gpio: int
    buzzer_gpio: int
    led_gpio: int
    feedback_enabled: bool

    @property
    def scans_url(self) -> str:
        """The one endpoint the scanner talks to. Spec section 4."""
        return f"{self.server_url.rstrip('/')}/api/scans"


class ConfigError(Exception):
    """Raised at startup so a misconfigured scanner never starts scanning."""


def parse_env_file(text: str) -> dict[str, str]:
    """KEY=value per line. Blank lines and # comments ignored.

    Values are taken literally, including spaces; only surrounding quotes are
    stripped, because a token pasted from a shell often arrives wrapped.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue  # not a setting; ignore rather than fail the whole file
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _required(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise ConfigError(f"{key} is missing from the environment or .env")
    return value


def _gpio(values: dict[str, str], key: str, default: int) -> int:
    raw = values.get(key, "").strip()
    if not raw:
        return default
    try:
        number = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a GPIO number, got {raw!r}") from None
    if not 0 <= number <= 27:
        raise ConfigError(f"{key} must be a BCM pin between 0 and 27, got {number}")
    return number


def _flag(values: dict[str, str], key: str, default: bool) -> bool:
    raw = values.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def load(env_path: Path | None = None) -> Config:
    """Read .env, let real environment variables win, then validate.

    The environment overriding the file is what lets a systemd unit or a test
    change one setting without rewriting the file.
    """
    path = env_path if env_path is not None else DEFAULT_ENV_PATH
    values: dict[str, str] = {}
    if path.is_file():
        values.update(parse_env_file(path.read_text(encoding="utf-8")))
    for key in (
        "SERVER_URL", "API_TOKEN", "SERIAL_PORT",
        "SWITCH_GPIO", "BUZZER_GPIO", "LED_GPIO", "FEEDBACK_ENABLED",
    ):
        if key in os.environ:
            values[key] = os.environ[key]

    server_url = _required(values, "SERVER_URL")
    if not server_url.startswith(("http://", "https://")):
        raise ConfigError(f"SERVER_URL must start with http:// or https://, got {server_url!r}")

    return Config(
        server_url=server_url,
        api_token=_required(values, "API_TOKEN"),
        serial_port=values.get("SERIAL_PORT", "").strip() or "/dev/serial0",
        switch_gpio=_gpio(values, "SWITCH_GPIO", 17),
        buzzer_gpio=_gpio(values, "BUZZER_GPIO", 27),
        led_gpio=_gpio(values, "LED_GPIO", 22),
        feedback_enabled=_flag(values, "FEEDBACK_ENABLED", True),
    )
