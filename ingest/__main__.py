"""Command-line interface for PostGIS ingestion implementations."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .adbc import run as run_adbc_alter
from .adbc import run_direct as run_adbc_direct
from .config import ConfigurationError, load_postgres_settings
from .duckdb import run as run_duckdb
from .ogr2ogr import DATASETS, IngestionError
from .ogr2ogr import run as run_ogr2ogr


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load benchmark datasets into PostGIS.")
    subcommands = parser.add_subparsers(dest="method", required=True)

    ogr = subcommands.add_parser("ogr2ogr", help="load a dataset with GDAL's ogr2ogr")
    ogr.add_argument("--dataset", choices=DATASETS, default="ns-water_elevation")
    ogr.add_argument("--table", help="destination table (default is method-specific)")
    ogr.add_argument("--env-file", type=Path, default=Path(".env"), help="dotenv file (default: .env)")
    ogr.add_argument("--ogr2ogr-bin", default="ogr2ogr", help="ogr2ogr executable (default: ogr2ogr)")
    ogr.add_argument("--dry-run", action="store_true", help="print the command without connecting to Postgres")

    duckdb = subcommands.add_parser("duckdb", help="load GeoParquet through DuckDB into PostGIS")
    duckdb.add_argument("--dataset", choices=DATASETS, default="ns-water_elevation")
    duckdb.add_argument("--table", help="destination table (default is method-specific)")
    duckdb.add_argument("--env-file", type=Path, default=Path(".env"), help="dotenv file (default: .env)")
    duckdb.add_argument("--dry-run", action="store_true", help="print the SQL without connecting to Postgres")

    adbc_alter = subcommands.add_parser(
        "adbc-alter",
        aliases=["adbc"],
        help="bulk-ingest WKB with ADBC, then convert bytea to geometry",
    )
    _add_adbc_arguments(adbc_alter)

    adbc_direct = subcommands.add_parser(
        "adbc-direct",
        help="pre-create a geometry column, then bulk-ingest WKB with ADBC",
    )
    _add_adbc_arguments(adbc_direct)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_postgres_settings(args.env_file)
        if args.method == "ogr2ogr":
            result = run_ogr2ogr(
                settings=settings,
                dataset=args.dataset,
                table=args.table,
                ogr2ogr_bin=args.ogr2ogr_bin,
                dry_run=args.dry_run,
            )
        elif args.method == "duckdb":
            result = run_duckdb(
                settings=settings,
                dataset=args.dataset,
                table=args.table,
                dry_run=args.dry_run,
            )
        elif args.method in {"adbc", "adbc-alter"}:
            result = run_adbc_alter(
                settings=settings,
                dataset=args.dataset,
                table=args.table,
                dry_run=args.dry_run,
            )
        else:
            result = run_adbc_direct(
                settings=settings,
                dataset=args.dataset,
                table=args.table,
                dry_run=args.dry_run,
            )
    except (ConfigurationError, IngestionError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.dry_run:
        if args.method == "ogr2ogr":
            import shlex

            print(" ".join(shlex.quote(argument) for argument in result.command))
        else:
            print("\n".join(result.statements))
    else:
        print(f"Loaded {args.dataset} with {args.method} in {_format_elapsed(result.elapsed_seconds)}.")
    return 0


def _add_adbc_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset", choices=DATASETS, default="ns-water_elevation")
    parser.add_argument("--table", help="destination table (default is method-specific)")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="dotenv file (default: .env)")
    parser.add_argument("--dry-run", action="store_true", help="print the ingestion steps without connecting to Postgres")


def _format_elapsed(seconds: float | None) -> str:
    assert seconds is not None
    minutes, remaining_seconds = divmod(seconds, 60)
    if minutes >= 1:
        return f"{int(minutes)}m {remaining_seconds:05.2f}s"
    return f"{remaining_seconds:.2f}s"


if __name__ == "__main__":
    raise SystemExit(main())
