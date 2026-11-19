"""Command-line interface for PostGIS ingestion implementations."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from .config import ConfigurationError, load_postgres_settings
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_postgres_settings(args.env_file)
        result = run_ogr2ogr(
            settings=settings,
            dataset=args.dataset,
            table=args.table,
            ogr2ogr_bin=args.ogr2ogr_bin,
            dry_run=args.dry_run,
        )
    except (ConfigurationError, IngestionError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(" ".join(shlex.quote(argument) for argument in result.command))
    else:
        print(f"Loaded {args.dataset} with ogr2ogr in {_format_elapsed(result.elapsed_seconds)}.")
    return 0


def _format_elapsed(seconds: float | None) -> str:
    assert seconds is not None
    minutes, remaining_seconds = divmod(seconds, 60)
    if minutes >= 1:
        return f"{int(minutes)}m {remaining_seconds:05.2f}s"
    return f"{remaining_seconds:.2f}s"


if __name__ == "__main__":
    raise SystemExit(main())
