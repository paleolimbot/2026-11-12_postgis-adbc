# PostGIS at the boundaries: breaking the ingress speed limit with Apache Arrow and ADBC

Among many sins committed whilst benchmarking PostGIS is neglecting the time required to ingest and egress PostGIS source tables and results, respectively. While in some cases bandwidth is the constraining factor determining ingress or egress speed, the choice of database client can have an order of magnitude effect on the time required to ingest or egress a large table to or from a PostGIS server on localhost or connected via a high bandwidth connection. In this talk we benchmark the most boring and arguably least benchmarked component of a PostGIS stack (the database client) and highlight recent improvements to the Arrow Database Connectivity (ADBC) PostgreSQL driver that help more users spend less time wrangling and more time computing.

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
