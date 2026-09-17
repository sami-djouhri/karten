#!/usr/bin/env bash
# fetch-dop.sh — holt die NRW-Orthophotos fuer den Raum Heiligenhaus.
#
# Quelle: opengeodata.nrw.de, Datenlizenz Deutschland Zero (dl-de/zero-2-0).
# Echtes Open Data mit ausdruecklichem Massendownload — kein Abernten fremder
# Kachelserver. Kacheln sind 1x1 km in UTM32 (EPSG:25832), 10 cm Bodenaufloesung,
# JPEG2000 mit 10-facher Kompression.
#
# Die Auswahl steht in dop-liste.tsv (erzeugt von dop-auswahl.py):
#   <bereich>\t<dateiname>\t<sollgroesse>
# `bereich` ist innen (8x8 km) oder aussen (20x20 km) — daraus werden spaeter
# zwei Kachelsaetze mit verschiedener Schaerfe.
set -euo pipefail

BASIS="https://www.opengeodata.nrw.de/produkte/geobasis/lusat/akt/dop/dop_jp2_f10"
ZIEL="${ZIEL:-/opt/luftbilder/quelle}"
LISTE="${LISTE:-/opt/luftbilder/dop-liste.tsv}"
PARALLEL="${PARALLEL:-3}"

mkdir -p "$ZIEL"
[ -r "$LISTE" ] || { echo "FEHLT: $LISTE" >&2; exit 1; }

hole() {
    local name="$1" soll="$2"
    local pfad="$ZIEL/$name"
    # Schon vollstaendig da? Dann nichts tun — das Skript ist wiederholbar.
    if [ -f "$pfad" ] && [ "$(stat -c%s "$pfad")" = "$soll" ]; then
        return 0
    fi
    curl -sS -f --retry 3 --retry-delay 2 -C - -o "$pfad" "$BASIS/$name" || {
        echo "FEHLER beim Laden: $name" >&2
        return 1
    }
    local ist
    ist=$(stat -c%s "$pfad")
    if [ "$ist" != "$soll" ]; then
        # Halbe Datei ist schlimmer als keine: der spaetere GDAL-Lauf wuerde
        # daran abbrechen, und zwar erst nach Stunden.
        echo "GROESSE FALSCH: $name ($ist statt $soll) — verworfen" >&2
        rm -f "$pfad"
        return 1
    fi
}
export -f hole
export BASIS ZIEL

anzahl=$(wc -l < "$LISTE")
echo "Hole $anzahl Kacheln nach $ZIEL (${PARALLEL} parallel) …"

cut -f2,3 "$LISTE" \
  | xargs -P "$PARALLEL" -n 2 bash -c 'hole "$0" "$1"' \
  || echo "WARNUNG: mindestens eine Kachel fehlte — Skript erneut laufen lassen" >&2

da=$(ls -1 "$ZIEL"/*.jp2 2>/dev/null | wc -l)
echo "fertig: $da von $anzahl Kacheln, $(du -sh "$ZIEL" | cut -f1)"
[ "$da" = "$anzahl" ] || exit 1
