from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ingest.__main__ import build_parser, main
from ingest.config import PostgresSettings
from ingest.matrix import run as run_matrix
from ingest.ogr2ogr import IngestionError
from ingest.postgis import drop_ingested_table, drop_table_statement


SETTINGS = PostgresSettings("localhost", "5432", "postgres", "postgres", "secret")
RUNNERS = {
    "ogr2ogr": "run_ogr2ogr",
    "duckdb": "run_duckdb",
    "duckdb-copy": "run_duckdb_copy",
    "geopandas": "run_geopandas",
    "adbc": "run_adbc_alter",
    "adbc-alter": "run_adbc_alter",
    "adbc-direct": "run_adbc_direct",
}


@pytest.mark.parametrize("method", [*RUNNERS, "matrix"])
def test_cleanup_cli_defaults_and_opt_out(method):
    argv = [method]
    if method == "matrix":
        argv += ["--datasets", "all", "--methods", "all"]
    parser = build_parser()
    assert parser.parse_args(argv).drop_table is True
    assert parser.parse_args([*argv, "--no-drop-table"]).drop_table is False
    assert parser.parse_args([*argv, "--drop-table"]).drop_table is True


@pytest.mark.parametrize("method,runner", RUNNERS.items())
@pytest.mark.parametrize("keep", [False, True])
def test_single_cli_cleanup_uses_loaded_table_and_preserves_timing(method, runner, keep, capsys):
    events = []
    result = SimpleNamespace(target_table="custom_table", elapsed_seconds=1.25)

    def load(**kwargs):
        assert kwargs["table"] == "custom_table"
        events.append("load")
        return result

    with (
        patch("ingest.__main__.load_postgres_settings", return_value=SETTINGS),
        patch(f"ingest.__main__.{runner}", side_effect=load),
        patch("ingest.__main__.drop_ingested_table", side_effect=lambda settings, table: events.append(table)),
    ):
        assert main([method, "--table", "custom_table", *(["--no-drop-table"] if keep else [])]) == 0
    assert events == (["load"] if keep else ["load", "custom_table"])
    assert "1.25s" in capsys.readouterr().out


@pytest.mark.parametrize("method", RUNNERS)
def test_dry_run_previews_cleanup_without_connecting(method, capsys):
    with (
        patch("ingest.__main__.load_postgres_settings", return_value=SETTINGS),
        patch("psycopg.connect") as connect,
    ):
        assert main([method, "--dry-run", "--table", "custom_table"]) == 0
    connect.assert_not_called()
    assert 'Cleanup after ingestion (not timed): DROP TABLE IF EXISTS "custom_table";' in capsys.readouterr().out


def test_ingest_failure_does_not_drop_partial_table(capsys):
    with (
        patch("ingest.__main__.load_postgres_settings", return_value=SETTINGS),
        patch("ingest.__main__.run_ogr2ogr", side_effect=IngestionError("load failed")),
        patch("ingest.__main__.drop_ingested_table") as drop,
    ):
        assert main(["ogr2ogr"]) == 2
    drop.assert_not_called()
    assert "load failed" in capsys.readouterr().err


@pytest.mark.parametrize("drop_table,dry_run", [(True, False), (False, False), (True, True)])
def test_matrix_cleans_each_cell_before_next_load(drop_table, dry_run):
    events = []

    def load(method, **kwargs):
        events.append(f"load {method}")
        return SimpleNamespace(target_table=method, elapsed_seconds=1.25)

    with (
        patch("ingest.matrix._run_method", side_effect=load),
        patch("ingest.matrix.drop_ingested_table", side_effect=lambda settings, table: events.append(f"drop {table}")),
    ):
        results = run_matrix(
            SETTINGS, ("ns-water_elevation",), ("ogr2ogr", "duckdb"),
            dry_run=dry_run, ogr2ogr_bin="ogr2ogr", psql_bin="psql",
            partitions=1, spatial_index=False, repetitions=2, drop_table=drop_table,
        )
    expected = []
    for method in ("ogr2ogr", "duckdb") * 2:
        expected.append(f"load {method}")
        if drop_table and not dry_run:
            expected.append(f"drop {method}")
    assert events == expected
    assert all(result.elapsed_seconds == 1.25 and result.error is None for result in results)


def test_matrix_cleanup_failure_is_reported_and_next_cell_runs():
    result = SimpleNamespace(target_table="sample", elapsed_seconds=1.25)
    with (
        patch("ingest.matrix._run_method", return_value=result),
        patch("ingest.matrix.drop_ingested_table", side_effect=[IngestionError("cleanup failed"), None]),
    ):
        results = run_matrix(
            SETTINGS, ("ns-water_elevation",), ("ogr2ogr", "duckdb"),
            dry_run=False, ogr2ogr_bin="ogr2ogr", psql_bin="psql",
            partitions=1, spatial_index=False,
        )
    assert results[0].error == "cleanup failed"
    assert results[0].elapsed_seconds == 1.25
    assert results[1].error is None


def test_cleanup_commits_exact_table_without_cascade():
    with patch("psycopg.connect") as connect:
        drop_ingested_table(SETTINGS, "MixedCase")
    connection = connect.return_value.__enter__.return_value
    connection.execute.assert_called_once_with('DROP TABLE IF EXISTS "MixedCase";')
    connect.return_value.__exit__.assert_called_once_with(None, None, None)
    with pytest.raises(IngestionError):
        drop_table_statement('sample; DROP TABLE other')


def test_cleanup_errors_redact_password():
    with patch("psycopg.connect", side_effect=RuntimeError("password=secret")):
        with pytest.raises(IngestionError, match="Cleanup failed") as error:
            drop_ingested_table(SETTINGS, "sample")
    assert "secret" not in str(error.value)
