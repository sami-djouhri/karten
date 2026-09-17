#!/bin/sh
# entpacke-eine.sh — packt EINE Quellkachel aus. Laeuft IM GDAL-Container,
# aufgerufen ueber xargs -P, damit mehrere Kacheln gleichzeitig laufen.
#
# Warum ueberhaupt auspacken: JPEG2000 zu dekodieren kostet rund 28 s je
# Kachel und laeuft je Datei einkernig. gdalwarp liest die Quelle waehrend
# des Warpens — damit haengt der ganze Lauf an EINEM Kern, waehrend drei
# brachliegen (gemessen: 13 % in 79 min, hochgerechnet ~10 h). Aus einem
# bereits ausgepackten, gekachelten GeoTIFF warpt dieselbe Kachel in 5 s.
# Also: einmal auspacken (parallel), dann warpen (schnell).
#
# ★★ ZWEI Ausgabeformen, gesteuert ueber ZIEL_DIR/KOMPRESSION:
#     tif/      JPEG Q90  — Vorlage fuer z19. Dort wird auf 18,7 cm herunter-
#                           gerechnet, die Artefakte verschwinden im Resampling.
#     tif-fein/ DEFLATE   — Vorlage fuer z20. Verlustfrei, damit zwischen der
#                           Quelle und der fertigen Kachel nur EINE verlust-
#                           behaftete Stufe liegt statt drei. Rund 150 MB je
#                           Kachel statt 22 — deshalb nur fuer die zwei
#                           Scharf-Inseln, nicht fuer alle 910.
set -e

quelle="$1"
ZIEL_DIR="${ZIEL_DIR:-tif}"
KOMPRESSION="${KOMPRESSION:-JPEG}"
ziel="$ZIEL_DIR/$(basename "$quelle" .jp2).tif"

# Schon da? Dann ueberspringen — der Lauf ist damit wiederaufnehmbar.
[ -s "$ziel" ] && exit 0

# ★ Erst unter .tmp schreiben, dann umbenennen. Ein abgebrochener Lauf
#   hinterlaesst sonst eine halbe .tif, die beim naechsten Mal als „schon
#   fertig" durchgewunken wird. Dieselbe Lehre wie bei extract-maps.sh.
#
# ★★ `-of GTiff` ist wegen genau dieser .tmp-Endung PFLICHT: GDAL errraet den
#    Ausgabetreiber sonst aus der Endung, kennt „.tmp" nicht und bricht mit
#    „Output driver not found." ab — 400 Mal in Folge, ohne eine einzige Datei.
#
# -b 1 -b 2 -b 3: die vierte Lage ist Infrarot — in einer Luftbildansicht
# unerwuenscht und leicht als Alphakanal misszuverstehen.
if [ "$KOMPRESSION" = "DEFLATE" ]; then
    # PREDICTOR=2 (horizontale Differenz) holt bei Bilddaten rund ein Drittel
    # heraus. PHOTOMETRIC=YCBCR gibt es hier NICHT — das ist eine JPEG-eigene
    # Farbraumtrennung und mit DEFLATE ungueltig.
    set -- -co COMPRESS=DEFLATE -co PREDICTOR=2 -co ZLEVEL=6
else
    set -- -co COMPRESS=JPEG -co JPEG_QUALITY=90 -co PHOTOMETRIC=YCBCR
fi

gdal_translate -q -of GTiff -b 1 -b 2 -b 3 \
    -co TILED=YES -co BLOCKXSIZE=512 -co BLOCKYSIZE=512 \
    "$@" \
    "$quelle" "$ziel.tmp"
mv "$ziel.tmp" "$ziel"
