import os
from pathlib import Path
from unittest.mock import patch

from ingest.__main__ import _format_elapsed
from ingest.adbc import build_direct_statements, build_statements as build_adbc_statements
from ingest.config import PostgresSettings, load_postgres_settings
from ingest.duckdb import build_statements
from ingest.ogr2ogr import build_command


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
    assert "secret" not in " ".join(command)


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


def test_elapsed_time_is_human_readable() -> None:
    assert _format_elapsed(2.345) == "2.35s"
    assert _format_elapsed(62.345) == "1m 02.34s"
