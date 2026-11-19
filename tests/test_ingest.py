import os
from pathlib import Path
from unittest.mock import patch

import pytest

from ingest.__main__ import _format_elapsed, build_parser
from ingest.adbc import build_direct_statements
from ingest.adbc import build_statements as build_adbc_statements
from ingest.config import PostgresSettings, load_postgres_settings
from ingest.duckdb import build_statements
from ingest.duckdb_copy import build_statements as build_duckdb_copy_statements
from ingest.geopandas import build_statements as build_geopandas_statements
from ingest.matrix import METHODS, MatrixResult, format_results, split_selection, write_results
from ingest.matrix import run as run_matrix
from ingest.ogr2ogr import IngestionError, build_command, discover_datasets
from ingest.postgis import spatial_index_statement
from ingest.server_settings import ServerSetting, format_settings


def test_environment_overrides_dotenv(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_USER=file-user\nPOSTGRES_DB=sample\nPOSTGRES_PASSWORD=file-password\n",
        encoding="utf-8",
    )
    for key in tuple(os.environ):
        if key.startswith("POSTGRES_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("POSTGRES_USER", "shell-user")

    settings = load_postgres_settings(env_file)

    assert settings.user == "shell-user"
    assert settings.host == "127.0.0.1"


def test_dataset_discovery_uses_parquet_stems_and_non_parquet_filenames(tmp_path: Path) -> None:
    (tmp_path / "water-poly.parquet").touch()
    (tmp_path / "water-poly.fgb").touch()
    (tmp_path / "2026 buildings.parquet").touch()
    (tmp_path / "notes.txt").touch()

    datasets = discover_datasets(tmp_path)

    assert set(datasets) == {"water-poly", "water-poly.fgb", "2026 buildings"}
    assert datasets["water-poly"]["layer"] == "water-poly"
    assert datasets["water-poly"]["table"] == "water_poly_ogr2ogr"
    assert datasets["water-poly.fgb"]["layer"] == "water-poly"
    assert datasets["water-poly.fgb"]["table"] == "water_poly_fgb_ogr2ogr"
    assert datasets["2026 buildings"]["table"] == "data_2026_buildings_ogr2ogr"


def test_ogr_command_uses_copy_and_method_table(tmp_path: Path) -> None:
    source = tmp_path / "source.parquet"
    source.touch()
    datasets = {"sample": {"source": source, "layer": "source", "table": "sample_ogr2ogr"}}
    with patch("ingest.ogr2ogr.DATASETS", datasets):
        command = build_command(
            PostgresSettings("localhost", "5432", "postgres", "postgres", "secret"),
            "sample",
        )

    assert "PG_USE_COPY" in command
    assert "sample_ogr2ogr" in command
    assert "SPATIAL_INDEX=NONE" in command
    assert "secret" not in " ".join(command)


def test_ogr_command_can_request_its_builtin_spatial_index(tmp_path: Path) -> None:
    source = tmp_path / "source.parquet"
    source.touch()
    datasets = {"sample": {"source": source, "layer": "source", "table": "sample_ogr2ogr"}}
    with patch("ingest.ogr2ogr.DATASETS", datasets):
        command = build_command(
            PostgresSettings("localhost", "5432", "postgres", "postgres", "secret"),
            "sample",
            spatial_index=True,
        )

    assert "SPATIAL_INDEX=GIST" in command


def test_ogr_command_lets_gdal_select_the_flatgeobuf_layer(tmp_path: Path) -> None:
    source = tmp_path / "source.fgb"
    source.touch()
    datasets = {"sample.fgb": {"source": source, "layer": "source", "table": "sample_fgb_ogr2ogr"}}
    with patch("ingest.ogr2ogr.DATASETS", datasets):
        command = build_command(
            PostgresSettings("localhost", "5432", "postgres", "postgres", "secret"),
            "sample.fgb",
        )

    assert str(source) == command[-1]
    assert "source" not in command


def test_duckdb_statements_use_postgres_and_spatial_extensions(tmp_path: Path) -> None:
    source = tmp_path / "source.parquet"
    source.touch()
    datasets = {"sample": {"source": source, "layer": "source", "table": "sample_ogr2ogr"}}
    with patch("ingest.duckdb.DATASETS", datasets):
        _, target_table, statements = build_statements("sample")

    output = "\n".join(statements)
    assert target_table == "sample_duckdb"
    assert "INSTALL postgres" in output
    assert "INSTALL spatial" in output
    assert "CREATE TABLE postgis.sample_duckdb" in output
    assert "read_parquet" in output


def test_duckdb_dry_run_does_not_expose_password(tmp_path: Path) -> None:
    source = tmp_path / "source.parquet"
    source.touch()
    datasets = {"sample": {"source": source, "layer": "source", "table": "sample_ogr2ogr"}}
    with patch("ingest.duckdb.DATASETS", datasets):
        _, _, statements = build_statements("sample")

    assert "<redacted>" in "\n".join(statements)


def test_adbc_alter_statements_ingest_wkb_then_convert_geometry(tmp_path: Path) -> None:
    source = tmp_path / "source.parquet"
    source.touch()
    datasets = {"sample": {"source": source, "layer": "source", "table": "sample_ogr2ogr"}}
    with patch("ingest.adbc.DATASETS", datasets):
        _, target_table, statements = build_adbc_statements("sample")

    output = "\n".join(statements)
    assert target_table == "sample_adbc"
    assert "adbc_ingest('sample_adbc', reader, mode='replace')" in output
    assert "ALTER TABLE sample_adbc ALTER COLUMN geometry TYPE geometry" in output
    assert "ST_GeomFromWKB(geometry)" in output


def test_adbc_direct_statements_precreate_geometry_then_append_wkb(tmp_path: Path) -> None:
    source = tmp_path / "source.parquet"
    source.touch()
    datasets = {"sample": {"source": source, "layer": "source", "table": "sample_ogr2ogr"}}
    with patch("ingest.adbc.DATASETS", datasets):
        _, target_table, statements = build_direct_statements("sample")

    output = "\n".join(statements)
    assert target_table == "sample_adbc_direct"
    assert "CREATE TABLE sample_adbc_direct" in output
    assert "geometry geometry" in output
    assert "adbc_ingest('sample_adbc_direct', reader, mode='append')" in output
    assert "ALTER TABLE" not in output


def test_duckdb_copy_statements_partition_and_load_with_psql(tmp_path: Path) -> None:
    source = tmp_path / "source.parquet"
    source.touch()
    datasets = {"sample": {"source": source, "layer": "source", "table": "sample_ogr2ogr"}}
    with patch("ingest.duckdb_copy.DATASETS", datasets):
        _, target_table, statements = build_duckdb_copy_statements("sample", partitions=4)

    output = "\n".join(statements)
    assert target_table == "sample_duckdb_copy"
    assert "row_number() OVER () % 4" in output
    assert "FORMAT postgres_binary" in output
    assert "4 concurrent clients" in output
    assert "\\copy sample_duckdb_copy" in output


def test_geopandas_statements_read_wkb_then_replace_table(tmp_path: Path) -> None:
    source = tmp_path / "source.parquet"
    source.touch()
    datasets = {"sample": {"source": source, "layer": "source", "table": "sample_ogr2ogr"}}
    with patch("ingest.geopandas.DATASETS", datasets):
        _, target_table, statements = build_geopandas_statements("sample")

    output = "\n".join(statements)
    assert target_table == "sample_geopandas"
    assert "pyarrow.parquet.read_table" in output
    assert "GeoSeries.from_wkb" in output
    assert "to_postgis('sample_geopandas'" in output
    assert "if_exists='replace'" in output


def test_flatgeobuf_statements_preserve_reader_geometry_column_names(tmp_path: Path) -> None:
    source = tmp_path / "sample.fgb"
    source.touch()
    datasets = {
        "sample.fgb": {
            "source": source,
            "layer": "sample",
            "table": "sample_fgb_ogr2ogr",
        }
    }

    with (
        patch("ingest.adbc.DATASETS", datasets),
        patch("ingest.duckdb.DATASETS", datasets),
        patch("ingest.duckdb_copy.DATASETS", datasets),
        patch("ingest.geopandas.DATASETS", datasets),
    ):
        _, _, adbc_statements = build_adbc_statements("sample.fgb", spatial_index=True)
        _, _, adbc_direct_statements = build_direct_statements("sample.fgb", spatial_index=True)
        _, _, duckdb_statements = build_statements("sample.fgb", spatial_index=True)
        _, _, copy_statements = build_duckdb_copy_statements(
            "sample.fgb", spatial_index=True
        )
        _, _, geopandas_statements = build_geopandas_statements("sample.fgb")

    assert "Pyogrio: stream FlatGeobuf" in "\n".join(adbc_statements)
    assert "wkb_geometry" in "\n".join(adbc_statements)
    assert "wkb_geometry geometry" in "\n".join(adbc_direct_statements)
    assert "ST_Read(" in "\n".join(duckdb_statements)
    assert "(geom)" in "\n".join(duckdb_statements)
    assert "ST_Read(" in "\n".join(copy_statements)
    assert "(geom)" in "\n".join(copy_statements)
    assert "geopandas.read_file" in "\n".join(geopandas_statements)


def test_matrix_parser_accepts_comma_separated_selections() -> None:
    args = build_parser().parse_args(
        [
            "matrix",
            "--datasets",
            "ns-water_elevation,ns-water_water-poly",
            "--methods",
            "ogr2ogr,duckdb",
        ]
    )

    assert args.datasets == "ns-water_elevation,ns-water_water-poly"
    assert args.methods == "ogr2ogr,duckdb"


def test_matrix_parser_accepts_repetitions_and_output_options(tmp_path: Path) -> None:
    output = tmp_path / "results.tsv"
    args = build_parser().parse_args(
        [
            "matrix",
            "--datasets",
            "ns-water_elevation",
            "--methods",
            "duckdb",
            "--n",
            "3",
            "--name",
            "remote-tuned",
            "--output",
            str(output),
        ]
    )

    assert args.n == 3
    assert args.name == "remote-tuned"
    assert args.output == output


def test_matrix_output_writes_tsv_with_name_and_iteration(tmp_path: Path) -> None:
    output = tmp_path / "results.tsv"

    write_results(
        output,
        [MatrixResult("fixture", "duckdb", 1.25, None, iteration=2)],
        name="remote-tuned",
        dry_run=False,
    )

    assert output.read_text(encoding="utf-8") == (
        "name\titeration\tdataset\tmethod\tstatus\telapsed_seconds\terror\n"
        "remote-tuned\t2\tfixture\tduckdb\tok\t1.25\t\n"
    )


def test_matrix_output_requires_csv_or_tsv_suffix(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match=".csv or .tsv"):
        write_results(tmp_path / "results.txt", [], name="run", dry_run=False)


def test_server_settings_commands_parse_values() -> None:
    parser = build_parser()

    shown = parser.parse_args(["settings"])
    updated = parser.parse_args(
        [
            "set-settings",
            "--maintenance-work-mem",
            "2GB",
            "--checkpoint-timeout",
            "30min",
        ]
    )

    assert shown.method == "settings"
    assert updated.maintenance_work_mem == "2GB"
    assert updated.max_wal_size is None
    assert updated.checkpoint_timeout == "30min"


def test_server_settings_formatter_includes_value_source_and_restart_state() -> None:
    output = format_settings(
        (
            ServerSetting("maintenance_work_mem", "2097152", "kB", "configuration file", False),
            ServerSetting("max_wal_size", "16", "GB", "configuration file", True),
        )
    )

    assert "maintenance_work_mem" in output
    assert "2097152kB" in output
    assert "configuration file" in output
    assert "yes" in output


def test_matrix_all_shortcut_expands_and_cannot_be_mixed() -> None:
    assert split_selection("all", "methods", METHODS) == METHODS

    with pytest.raises(IngestionError, match="cannot be combined"):
        split_selection("all,duckdb", "methods", METHODS)


def test_matrix_dry_run_reports_every_pair() -> None:
    settings = PostgresSettings("localhost", "5432", "postgres", "postgres", "secret")

    results = run_matrix(
        settings,
        ("ns-water_elevation",),
        ("ogr2ogr", "duckdb"),
        dry_run=True,
        ogr2ogr_bin="ogr2ogr",
        psql_bin="psql",
        partitions=1,
        spatial_index=False,
    )

    assert len(results) == 2
    assert all(result.error is None for result in results)
    assert "dry run" in format_results(results, dry_run=True)


def test_matrix_repeats_the_complete_selection() -> None:
    settings = PostgresSettings("localhost", "5432", "postgres", "postgres", "secret")
    started: list[tuple[int, str, str]] = []

    results = run_matrix(
        settings,
        ("ns-water_elevation",),
        ("ogr2ogr",),
        dry_run=True,
        ogr2ogr_bin="ogr2ogr",
        psql_bin="psql",
        partitions=1,
        spatial_index=False,
        repetitions=3,
        progress=lambda iteration, dataset, method: started.append((iteration, dataset, method)),
    )

    assert [result.iteration for result in results] == [1, 2, 3]
    assert started == [
        (1, "ns-water_elevation", "ogr2ogr"),
        (2, "ns-water_elevation", "ogr2ogr"),
        (3, "ns-water_elevation", "ogr2ogr"),
    ]
    assert "iteration" in format_results(results, dry_run=True, include_iteration=True)


def test_spatial_index_statement_shortens_long_index_names() -> None:
    statement = spatial_index_statement("table_" * 20)

    index_name = statement.split()[2]
    assert len(index_name) <= 63
    assert statement.endswith("USING GIST (geometry)")


def test_elapsed_time_is_human_readable() -> None:
    assert _format_elapsed(2.345) == "2.35s"
    assert _format_elapsed(62.345) == "1m 02.34s"
