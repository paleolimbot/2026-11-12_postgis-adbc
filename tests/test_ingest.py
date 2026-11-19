import os
from pathlib import Path
from unittest.mock import patch

from ingest.__main__ import _format_elapsed
from ingest.config import PostgresSettings, load_postgres_settings
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


def test_elapsed_time_is_human_readable() -> None:
    assert _format_elapsed(2.345) == "2.35s"
    assert _format_elapsed(62.345) == "1m 02.34s"
