# PostGIS at the boundaries: breaking the ingress speed limit with Apache Arrow and ADBC

Among many sins committed whilst benchmarking PostGIS is neglecting the time required to ingest and egress PostGIS source tables and results, respectively. While in some cases bandwidth is the constraining factor determining ingress or egress speed, the choice of database client can have an order of magnitude effect on the time required to ingest or egress a large table to or from a PostGIS server on localhost or connected via a high bandwidth connection. In this talk we benchmark the most boring and arguably least benchmarked component of a PostGIS stack (the database client) and highlight recent improvements to the Arrow Database Connectivity (ADBC) PostgreSQL driver that help more users spend less time wrangling and more time computing.

## PostGIS

The Compose service uses PostgreSQL 18 / PostGIS 3.6, matching `../sedona-db`.
The [BAO Systems image](https://github.com/baosystems/docker-postgis) rebuilds
upstream for both AMD64 and ARM64, so Docker selects the native image on Intel
and Apple Silicon Macs. This is a third-party rebuild with no support provided.

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
