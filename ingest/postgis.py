"""Shared PostGIS DDL helpers."""

from __future__ import annotations

import hashlib


def spatial_index_statement(table: str, geometry_column: str = "geometry") -> str:
    """Return a GiST index statement with a PostgreSQL-safe index name."""

    base_name = f"{table}_{geometry_column}_gist"
    if len(base_name) > 63:
        suffix = hashlib.sha256(base_name.encode()).hexdigest()[:8]
        base_name = f"{base_name[:54]}_{suffix}"
    return f"CREATE INDEX {base_name} ON {table} USING GIST ({geometry_column})"
