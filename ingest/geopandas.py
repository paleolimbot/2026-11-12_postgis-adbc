"""The GeoPandas ``GeoDataFrame.to_postgis`` ingestion implementation."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .config import PostgresSettings
from .ogr2ogr import DATASETS, IngestionError, _validate_identifier


@dataclass(frozen=True)
class IngestionResult:
    """The GeoPandas steps that ran and their elapsed wall-clock time."""

    statements: list[str]
    elapsed_seconds: float | None


def build_statements(
    dataset: str,
    table: str | None = None,
) -> tuple[Path, str, list[str]]:
    """Build a password-free description of a GeoPandas load."""

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

    target_table = table or f"{definition['table'].removesuffix('_ogr2ogr')}_geopandas"
    _validate_identifier(target_table)
    statements = [
        f"PyArrow: pyarrow.parquet.read_table({source});",
        "GeoPandas: GeoSeries.from_wkb(table['geometry']);",
        (
            "GeoPandas: GeoDataFrame.to_postgis("
            f"'{target_table}', engine, if_exists='replace', index=False);"
        ),
    ]
    return source, target_table, statements


def run(
    settings: PostgresSettings,
    dataset: str,
    table: str | None = None,
    dry_run: bool = False,
) -> IngestionResult:
    """Read WKB Parquet into a GeoDataFrame and replace its PostGIS table."""

    source, target_table, statements = build_statements(dataset, table)
    if dry_run:
        return IngestionResult(statements=statements, elapsed_seconds=None)

    try:
        import geoalchemy2
        import geopandas
        import pyarrow.parquet
        import sqlalchemy
    except ModuleNotFoundError as error:
        raise IngestionError(
            "Could not import GeoPandas, GeoAlchemy2, or SQLAlchemy. Install the project's runtime dependencies first."
        ) from error

    engine = sqlalchemy.create_engine(_postgres_url(settings))
    try:
        started_at = time.perf_counter()
        arrow_table = pyarrow.parquet.read_table(source)
        frame = arrow_table.to_pandas()
        frame["geometry"] = geopandas.GeoSeries.from_wkb(frame["geometry"])
        frame = geopandas.GeoDataFrame(frame, geometry="geometry")
        frame.to_postgis(
            target_table,
            engine,
            if_exists="replace",
            index=False,
            dtype={"geometry": geoalchemy2.Geometry(spatial_index=False)},
        )
    except Exception as error:
        raise IngestionError(
            f"GeoPandas ingestion failed: {_redact_error(str(error), settings.password)}"
        ) from error
    finally:
        engine.dispose()

    return IngestionResult(statements=statements, elapsed_seconds=time.perf_counter() - started_at)


def _postgres_url(settings: PostgresSettings) -> str:
    """Build the private SQLAlchemy URL used by the psycopg PostgreSQL dialect."""

    host = f"[{settings.host}]" if ":" in settings.host else settings.host
    return "postgresql+psycopg://{}:{}@{}:{}/{}".format(
        quote(settings.user, safe=""),
        quote(settings.password, safe=""),
        host,
        settings.port,
        quote(settings.database, safe=""),
    )


def _redact_error(message: str, password: str) -> str:
    return message.replace(password, "<redacted>").replace(quote(password, safe=""), "<redacted>")
