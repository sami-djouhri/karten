#!/usr/bin/env bash
# build-terrain.sh — macht aus den NRW-DGM1-Kacheln einen Terrain-Kachelsatz
# fuer MapLibre (3D-Gelaende + Schummerung).
#
# Ergebnis: gelaende.mbtiles, Terrarium-kodiert, z8 bis z16 (2,4 m je Bildpunkt).
# Die Quelle hat 1 m Raster — z16 ist also noch ehrlich aufgeloest und nicht
# hochgerechnet. Fuer die Silhouette von Haengen waere z14 genug, aber der Platz
# ist da und die Hoehenabfrage am Cursor profitiert.
#
# ★ Anders als bei den Bildkacheln entsteht die Pyramide NICHT am Ende auf den
#   fertigen Kacheln, sondern hier auf dem Float-Raster (`gdaladdo` unten).
#   Warum das zwingend ist, steht ausfuehrlich in kachle-terrain.py.
#
# ★★ `-r average` beim Warpen und bei den Uebersichtsstufen, NICHT `bilinear`:
#    beim Verkleinern eines Hoehenmodells soll jeder Quellwert eingehen. Bilinear
#    tastet nur vier Nachbarn ab und laesst bei Faktor 8 den Rest liegen —
#    einzelne Felsnasen und Bachtaeler verschwinden dann je nach Zoomstufe.
set -euo pipefail

WURZEL="${WURZEL:-/opt/hoehendaten}"
QUELLE="$WURZEL/quelle"
AUS="$WURZEL/aus"
GDAL="${GDAL:-ghcr.io/osgeo/gdal:ubuntu-small-latest}"
MINZOOM="${MINZOOM:-8}"
MAXZOOM="${MAXZOOM:-16}"

mkdir -p "$AUS"
[ -d "$QUELLE" ] || { echo "FEHLT: $QUELLE" >&2; exit 1; }

gdal() { docker run --rm -v "$WURZEL:/w" -w /w "$GDAL" "$@"; }
aufloesung() { python3 -c "print(f'{156543.033928 / 2**$1:.9f}')"; }

TR=$(aufloesung "$MAXZOOM")
anzahl=$(ls -1 "$QUELLE"/*.tif 2>/dev/null | wc -l)
echo "== Terrain aus $anzahl DGM1-Kacheln, z$MINZOOM..z$MAXZOOM ($TR m/px) =="

# --- Stufe 1: virtuelles Mosaik ----------------------------------------------
# DGM1 ist bereits unkomprimiertes GeoTIFF — kein Auspackschritt noetig, anders
# als bei den JPEG2000-Luftbildern.
ls -1 "$QUELLE"/*.tif | sed "s|$WURZEL/|/w/|" > "$WURZEL/dgm.txt"
gdal gdalbuildvrt -overwrite -input_file_list /w/dgm.txt /w/dgm.vrt

# --- Stufe 2: nach WebMercator warpen ----------------------------------------
# Float32 bleibt Float32 — hier wird noch nichts kodiert.
echo "-> warpen nach EPSG:3857"
gdal gdalwarp -overwrite -t_srs EPSG:3857 -tr "$TR" "$TR" -r average \
    -multi -wo NUM_THREADS=ALL_CPUS --config GDAL_CACHEMAX 1536 \
    -ot Float32 -dstnodata -9999 \
    -co TILED=YES -co BLOCKXSIZE=256 -co BLOCKYSIZE=256 \
    -co COMPRESS=DEFLATE -co PREDICTOR=3 -co ZLEVEL=6 \
    -co BIGTIFF=YES -co NUM_THREADS=ALL_CPUS \
    /w/dgm.vrt /w/dem_3857.tif
ls -lh "$WURZEL/dem_3857.tif"

# --- Stufe 3: Float-Pyramide -------------------------------------------------
# Deckt z15 bis z8 ab. Auf Float-Hoehen ist Mitteln korrekt; auf RGB waere es
# Unsinn (siehe kachle-terrain.py).
echo "-> Uebersichtsstufen auf den Float-Hoehen"
gdal gdaladdo -r average --config COMPRESS_OVERVIEW DEFLATE \
    --config PREDICTOR_OVERVIEW 3 /w/dem_3857.tif 2 4 8 16 32 64 128 256

# --- Stufe 4: kodieren und kacheln -------------------------------------------
echo "-> Terrarium kodieren und kacheln"
gdal python3 /w/kachle-terrain.py /w/dem_3857.tif /w/aus/gelaende.mbtiles \
    "$MINZOOM" "$MAXZOOM"

ls -lh "$AUS/gelaende.mbtiles"
echo "Naechster Schritt: nach pmtiles wandeln und nach node1 ins Karten-Portal legen."
