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
Postgres connection values in `.env`. Every `*.parquet` fixture in `data/` is
available through its filename stem: for example,
`data/ns-water_water-poly.parquet` is selected with
`--dataset ns-water_water-poly`. Each method uses its own destination table, so
the same source can be loaded for comparison.

Download just this source (only required once), start PostGIS as above, then run:

```sh
./data/fetch.sh ns-water_elevation
uv run postgis-ingest ogr2ogr
```

Choose another fixture with `--dataset`; run `postgis-ingest <method> --help`
to see the currently discovered names:

```sh
uv run postgis-ingest adbc-direct --dataset ns-water_water-poly
```

Preview the exact `ogr2ogr` command without connecting to the database:

```sh
uv run postgis-ingest ogr2ogr --dry-run
```

The database password is supplied to GDAL through `PGPASSWORD` and is never
included in the printed command. Use `--table another_name` to choose a
different target table, or `--env-file path/to/.env` for a different connection.

All loaders default to ingest-only timing and do not create a spatial index.
Pass `--spatial-index` to include a GiST index in that method's timing; in the
matrix harness it applies to every selected cell. `ogr2ogr` and GeoPandas use
their built-in index creation paths, while the other methods run a PostGIS
`CREATE INDEX ... USING GIST` after loading.

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

### DuckDB COPY files + psql

`duckdb-copy` uses DuckDB's PostgreSQL binary COPY writer to split rows into a
configurable number of local temporary files. It then uses concurrent `psql`
client-side `\copy` commands to load them. The intermediate files are removed
after a successful or failed run. Use `--partitions` to select both the number
of write buckets and the maximum number of parallel `psql` clients:

```sh
uv run postgis-ingest duckdb-copy --partitions 4
```

The default is one partition. The route currently supports flat Parquet schemas
with a `geometry` column; it creates `ns_water_elevation_duckdb_copy` by default.
Preview the workflow without connecting:

```sh
uv run postgis-ingest duckdb-copy --partitions 4 --dry-run
```

### GeoPandas

`geopandas` is the common high-level GIS Python path. It materializes the
fixture with PyArrow, turns its WKB `geometry` column into a `GeoDataFrame`,
then calls `GeoDataFrame.to_postgis()` with SQLAlchemy, GeoAlchemy2, and
psycopg. This measures both GeoPandas conversion and the write, and replaces
`ns_water_elevation_geopandas` by default. It deliberately does not create a
spatial index, matching the other ingestion benchmarks:

```sh
uv run postgis-ingest geopandas
```

Preview the workflow without connecting:

```sh
uv run postgis-ingest geopandas --dry-run
```

### Matrix harness

Run selected dataset × method combinations sequentially with comma-separated
fixture and method lists. Each method uses its own default destination table;
the harness prints a status and elapsed-time table, continues after a failed
cell, and exits nonzero when any cell fails.

```sh
uv run postgis-ingest matrix \
  --datasets ns-water_elevation,ns-water_water-poly \
  --methods ogr2ogr,duckdb,adbc-direct
```

Available methods are `ogr2ogr`, `duckdb`, `duckdb-copy`, `geopandas`,
`adbc-alter`, and `adbc-direct`. Pass `--partitions N` to control the
`duckdb-copy` cells, or use `--dry-run` to validate a matrix without loading.
Use `all` as either selector to expand to every discovered fixture or every
available method:

```sh
uv run postgis-ingest matrix --datasets all --methods all --dry-run
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
