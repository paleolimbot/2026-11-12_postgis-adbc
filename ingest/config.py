"""Configuration shared by every ingestion implementation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when the Postgres configuration is incomplete."""


@dataclass(frozen=True)
class PostgresSettings:
    """Connection settings read from ``.env`` and the process environment."""

    host: str
    port: str
    user: str
    database: str
    password: str

    def ogr_connection_string(self) -> str:
        """Return a libpq connection string without the password.

        The password is deliberately supplied through ``PGPASSWORD`` instead,
        so it never appears in a command line, process list, or dry-run output.
        """

        return " ".join(
            (
                f"host={_conninfo_value(self.host)}",
                f"port={_conninfo_value(self.port)}",
                f"dbname={_conninfo_value(self.database)}",
                f"user={_conninfo_value(self.user)}",
            )
        )


def load_postgres_settings(env_file: Path) -> PostgresSettings:
    """Load Postgres settings, letting explicit environment values win.

    Keeping this parser dependency-free makes the ingestion commands runnable
    with the system Python; it implements the simple ``KEY=value`` format used
    by this repository's ``.env`` file.
    """

    file_values = _read_dotenv(env_file)
    values = {**file_values, **{key: value for key, value in os.environ.items() if key.startswith("POSTGRES_")}}

    required = ("POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD")
    missing = [key for key in required if not values.get(key)]
    if missing:
        source = str(env_file)
        raise ConfigurationError(
            f"Missing {', '.join(missing)}. Set it in {source} or the environment."
        )

    return PostgresSettings(
        host=values.get("POSTGRES_HOST", "127.0.0.1"),
        port=values.get("POSTGRES_PORT", "5432"),
        user=values["POSTGRES_USER"],
        database=values["POSTGRES_DB"],
        password=values["POSTGRES_PASSWORD"],
    )


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            raise ConfigurationError(f"Invalid .env entry at {path}:{number}")
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _conninfo_value(value: str) -> str:
    """Quote a libpq conninfo value when a value needs quoting."""

    if value and not any(character.isspace() or character in "'\\" for character in value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
