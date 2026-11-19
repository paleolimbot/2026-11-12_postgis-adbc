"""A DuckDB PostgreSQL-binary COPY writer with psql-based loading."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from .config import PostgresSettings
from .ogr2ogr import DATASETS, IngestionError, _validate_identifier
from .postgis import spatial_index_statement


@dataclass(frozen=True)
class IngestionResult:
    """The hybrid-ingestion steps that ran and their elapsed wall-clock time."""

    statements: list[str]
    elapsed_seconds: float | None


def build_statements(
    dataset: str,
    table: str | None = None,
    partitions: int = 1,
    spatial_index: bool = False,
) -> tuple[Path, str, list[str]]:
    """Build a password-free description of one hybrid COPY load."""

    if partitions < 1:
        raise IngestionError("--partitions must be at least 1.")

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

    target_table = table or f"{definition['table'].removesuffix('_ogr2ogr')}_duckdb_copy"
    _validate_identifier(target_table)
    statements = [
        (
            "DuckDB: COPY (SELECT *, row_number() OVER () % "
            f"{partitions} AS _copy_bucket FROM read_parquet(...)) TO <temporary directory> "
            "(FORMAT postgres_binary, PARTITION_BY (_copy_bucket), "
            "WRITE_PARTITION_COLUMNS false);"
        ),
        f"psql: DROP TABLE IF EXISTS {target_table}; CREATE TABLE {target_table} (...);",
        f"psql ({partitions} concurrent clients): \\copy {target_table} FROM <each .bin file> WITH (FORMAT binary);",
    ]
    if spatial_index:
        statements.append(f"psql: {spatial_index_statement(target_table)};")
    return source, target_table, statements


def run(
    settings: PostgresSettings,
    dataset: str,
    table: str | None = None,
    partitions: int = 1,
    psql_bin: str = "psql",
    spatial_index: bool = False,
    dry_run: bool = False,
) -> IngestionResult:
    """Write partitioned PostgreSQL binary COPY files with DuckDB and load them.

    The intermediate files use DuckDB's ``postgres_binary`` writer. Each is
    loaded with psql's client-side ``\\copy`` command, so Postgres never needs
    access to the temporary directory on the client machine.
    """

    source, target_table, statements = build_statements(dataset, table, partitions, spatial_index)
    if dry_run:
        return IngestionResult(statements=statements, elapsed_seconds=None)
    if shutil.which(psql_bin) is None:
        raise IngestionError(f"Could not find {psql_bin!r}. Install PostgreSQL client tools first.")

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
        # A partitioned COPY normally writes one file per writer thread. One
        # thread makes --partitions a predictable number of output files.
        connection.execute("SET threads = 1")
        column_types = connection.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(source)]
        ).fetchall()
        create_table = _create_table_statement(target_table, column_types)

        with tempfile.TemporaryDirectory(prefix="postgis-duckdb-copy-") as temporary_directory:
            output_directory = Path(temporary_directory) / "copy-files"
            started_at = time.perf_counter()
            connection.execute(
                f"""
                COPY (
                    SELECT *, row_number() OVER () % {partitions} AS _copy_bucket
                    FROM read_parquet(?)
                ) TO ? (
                    FORMAT postgres_binary,
                    PARTITION_BY (_copy_bucket),
                    WRITE_PARTITION_COLUMNS false,
                    FILENAME_PATTERN 'part_{{i}}',
                    FILE_EXTENSION 'bin'
                )
                """,
                [str(source), str(output_directory)],
            )
            copy_files = sorted(output_directory.rglob("*.bin"))
            if not copy_files:
                raise IngestionError("DuckDB did not create any PostgreSQL binary COPY files.")

            _run_psql(settings, psql_bin, f"DROP TABLE IF EXISTS {target_table}; {create_table}")
            with ThreadPoolExecutor(max_workers=min(partitions, len(copy_files))) as executor:
                futures = [
                    executor.submit(_copy_file, settings, psql_bin, target_table, copy_file)
                    for copy_file in copy_files
                ]
                for future in futures:
                    future.result()
            if spatial_index:
                _run_psql(settings, psql_bin, spatial_index_statement(target_table))
    except (duckdb.Error, OSError, subprocess.CalledProcessError) as error:
        raise IngestionError(f"DuckDB COPY ingestion failed: {error}") from error
    finally:
        connection.close()

    return IngestionResult(statements=statements, elapsed_seconds=time.perf_counter() - started_at)


def _create_table_statement(table: str, columns: list[tuple[object, ...]]) -> str:
    definitions = []
    for column in columns:
        name, duckdb_type = str(column[0]), str(column[1])
        definitions.append(f"{_quote_identifier(name)} {_postgres_type(name, duckdb_type)}")
    return f"CREATE TABLE {table} ({', '.join(definitions)})"


def _postgres_type(name: str, duckdb_type: str) -> str:
    """Map the flat DuckDB types supported by PostgreSQL binary COPY."""

    if name == "geometry" and duckdb_type.startswith("GEOMETRY"):
        return "geometry"
    if duckdb_type == "VARCHAR":
        return "text"
    if duckdb_type == "BOOLEAN":
        return "boolean"
    if duckdb_type in {"TINYINT", "SMALLINT"}:
        return "smallint"
    if duckdb_type == "INTEGER":
        return "integer"
    if duckdb_type == "BIGINT":
        return "bigint"
    if duckdb_type == "FLOAT":
        return "real"
    if duckdb_type == "DOUBLE":
        return "double precision"
    if duckdb_type == "BLOB":
        return "bytea"
    raise IngestionError(
        f"DuckDB COPY ingestion does not yet map column {name!r} with DuckDB type {duckdb_type!r}."
    )


def _copy_file(
    settings: PostgresSettings,
    psql_bin: str,
    table: str,
    copy_file: Path,
) -> None:
    file_literal = str(copy_file).replace("'", "''")
    _run_psql(settings, psql_bin, f"\\copy {table} FROM '{file_literal}' WITH (FORMAT binary)")


def _run_psql(settings: PostgresSettings, psql_bin: str, command: str) -> None:
    environment = os.environ.copy()
    environment["PGPASSWORD"] = settings.password
    subprocess.run(
        [
            psql_bin,
            "--no-password",
            "--set",
            "ON_ERROR_STOP=1",
            "--host",
            settings.host,
            "--port",
            settings.port,
            "--username",
            settings.user,
            "--dbname",
            settings.database,
            "--command",
            command,
        ],
        check=True,
        env=environment,
    )


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
