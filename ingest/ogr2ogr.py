"""The GDAL/ogr2ogr implementation of the benchmark load."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .config import PostgresSettings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


DATASETS = {
    "ns-water_elevation": {
        "source": PROJECT_ROOT / "data/ns-water_elevation.parquet",
        "layer": "ns-water_elevation",
        "table": "ns_water_elevation_ogr2ogr",
    },
}


class IngestionError(RuntimeError):
    """Raised when an ingestion implementation cannot be started."""


@dataclass(frozen=True)
class IngestionResult:
    """The command that ran and its elapsed wall-clock time."""

    command: list[str]
    elapsed_seconds: float | None


def build_command(
    settings: PostgresSettings,
    dataset: str,
    table: str | None = None,
    ogr2ogr_bin: str = "ogr2ogr",
) -> list[str]:
    """Build the deterministic ogr2ogr invocation for one named dataset."""

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

    # This table name is method-specific. Other implementations can load the
    # same source independently, without replacing this baseline.
    target_table = table or definition["table"]
    _validate_identifier(target_table)

    return [
        ogr2ogr_bin,
        "--config",
        "PG_USE_COPY",
        "YES",
        "-f",
        "PostgreSQL",
        "-overwrite",
        "-nln",
        target_table,
        "-lco",
        "GEOMETRY_NAME=geom",
        f"PG:{settings.ogr_connection_string()}",
        str(source),
        definition["layer"],
    ]


def run(
    settings: PostgresSettings,
    dataset: str,
    table: str | None = None,
    ogr2ogr_bin: str = "ogr2ogr",
    dry_run: bool = False,
) -> IngestionResult:
    """Run ogr2ogr and return its command and elapsed wall-clock time.

    ``PG_USE_COPY=YES`` tells the PostgreSQL GDAL driver to use COPY for the
    bulk load. ``-overwrite`` makes repeat executions reproducible by replacing
    only this method's destination table.
    """

    command = build_command(settings, dataset, table, ogr2ogr_bin)
    if dry_run:
        return IngestionResult(command=command, elapsed_seconds=None)
    if shutil.which(ogr2ogr_bin) is None:
        raise IngestionError(
            f"Could not find {ogr2ogr_bin!r}. Install GDAL so the ogr2ogr command is available."
        )

    environment = os.environ.copy()
    environment["PGPASSWORD"] = settings.password
    try:
        started_at = time.perf_counter()
        subprocess.run(command, check=True, env=environment)
    except subprocess.CalledProcessError as error:
        raise IngestionError(f"ogr2ogr failed with exit status {error.returncode}.") from error
    return IngestionResult(command=command, elapsed_seconds=time.perf_counter() - started_at)


def _validate_identifier(identifier: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise IngestionError(
            "Table names may contain only letters, numbers, and underscores, and may not start with a number."
        )
