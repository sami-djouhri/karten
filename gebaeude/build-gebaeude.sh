#!/usr/bin/env bash
# build-gebaeude.sh — macht aus den NRW-LoD2-Modellen Vektorkacheln mit
# Gebaeudehoehen fuer die 3D-Ansicht des Karten-Portals.
#
# Zwei Schritte:
#   1. CityGML lesen -> zeilenweises GeoJSON (Grundriss + Hoehe)
#   2. GeoJSON -> Vektorkacheln (MVT) -> MBTILES
#
# ★ Kein tippecanoe noetig: GDAL schreibt MVT selbst. Ein Werkzeug weniger,
#   das gepflegt und aktuell gehalten werden muss — und das GDAL-Image liegt
#   fuer die Bildkacheln ohnehin auf der Platte.
#
# ★★ MINZOOM=13: darunter waeren Gebaeude wenige Bildpunkte gross, die Kacheln
#    aber voll. Ganz NRW auf z8 waere ein Vielfaches der Datenmenge fuer etwas,
#    das man nicht sieht. Die 3D-Ansicht ergibt ohnehin erst nah am Boden Sinn.
set -euo pipefail

WURZEL="${WURZEL:-/opt/gebaeude}"
QUELLE="$WURZEL/quelle"
AUS="$WURZEL/aus"
GDAL="${GDAL:-ghcr.io/osgeo/gdal:ubuntu-small-latest}"
PARALLEL="${PARALLEL:-3}"
MINZOOM="${MINZOOM:-13}"
MAXZOOM="${MAXZOOM:-16}"

mkdir -p "$AUS"
[ -d "$QUELLE" ] || { echo "FEHLT: $QUELLE" >&2; exit 1; }

gdal() { docker run --rm -v "$WURZEL:/w" -w /w "$GDAL" "$@"; }

echo "== Stufe 1: CityGML lesen =="
gdal python3 /w/lies-lod2.py /w/quelle /w/gebaeude.geojsonl "$PARALLEL"

echo
echo "== Stufe 2: Vektorkacheln z$MINZOOM..z$MAXZOOM =="
rm -rf "$AUS/gebaeude.mbtiles"
# -nlt POLYGON: die Quelle ist eindeutig flaechig; ohne Ansage raet ogr2ogr aus
# dem ersten Datensatz und stolpert ueber den ersten Mehrteiler.
gdal ogr2ogr -f MVT /w/aus/gebaeude.mbtiles /w/gebaeude.geojsonl \
    -nln gebaeude -nlt POLYGON \
    -dsco MINZOOM="$MINZOOM" -dsco MAXZOOM="$MAXZOOM" \
    -dsco FORMAT=MBTILES \
    -dsco COMPRESS=YES \
    -dsco NAME=gebaeude \
    -dsco DESCRIPTION="LoD2-Gebaeudehoehen, Land NRW (dl-de/zero-2-0)"

ls -lh "$AUS/gebaeude.mbtiles"
echo "Naechster Schritt: nach pmtiles wandeln und nach node1 ins Karten-Portal legen."
