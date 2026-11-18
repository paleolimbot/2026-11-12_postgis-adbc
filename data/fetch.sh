
curl -L \
    https://github.com/geoarrow/geoarrow-data/releases/download/v0.2.0/microsoft-buildings_point.parquet \
    -o data/microsoft-buildings_point.parquet

curl -L \
  https://overturemapswestus2.blob.core.windows.net/release/2026-10-21.0/theme=divisions/type=division_area/part-00000-d97cde63-9be2-55c6-8b16-c60d627c9ab7-c000.zstd.parquet \
  -o data/overture-division-area.parquet

curl -L \
  https://github.com/geoarrow/geoarrow-data/releases/download/v0.2.0/ns-water_elevation.parquet \
  -o data/ns-water_elevation.parquet

curl -L \
  https://github.com/geoarrow/geoarrow-data/releases/download/v0.2.0/ns-water_water-poly.parquet \
  -o data/ns-water_water-poly.parquet
