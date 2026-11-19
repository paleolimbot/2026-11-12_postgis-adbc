"""The ADBC PostgreSQL ingestion implementation."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import time
from urllib.parse import quote

from .config import PostgresSettings
from .ogr2ogr import DATASETS, IngestionError, _validate_identifier
from .postgis import spatial_index_statement


@dataclass(frozen=True)
class IngestionResult:
    """The ADBC steps that ran and their elapsed wall-clock time."""

    statements: list[str]
    elapsed_seconds: float | None
    target_table: str


def build_statements(
    dataset: str,
    table: str | None = None,
    spatial_index: bool = False,
) -> tuple[Path, str, list[str]]:
    """Build a password-free description of an ADBC load for one dataset."""

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
    # Keep this route independent so all methods can coexist.
    target_table = table or f"{definition['table'].removesuffix('_ogr2ogr')}_adbc"
    _validate_identifier(target_table)
    geometry_column = _geometry_column(source)

    statements = [
        *_read_steps(source),
        f"ADBC: cursor.adbc_ingest('{target_table}', reader, mode='replace');",
        (
            f"ALTER TABLE {target_table} ALTER COLUMN {geometry_column} "
            f"TYPE geometry USING ST_GeomFromWKB({geometry_column});"
        ),
    ]
    if spatial_index:
        statements.append(f"{spatial_index_statement(target_table, geometry_column)};")
    return source, target_table, statements


def run(
    settings: PostgresSettings,
    dataset: str,
    table: str | None = None,
    spatial_index: bool = False,
    dry_run: bool = False,
) -> IngestionResult:
    """Bulk-create a bytea table with ADBC, then restore its geometry type.

    ADBC maps Arrow's binary type to PostgreSQL ``bytea`` during bulk ingest.
    GeoParquet and Pyogrio's FlatGeobuf Arrow stream represent geometry as WKB,
    so PostGIS can convert that temporary binary column in place with
    ``ST_GeomFromWKB`` after the bulk load.
    """

    source, target_table, statements = build_statements(dataset, table, spatial_index)
    if dry_run:
        return IngestionResult(statements=statements, target_table=target_table, elapsed_seconds=None)

    try:
        import adbc_driver_postgresql.dbapi
        import pyarrow as pa
    except ModuleNotFoundError as error:
        raise IngestionError(
            "Could not import ADBC PostgreSQL or PyArrow. Install the project's runtime dependencies first."
        ) from error

    uri = _postgres_uri(settings)
    connection = None
    try:
        connection = adbc_driver_postgresql.dbapi.connect(uri)
        geometry_column = _geometry_column(source)
        with _open_reader(source, pa) as reader, connection.cursor() as cursor:
            started_at = time.perf_counter()
            cursor.adbc_ingest(target_table, reader, mode="replace")
            cursor.execute(
                f"ALTER TABLE {target_table} "
                f"ALTER COLUMN {geometry_column} TYPE geometry "
                f"USING ST_GeomFromWKB({geometry_column})"
            )
            if spatial_index:
                cursor.execute(spatial_index_statement(target_table, geometry_column))
        connection.commit()
    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        raise IngestionError(f"ADBC ingestion failed: {_redact_error(str(error), settings.password)}") from error
    finally:
        if connection is not None:
            connection.close()

    return IngestionResult(statements=statements, target_table=target_table, elapsed_seconds=time.perf_counter() - started_at)


def build_direct_statements(
    dataset: str,
    table: str | None = None,
    spatial_index: bool = False,
) -> tuple[Path, str, list[str]]:
    """Build a password-free description of direct ADBC geometry ingestion."""

    source, target_table, _ = build_statements(dataset, table)
    if table is None:
        target_table = f"{target_table}_direct"
    geometry_column = _geometry_column(source)
    statements = [
        *_read_steps(source),
        f"DROP TABLE IF EXISTS {target_table};",
        f"CREATE TABLE {target_table} (... inferred Arrow columns ..., {geometry_column} geometry);",
        f"ADBC: cursor.adbc_ingest('{target_table}', reader, mode='append');",
    ]
    if spatial_index:
        statements.append(f"{spatial_index_statement(target_table, geometry_column)};")
    return source, target_table, statements


def run_direct(
    settings: PostgresSettings,
    dataset: str,
    table: str | None = None,
    spatial_index: bool = False,
    dry_run: bool = False,
) -> IngestionResult:
    """Pre-create a PostGIS geometry column, then append WKB via ADBC.

    This relies on the PostgreSQL driver's binary COPY path accepting WKB as
    PostGIS geometry input. Unlike :func:`run`, it avoids rewriting the table
    with ``ALTER TABLE ... USING ST_GeomFromWKB`` after ingestion.
    """

    source, target_table, statements = build_direct_statements(dataset, table, spatial_index)
    if dry_run:
        return IngestionResult(statements=statements, target_table=target_table.lower(), elapsed_seconds=None)

    try:
        import adbc_driver_postgresql.dbapi
        import pyarrow as pa
    except ModuleNotFoundError as error:
        raise IngestionError(
            "Could not import ADBC PostgreSQL or PyArrow. Install the project's runtime dependencies first."
        ) from error

    uri = _postgres_uri(settings)
    connection = None
    try:
        connection = adbc_driver_postgresql.dbapi.connect(uri)
        geometry_column = _geometry_column(source)
        with _open_reader(source, pa) as reader, connection.cursor() as cursor:
            create_table = _create_table_statement(
                target_table, reader.schema, pa, geometry_column
            )
            cursor.execute(f"DROP TABLE IF EXISTS {target_table}")
            cursor.execute(create_table)
            started_at = time.perf_counter()
            cursor.adbc_ingest(target_table, reader, mode="append")
            if spatial_index:
                cursor.execute(spatial_index_statement(target_table, geometry_column))
        connection.commit()
    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        raise IngestionError(f"ADBC direct ingestion failed: {_redact_error(str(error), settings.password)}") from error
    finally:
        if connection is not None:
            connection.close()

    return IngestionResult(statements=statements, target_table=target_table.lower(), elapsed_seconds=time.perf_counter() - started_at)


def _postgres_uri(settings: PostgresSettings) -> str:
    """Build the private PostgreSQL URI required by the ADBC driver."""

    host = f"[{settings.host}]" if ":" in settings.host else settings.host
    return "postgresql://{}:{}@{}:{}/{}".format(
        quote(settings.user, safe=""),
        quote(settings.password, safe=""),
        host,
        settings.port,
        quote(settings.database, safe=""),
    )


def _redact_error(message: str, password: str) -> str:
    """Avoid including the database password if a driver error echoes its URI."""

    return message.replace(password, "<redacted>").replace(quote(password, safe=""), "<redacted>")


@contextmanager
def _open_reader(source: Path, pa: object):
    """Yield a streaming Arrow reader for GeoParquet or FlatGeobuf."""

    if source.suffix.lower() == ".fgb":
        try:
            import pyogrio.raw
        except ModuleNotFoundError as error:
            raise IngestionError(
                "Could not import Pyogrio. Install the project's runtime dependencies first."
            ) from error

        with pyogrio.raw.open_arrow(source, use_pyarrow=True) as (_, reader):
            yield reader
        return

    import pyarrow.parquet as pq

    parquet_file = pq.ParquetFile(source)
    yield pa.RecordBatchReader.from_batches(
        parquet_file.schema_arrow,
        parquet_file.iter_batches(),
    )


def _read_steps(source: Path) -> list[str]:
    if source.suffix.lower() == ".fgb":
        return [f"Pyogrio: stream FlatGeobuf Arrow batches from {source};"]
    return [f"PyArrow: stream GeoParquet batches from {source};"]


def _geometry_column(source: Path) -> str:
    return "wkb_geometry" if source.suffix.lower() == ".fgb" else "geometry"


def _create_table_statement(
    table: str, schema: object, pa: object, geometry_column: str = "geometry"
) -> str:
    """Create DDL from an Arrow schema, preserving its WKB column as geometry."""

    columns = []
    for field in schema:
        name = _quote_identifier(field.name)
        if field.name == geometry_column:
            columns.append(f"{name} geometry")
        else:
            columns.append(f"{name} {_postgres_type(field.type, pa)}")
    return f"CREATE TABLE {table} ({', '.join(columns)})"


def _postgres_type(arrow_type: object, pa: object) -> str:
    """Map the scalar Arrow types used by benchmark inputs to Postgres DDL."""

    types = pa.types
    if types.is_string(arrow_type) or types.is_large_string(arrow_type):
        return "text"
    if types.is_boolean(arrow_type):
        return "boolean"
    if types.is_int8(arrow_type) or types.is_int16(arrow_type):
        return "smallint"
    if types.is_int32(arrow_type):
        return "integer"
    if types.is_int64(arrow_type):
        return "bigint"
    if types.is_float32(arrow_type):
        return "real"
    if types.is_float64(arrow_type):
        return "double precision"
    if types.is_binary(arrow_type):
        return "bytea"
    raise IngestionError(f"ADBC direct ingestion does not yet map Arrow type {arrow_type} to Postgres DDL.")


def _quote_identifier(identifier: str) -> str:
    """Quote a source column name for Postgres DDL."""

    return '"' + identifier.replace('"', '""') + '"'
