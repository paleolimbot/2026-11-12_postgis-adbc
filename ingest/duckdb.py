"""The DuckDB PostgreSQL and spatial extension ingestion implementation."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .config import PostgresSettings
from .ogr2ogr import DATASETS, IngestionError, _validate_identifier


@dataclass(frozen=True)
class IngestionResult:
    """The DuckDB statements that ran and their elapsed wall-clock time."""

    statements: list[str]
    elapsed_seconds: float | None


def build_statements(
    dataset: str,
    table: str | None = None,
) -> tuple[Path, str, list[str]]:
    """Build a password-free description of a DuckDB load for one dataset."""

    try:
        definition = DATASETS[dataset]
    except KeyError as error:
        choices = ", ".join(DATASETS)
        raise IngestionError(f"Unknown dataset {dataset!r}. Choose one of: {choices}.") from error

    source = definition["source"]
    if not source.is_file():
        raise IngestionError(
            f"Source data is missing: {source}. Download it first with ./data/fetch.sh."
        )

    # Dataset definitions currently name the ogr2ogr baseline explicitly.
    # Keep this route independent so both implementations can coexist.
    target_table = table or f"{definition['table'].removesuffix('_ogr2ogr')}_duckdb"
    _validate_identifier(target_table)

    source_literal = _sql_literal(str(source))
    statements = [
        "INSTALL postgres;",
        "LOAD postgres;",
        "INSTALL spatial;",
        "LOAD spatial;",
        "CREATE SECRET postgis_ingest (TYPE postgres, HOST ?, PORT ?, DATABASE ?, USER ?, PASSWORD '<redacted>');",
        "ATTACH '' AS postgis (TYPE postgres, SECRET postgis_ingest);",
        f"DROP TABLE IF EXISTS postgis.{target_table};",
        f"CREATE TABLE postgis.{target_table} AS SELECT * FROM read_parquet({source_literal});",
    ]
    return source, target_table, statements


def run(
    settings: PostgresSettings,
    dataset: str,
    table: str | None = None,
    dry_run: bool = False,
) -> IngestionResult:
    """Load GeoParquet through DuckDB directly into an attached PostGIS database.

    The spatial extension recognizes the GeoParquet geometry metadata; the
    PostgreSQL extension then creates the destination table in Postgres. A
    temporary DuckDB secret keeps the password out of both statements shown by
    ``--dry-run`` and connection-string error messages.
    """

    source, target_table, statements = build_statements(dataset, table)
    if dry_run:
        return IngestionResult(statements=statements, elapsed_seconds=None)

    try:
        import duckdb
    except ModuleNotFoundError as error:
        raise IngestionError(
            "Could not import duckdb. Install the project's runtime dependencies first."
        ) from error

    connection = duckdb.connect()
    try:
        connection.execute("INSTALL postgres; LOAD postgres;")
        connection.execute("INSTALL spatial; LOAD spatial;")
        connection.execute(
            """
            CREATE SECRET postgis_ingest (
                TYPE postgres,
                HOST ?,
                PORT ?,
                DATABASE ?,
                USER ?,
                PASSWORD ?
            )
            """,
            [settings.host, int(settings.port), settings.database, settings.user, settings.password],
        )
        connection.execute("ATTACH '' AS postgis (TYPE postgres, SECRET postgis_ingest)")
        connection.execute(f"DROP TABLE IF EXISTS postgis.{target_table}")

        started_at = time.perf_counter()
        connection.execute(
            f"CREATE TABLE postgis.{target_table} AS SELECT * FROM read_parquet(?)",
            [str(source)],
        )
    except duckdb.Error as error:
        raise IngestionError(f"DuckDB ingestion failed: {error}") from error
    finally:
        connection.close()

    return IngestionResult(statements=statements, elapsed_seconds=time.perf_counter() - started_at)


def _sql_literal(value: str) -> str:
    """Return a SQL string literal for a dry-run statement."""

    return "'" + value.replace("'", "''") + "'"
