#!/usr/bin/env bash
# hole-nrw.sh — holt einen Kachelsatz von opengeodata.nrw.de.
#
#   ./hole-nrw.sh dgm1 /opt/hoehendaten/dgm1-liste.tsv /opt/hoehendaten/quelle
#   ./hole-nrw.sh lod2 /opt/gebaeude/lod2-liste.tsv    /opt/gebaeude/quelle
#
# Quelle: Datenlizenz Deutschland Zero (dl-de/zero-2-0) — echtes Open Data mit
# ausdruecklichem Massendownload, kein Abernten fremder Kachelserver.
#
# ★ Groessenpruefung gegen den Katalogwert ist der Kern: eine halbe Datei ist
#   schlimmer als keine. Der spaetere GDAL- oder Parser-Lauf wuerde daran
#   abbrechen, und zwar erst nach Stunden. Deshalb wird jede unvollstaendige
#   Datei sofort verworfen und der Lauf ist beliebig wiederholbar.
set -euo pipefail

BASIS_ROOT="https://www.opengeodata.nrw.de/produkte/geobasis"
case "${1:-}" in
    dgm1) BASIS="$BASIS_ROOT/hm/dgm1_tiff/dgm1_tiff" ;;
    dom1) BASIS="$BASIS_ROOT/hm/dom1_tiff/dom1_tiff" ;;
    lod2) BASIS="$BASIS_ROOT/3dg/lod2_gml/lod2_gml" ;;
    *) echo "Aufruf: $0 <dgm1|dom1|lod2> <liste.tsv> <zielverzeichnis>" >&2; exit 1 ;;
esac
LISTE="${2:?Liste fehlt}"
ZIEL="${3:?Zielverzeichnis fehlt}"
PARALLEL="${PARALLEL:-3}"

mkdir -p "$ZIEL"
[ -r "$LISTE" ] || { echo "FEHLT: $LISTE" >&2; exit 1; }

hole() {
    local name="$1" soll="$2"
    local pfad="$ZIEL/$name"
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
        echo "GROESSE FALSCH: $name ($ist statt $soll) — verworfen" >&2
        rm -f "$pfad"
        return 1
    fi
}
export -f hole
export BASIS ZIEL

anzahl=$(wc -l < "$LISTE")
echo "Hole $anzahl Kacheln ($1) nach $ZIEL (${PARALLEL} parallel) …"

cut -f1,2 "$LISTE" \
  | xargs -P "$PARALLEL" -n 2 bash -c 'hole "$0" "$1"' \
  || echo "WARNUNG: mindestens eine Kachel fehlte — Skript erneut laufen lassen" >&2

da=$(find "$ZIEL" -maxdepth 1 -type f | wc -l)
echo "fertig: $da von $anzahl Kacheln, $(du -sh "$ZIEL" | cut -f1)"
[ "$da" -ge "$anzahl" ] || exit 1
