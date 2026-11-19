
#!/usr/bin/env sh
# Download one benchmark source, or every source when no name is given.
set -eu

dataset="${1:-all}"

fetch() {
  filename="$1"
  url="$2"
  curl --fail --location --retry 3 "$url" -o "data/$filename"
}

fetch_ns_water_fgb() {
  # GeoArrow distributes these release assets as raw FlatGeobuf files, rather
  # than ZIP archives.  Keep the downloaded .fgb files in data/ for ogr2ogr.
  fetch ns-water_elevation.fgb \
    https://github.com/geoarrow/geoarrow-data/releases/download/v0.2.0/ns-water_elevation.fgb
  fetch ns-water_water-poly.fgb \
    https://github.com/geoarrow/geoarrow-data/releases/download/v0.2.0/ns-water_water-poly.fgb
}

case "$dataset" in
  ns-water_elevation)
    fetch ns-water_elevation.parquet \
      https://github.com/geoarrow/geoarrow-data/releases/download/v0.2.0/ns-water_elevation.parquet
    ;;
  ns-water-fgb)
    fetch_ns_water_fgb
    ;;
  all)
    fetch microsoft-buildings_point.parquet \
      https://github.com/geoarrow/geoarrow-data/releases/download/v0.2.0/microsoft-buildings_point.parquet
    fetch overture-division-area.parquet \
      https://overturemapswestus2.blob.core.windows.net/release/2026-10-21.0/theme=divisions/type=division_area/part-00000-d97cde63-9be2-55c6-8b16-c60d627c9ab7-c000.zstd.parquet
    fetch ns-water_elevation.parquet \
      https://github.com/geoarrow/geoarrow-data/releases/download/v0.2.0/ns-water_elevation.parquet
    fetch ns-water_water-poly.parquet \
      https://github.com/geoarrow/geoarrow-data/releases/download/v0.2.0/ns-water_water-poly.parquet
    ;;
  *)
    echo "usage: $0 [ns-water_elevation|ns-water-fgb|all]" >&2
    exit 2
    ;;
esac
