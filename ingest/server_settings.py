"""Read and persist the PostgreSQL settings relevant to bulk loading."""

from __future__ import annotations

from dataclasses import dataclass

from .config import PostgresSettings

SETTING_NAMES = ("maintenance_work_mem", "max_wal_size", "checkpoint_timeout")


class ServerSettingsError(RuntimeError):
    """Raised when the server settings cannot be read or changed."""


@dataclass(frozen=True)
class ServerSetting:
    """One setting as reported by ``pg_settings``."""

    name: str
    setting: str
    unit: str | None
    source: str
    pending_restart: bool

    @property
    def value(self) -> str:
        """Format the value exactly enough to be useful in a configuration file."""

        return f"{self.setting}{self.unit or ''}"


def read(settings: PostgresSettings) -> tuple[ServerSetting, ...]:
    """Return the active bulk-load-related configuration values."""

    try:
        import psycopg

        with (
            psycopg.connect(**_connection_arguments(settings)) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                    SELECT name, setting, unit, source, pending_restart
                    FROM pg_settings
                    WHERE name = ANY(%s)
                    ORDER BY array_position(%s::text[], name)
                    """,
                (list(SETTING_NAMES), list(SETTING_NAMES)),
            )
            rows = cursor.fetchall()
    except Exception as error:
        raise ServerSettingsError(
            f"Could not read PostgreSQL server settings: {error}"
        ) from error

    return tuple(ServerSetting(*row) for row in rows)


def update(
    settings: PostgresSettings, values: dict[str, str | None]
) -> tuple[bool, tuple[ServerSetting, ...]]:
    """Persist selected settings with ``ALTER SYSTEM`` and reload the config.

    The parameter names are selected from a fixed allow-list before interpolating
    them into SQL; values themselves are always bound parameters.
    """

    changes = tuple(
        (name, value) for name, value in values.items() if value is not None
    )
    if not changes:
        raise ServerSettingsError("set-settings requires at least one setting value")

    invalid_names = set(values).difference(SETTING_NAMES)
    if invalid_names:
        raise ServerSettingsError(
            f"Unsupported server setting: {', '.join(sorted(invalid_names))}"
        )

    try:
        import psycopg
        from psycopg import sql

        with (
            psycopg.connect(
                **_connection_arguments(settings), autocommit=True
            ) as connection,
            connection.cursor() as cursor,
        ):
            for name, value in changes:
                # ALTER SYSTEM does not accept a parameter placeholder for
                # its value.  Compose a SQL literal instead; ``name`` has
                # already been checked against SETTING_NAMES above.
                statement = sql.SQL("ALTER SYSTEM SET {} TO {}").format(
                    sql.Identifier(name),
                    sql.Literal(value),
                )
                cursor.execute(statement)
            cursor.execute("SELECT pg_reload_conf()")
            reloaded = bool(cursor.fetchone()[0])
    except Exception as error:
        raise ServerSettingsError(
            f"Could not update PostgreSQL server settings: {error}"
        ) from error

    return reloaded, read(settings)


def format_settings(rows: tuple[ServerSetting, ...]) -> str:
    """Render settings in a compact, dependency-free table."""

    headers = ("name", "value", "source", "pending restart")
    body = [
        (row.name, row.value, row.source, "yes" if row.pending_restart else "no")
        for row in rows
    ]
    widths = [
        max(len(header), *(len(row[index]) for row in body))
        for index, header in enumerate(headers)
    ]
    lines = [
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        "  ".join("-" * width for width in widths),
    ]
    lines.extend(
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in body
    )
    return "\n".join(lines)


def _connection_arguments(settings: PostgresSettings) -> dict[str, str]:
    return {
        "host": settings.host,
        "port": settings.port,
        "user": settings.user,
        "dbname": settings.database,
        "password": settings.password,
    }
