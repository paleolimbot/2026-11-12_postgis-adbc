
curl -L \
    https://github.com/geoarrow/geoarrow-data/releases/download/v0.2.0/microsoft-buildings_point.parquet \
    -o data/microsoft-buildings_point.parquet

curl -L \
    https://github.com/geoarrow/geoarrow-data/releases/download/v0.2.0/microsoft-buildings_point.fgb.zip \
    -o data/microsoft-buildings_point.fgb.zip

unzip data/microsoft-buildings_point.fgb.zip -d data/
rm data/microsoft-buildings_point.fgb.zip
