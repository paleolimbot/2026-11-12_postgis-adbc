# PostGIS at the boundaries: breaking the ingress speed limit with Apache Arrow and ADBC

Among many sins committed whilst benchmarking PostGIS is neglecting the time required to ingest and egress PostGIS source tables and results, respectively. While in some cases bandwidth is the constraining factor determining ingress or egress speed, the choice of database client can have an order of magnitude effect on the time required to ingest or egress a large table to or from a PostGIS server on localhost or connected via a high bandwidth connection. In this talk we benchmark the most boring and arguably least benchmarked component of a PostGIS stack (the database client) and highlight recent improvements to the Arrow Database Connectivity (ADBC) PostgreSQL driver that help more users spend less time wrangling and more time computing.

## PostGIS

The Compose service uses PostgreSQL 18 / PostGIS 3.6, matching `../sedona-db`.
It uses the upstream AMD64 image; Docker Desktop runs it under emulation on
Apple Silicon.

Copy the environment file and set your password in `.env` (ignored by Git):

```sh
cp .env.example .env
```

Start the service and check PostGIS:

```sh
docker compose up -d --wait
docker compose exec postgis psql -U postgres -d postgres -c 'SELECT PostGIS_Full_Version();'
```

Connect to `localhost:5432`, or `<docker-host-lan-ip>:5432` from another machine,
with database/user `postgres` and the password from `.env`. The port binds to all
IPv4 interfaces by default; allow inbound TCP 5432 through the host firewall if
needed. Set `POSTGRES_BIND_ADDRESS` to a specific LAN IP to restrict the binding,
or `127.0.0.1` for localhost only. Port, user, and database overrides are shown in
`.env.example`; adjust the check command if you change the user or database.

Data persists in a named volume across `docker compose down` / `up`. Credentials
and database settings initialize an empty volume only; editing `.env` does not
change an existing database password. `docker compose down -v` deletes the data.

## Ingestion methods

Each ingestion implementation lives in the Python `ingest` package and uses the
Postgres connection values in `.env`. The first baseline loads only
`ns-water_elevation` with GDAL's `ogr2ogr`; it uses the PostgreSQL driver's
`COPY` path and replaces the method-specific table
`ns_water_elevation_ogr2ogr` on every run. This leaves room for later methods to
load the same source into their own comparison tables.

Download just this source (only required once), start PostGIS as above, then run:

```sh
./data/fetch.sh ns-water_elevation
uv run postgis-ingest ogr2ogr
```

Preview the exact `ogr2ogr` command without connecting to the database:

```sh
uv run postgis-ingest ogr2ogr --dry-run
```

The database password is supplied to GDAL through `PGPASSWORD` and is never
included in the printed command. Use `--table another_name` to choose a
different target table, or `--env-file path/to/.env` for a different connection.

### DuckDB

The `duckdb` option reads the GeoParquet source with DuckDB's `spatial`
extension and writes the resulting geometry column to the attached PostGIS
database through DuckDB's `postgres` extension. It replaces only
`ns_water_elevation_duckdb` by default (or the table supplied with
`--table`):

```sh
uv run postgis-ingest duckdb
```

On first use, DuckDB downloads its `postgres` and `spatial` extensions. Preview
the password-free DuckDB SQL without opening a database connection:

```sh
uv run postgis-ingest duckdb --dry-run
```

### ADBC PostgreSQL

Both ADBC routes stream the GeoParquet file as Arrow record batches through the
ADBC PostgreSQL driver's bulk-ingest API and replace only their own
method-specific destination table.

`adbc-alter` (also available as the `adbc` alias) lets ADBC create the table.
This maps the source WKB geometry to PostgreSQL `bytea`, then rewrites the table
to convert that column with `ST_GeomFromWKB`; its default table is
`ns_water_elevation_adbc`:

```sh
uv run postgis-ingest adbc-alter
```

`adbc-direct` instead pre-creates the PostGIS `geometry` column and appends the
WKB directly through ADBC's binary COPY path. This avoids the conversion pass
and uses `ns_water_elevation_adbc_direct` by default:

```sh
uv run postgis-ingest adbc-direct
```

Preview either route without connecting:

```sh
uv run postgis-ingest adbc-direct --dry-run
```

Run the tests with:

```sh
uv run pytest
```

## Slides

The slide source is [`2026-11-12_postgis-adbc.qmd`](2026-11-12_postgis-adbc.qmd), a Quarto Reveal.js presentation adapted from `../2026-10-07_teaching-sedonadb`. It contains a title slide, the original about-me slides, and the spinning-globe closing slide. The closing links are retained from the previous talk as placeholders.

Preview with [Quarto](https://quarto.org/):

```sh
quarto preview 2026-11-12_postgis-adbc.qmd
```

Render a self-contained HTML deck:

```sh
quarto render 2026-11-12_postgis-adbc.qmd
```
