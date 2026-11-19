"""Shared PostGIS DDL helpers."""

from __future__ import annotations

import hashlib
from urllib.parse import quote

from .config import PostgresSettings
from .ogr2ogr import IngestionError, _validate_identifier


def drop_table_statement(table: str) -> str:
    """Quote the actual destination name without cascading to other objects."""

    _validate_identifier(table)
    return f'DROP TABLE IF EXISTS "{table}";'


def drop_ingested_table(settings: PostgresSettings, table: str) -> None:
    """Drop a successfully loaded table, outside the ingestion timer."""

    statement = drop_table_statement(table)
    try:
        import psycopg

        with psycopg.connect(
            host=settings.host,
            port=settings.port,
            user=settings.user,
            dbname=settings.database,
            password=settings.password,
        ) as connection:
            connection.execute(statement)
    except Exception as error:
        message = str(error).replace(settings.password, "<redacted>").replace(
            quote(settings.password, safe=""), "<redacted>"
        )
        raise IngestionError(f"Cleanup failed for table {table!r}: {message}") from error


def spatial_index_statement(table: str, geometry_column: str = "geometry") -> str:
    """Return a GiST index statement with a PostgreSQL-safe index name."""

    base_name = f"{table}_{geometry_column}_gist"
    if len(base_name) > 63:
        suffix = hashlib.sha256(base_name.encode()).hexdigest()[:8]
        base_name = f"{base_name[:54]}_{suffix}"
    return f"CREATE INDEX {base_name} ON {table} USING GIST ({geometry_column})"
