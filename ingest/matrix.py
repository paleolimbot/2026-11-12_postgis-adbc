"""Matrix execution for the ingestion implementations."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .adbc import run as run_adbc_alter
from .adbc import run_direct as run_adbc_direct
from .config import PostgresSettings
from .duckdb import run as run_duckdb
from .duckdb_copy import run as run_duckdb_copy
from .geopandas import run as run_geopandas
from .ogr2ogr import DATASETS, IngestionError
from .ogr2ogr import run as run_ogr2ogr
from .postgis import drop_ingested_table

METHODS = (
    "ogr2ogr",
    "duckdb",
    "duckdb-copy",
    "geopandas",
    "adbc-alter",
    "adbc-direct",
)


@dataclass(frozen=True)
class MatrixResult:
    """The result of one dataset and ingestion-method combination."""

    dataset: str
    method: str
    elapsed_seconds: float | None
    error: str | None
    iteration: int = 1


def split_selection(value: str, name: str, all_values: tuple[str, ...]) -> tuple[str, ...]:
    """Parse a non-empty comma-separated selection, expanding ``all``."""

    selections = tuple(item.strip() for item in value.split(",") if item.strip())
    if not selections:
        raise IngestionError(f"--{name} must contain at least one comma-separated value.")
    if "all" in selections:
        if len(selections) != 1:
            raise IngestionError(f"--{name} value 'all' cannot be combined with other selections.")
        return all_values
    return selections


def run(
    settings: PostgresSettings,
    datasets: tuple[str, ...],
    methods: tuple[str, ...],
    *,
    dry_run: bool,
    ogr2ogr_bin: str,
    psql_bin: str,
    partitions: int,
    spatial_index: bool,
    repetitions: int = 1,
    drop_table: bool = True,
    progress: Callable[[int, str, str], None] | None = None,
) -> list[MatrixResult]:
    """Run every selected dataset/method pair, retaining individual failures."""

    unknown_datasets = sorted(set(datasets).difference(DATASETS))
    if unknown_datasets:
        raise IngestionError(f"Unknown dataset(s): {', '.join(unknown_datasets)}.")
    unknown_methods = sorted(set(methods).difference(METHODS))
    if unknown_methods:
        raise IngestionError(f"Unknown method(s): {', '.join(unknown_methods)}.")
    if repetitions < 1:
        raise IngestionError("--n must be at least 1.")

    results: list[MatrixResult] = []
    for iteration in range(1, repetitions + 1):
        for dataset in datasets:
            for method in methods:
                if progress is not None:
                    progress(iteration, dataset, method)
                try:
                    result = _run_method(
                        method,
                        settings=settings,
                        dataset=dataset,
                        dry_run=dry_run,
                        ogr2ogr_bin=ogr2ogr_bin,
                        psql_bin=psql_bin,
                        partitions=partitions,
                        spatial_index=spatial_index,
                    )
                except IngestionError as error:
                    results.append(MatrixResult(dataset, method, None, str(error), iteration))
                else:
                    cleanup_error = None
                    if drop_table and not dry_run:
                        try:
                            drop_ingested_table(settings, result.target_table)
                        except IngestionError as error:
                            cleanup_error = str(error)
                    results.append(MatrixResult(dataset, method, result.elapsed_seconds, cleanup_error, iteration))
    return results


def format_results(results: list[MatrixResult], dry_run: bool, include_iteration: bool = False) -> str:
    """Render a compact table suitable for terminal output."""

    headers = ("iteration", "dataset", "method", "status", "elapsed") if include_iteration else ("dataset", "method", "status", "elapsed")
    rows = [headers]
    for result in results:
        if result.error:
            status = f"error: {result.error}"
            elapsed = "-"
        elif dry_run:
            status = "dry run"
            elapsed = "-"
        else:
            status = "ok"
            elapsed = _format_elapsed(result.elapsed_seconds)
        values = (result.dataset, result.method, status, elapsed)
        rows.append((str(result.iteration), *values) if include_iteration else values)

    widths = [max(len(row[index]) for row in rows) for index in range(len(headers))]
    return "\n".join(
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip()
        for row in rows
    )


def write_results(path: Path, results: list[MatrixResult], *, name: str, dry_run: bool) -> None:
    """Write matrix results as CSV or TSV, inferred from the output suffix."""

    delimiter = {".csv": ",", ".tsv": "\t"}.get(path.suffix.lower())
    if delimiter is None:
        raise IngestionError("--output must have a .csv or .tsv extension.")

    try:
        with path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=("name", "iteration", "dataset", "method", "status", "elapsed_seconds", "error"),
                delimiter=delimiter,
            )
            writer.writeheader()
            for result in results:
                if result.error:
                    status = "error"
                elif dry_run:
                    status = "dry run"
                else:
                    status = "ok"
                writer.writerow(
                    {
                        "name": name,
                        "iteration": result.iteration,
                        "dataset": result.dataset,
                        "method": result.method,
                        "status": status,
                        "elapsed_seconds": result.elapsed_seconds if result.elapsed_seconds is not None else "",
                        "error": result.error or "",
                    }
                )
    except OSError as error:
        raise IngestionError(f"Could not write matrix output {path}: {error}") from error


def _run_method(
    method: str,
    *,
    settings: PostgresSettings,
    dataset: str,
    dry_run: bool,
    ogr2ogr_bin: str,
    psql_bin: str,
    partitions: int,
    spatial_index: bool,
):
    if method == "ogr2ogr":
        return run_ogr2ogr(
            settings,
            dataset,
            ogr2ogr_bin=ogr2ogr_bin,
            spatial_index=spatial_index,
            dry_run=dry_run,
        )
    if method == "duckdb":
        return run_duckdb(settings, dataset, spatial_index=spatial_index, dry_run=dry_run)
    if method == "duckdb-copy":
        return run_duckdb_copy(
            settings,
            dataset,
            partitions=partitions,
            psql_bin=psql_bin,
            spatial_index=spatial_index,
            dry_run=dry_run,
        )
    if method == "geopandas":
        return run_geopandas(settings, dataset, spatial_index=spatial_index, dry_run=dry_run)
    if method == "adbc-alter":
        return run_adbc_alter(settings, dataset, spatial_index=spatial_index, dry_run=dry_run)
    assert method == "adbc-direct"
    return run_adbc_direct(settings, dataset, spatial_index=spatial_index, dry_run=dry_run)


def _format_elapsed(seconds: float | None) -> str:
    assert seconds is not None
    minutes, remaining_seconds = divmod(seconds, 60)
    if minutes >= 1:
        return f"{int(minutes)}m {remaining_seconds:05.2f}s"
    return f"{remaining_seconds:.2f}s"
