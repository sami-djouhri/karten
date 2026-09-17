#!/usr/bin/env bash
# build-dop-tiles.sh — macht aus den NRW-Orthophotos drei Rasterkachel-Archive
# fuer das Karten-Portal.
#
# Warum DREI Archive statt einem:
#   Die ganze 26x35-km-Flaeche in voller Quellschaerfe waere jenseits von 70 GB.
#   Die Flaeche grob und zwei Stadtkerne scharf sind zusammen rund 12 GB — bei
#   praktisch gleichem Nutzen, weil man die volle Schaerfe nur dort braucht, wo
#   man wirklich hinschaut. MapLibre legt die Ebenen uebereinander; die
#   schaerfere gewinnt, wo sie vorhanden ist.
#
#   umland    26x35 km, bis z19  = 18,7 cm je Bildpunkt
#   stadt      8x8  km, bis z20  =  9,3 cm je Bildpunkt  (Heiligenhaus)
#   solingen   8x8  km, bis z20  =  9,3 cm je Bildpunkt  (Solingen-Mitte)
#
# ★ Die Zoomstufe wird NICHT als Option gesetzt, sondern ueber die Zielaufloesung
#   erzwungen. Der MBTILES-Schreiber leitet die hoechste Stufe aus der Aufloesung
#   des Eingangs ab — gaebe man ihm die 10-cm-Kacheln unveraendert, baute er fuer
#   die ganze Flaeche z20 und das Ergebnis waere dreimal so gross wie gewollt.
#   In WebMercator ist die Aufloesung breitenunabhaengig: 156543,033928 / 2^z.
#
# ★★ ZWEISTUFIG, und die erste Stufe ist der ganze Unterschied:
#   JPEG2000 zu dekodieren kostet ~28 s je Kachel und laeuft je Datei EINKERNIG.
#   Laesst man gdalwarp direkt aus den .jp2 lesen, haengt der komplette Lauf
#   an einem Kern — gemessen 13 % in 79 Minuten, hochgerechnet rund 10 Stunden
#   fuer `umland` allein, bei drei brachliegenden Kernen.
#   Deshalb: (1) jede Kachel EINMAL parallel nach gekacheltem GeoTIFF auspacken,
#   (2) daraus warpen UND kacheln in einem Durchgang — dieselbe Kachel warpt dann
#   in 5 s statt 30.
#   _(Bis 2026-08-29 war Schritt 2 nochmals geteilt, ueber ein gekacheltes
#   Zwischen-GeoTIFF. Das kostete bei z20 rund 46 GB Platz und war nicht einmal
#   schneller — gemessen 110 s gegen 128 s an einem 2x2-km-Block, wobei die
#   128 s zusaetzlich den Alphakanal und 34 % kleinere Kacheln liefern.)_
#
#   Nicht funktioniert hat der naheliegende Umweg `gdalwarp -ovr AUTO-1`: die .jp2
#   tragen zwar interne Aufloesungsstufen (5000x5000, 2500x2500, …), gdalwarp holt
#   sie ueber die VRT aber nicht heran — mit und ohne Schalter exakt 30 s. Nur
#   gdal_translate greift darauf zu. Gegenprobe, falls das je jemand nachrechnet.
#
# ★★★ ZWEI AUSPACK-DURCHGAENGE, und das ist der Grund fuer die Bildqualitaet:
#   Die Quelle ist JPEG2000 mit 10-facher Kompression — daran ist nichts zu
#   aendern. Alles danach ist hausgemacht, und frueher wurde noch ZWEIMAL JPEG
#   komprimiert (Auspacken Q90, Kacheln Q82). Drei Generationen uebereinander
#   sieht man an Dachkanten, Zaeunen und Fahrzeugen. Jetzt:
#     tif/       JPEG Q90  fuer die Flaeche — eine verlustbehaftete Stufe nach
#                          der Quelle, danach direkt die WEBP-Kachel.
#     tif-fein/  DEFLATE   fuer die beiden Scharf-Inseln — verlustfrei, damit
#                          zwischen Quelle und fertiger Kachel nur EINE
#                          verlustbehaftete Stufe liegt. Kostet rund 150 MB je
#                          Kachel statt 22 — deshalb nur fuer die 128 Kacheln
#                          der Inseln, nicht fuer 1330.
#   ⚠ tif-fein/ ist derzeit LEER. stadt und solingen lassen sich also nicht neu
#     bauen, ohne ihre Quellkacheln vorher wieder verlustfrei auszupacken
#     (Durchgang 2 unten erledigt das, braucht aber rund 19 GB und Zeit).
set -euo pipefail

WURZEL="${WURZEL:-/opt/luftbilder}"
QUELLE="$WURZEL/quelle"
TIF="$WURZEL/tif"
TIF_FEIN="$WURZEL/tif-fein"
AUS="$WURZEL/aus"
LISTE="$WURZEL/dop-liste.tsv"
GDAL="${GDAL:-ghcr.io/osgeo/gdal:ubuntu-small-latest}"
# Drei von vier Kernen. Der vierte bleibt fuer Valhalla frei — der Routing-Dienst
# liegt im selben LXC und soll waehrend des stundenlangen Baus bedienbar bleiben.
PARALLEL="${PARALLEL:-3}"

mkdir -p "$AUS" "$TIF" "$TIF_FEIN"
[ -d "$QUELLE" ] || { echo "FEHLT: $QUELLE" >&2; exit 1; }

gdal() { docker run --rm -v "$WURZEL:/w" -w /w "$GDAL" "$@"; }
aufloesung() { python3 -c "print(f'{156543.033928 / 2**$1:.9f}')"; }

# Spalte 1 der Liste ist die Markierung (aussen/innen/solingen), Spalte 2 der
# Dateiname. `alle` nimmt jede Zeile — das ist die z19-Gesamtflaeche.
liste_fuer() {
    awk -F'\t' -v f="$1" -v praefix="$2" -v endung="$3" \
        '(f=="alle" || $1==f){ sub(/\.jp2$/, endung, $2); print praefix $2 }' "$LISTE"
}

# --- Stufe 1: Quellkacheln auspacken (parallel, wiederaufnehmbar) -------------
auspacken() {
    local ziel_dir="$1" komp="$2" filter="$3"
    local kurz; kurz=$(basename "$ziel_dir")
    local liste_datei="$WURZEL/.auspack-$kurz.txt"
    local gesamt offen

    liste_fuer "$filter" "quelle/" ".jp2" > "$liste_datei"
    gesamt=$(wc -l < "$liste_datei")
    offen=0
    while read -r q; do
        [ -s "$ziel_dir/$(basename "$q" .jp2).tif" ] || offen=$((offen + 1))
    done < "$liste_datei"

    echo "== auspacken nach $kurz/ ($komp): $offen von $gesamt offen (${PARALLEL}-fach parallel) =="
    [ "$offen" -eq 0 ] && { echo "   nichts zu tun"; return 0; }

    # EIN Container fuer alle Kacheln statt hunderter Containerstarts.
    docker run --rm -v "$WURZEL:/w" -w /w \
        -e P="$PARALLEL" -e ZIEL_DIR="$kurz" -e KOMPRESSION="$komp" "$GDAL" \
        bash -c 'xargs -a "$0" -P "$P" -n1 sh entpacke-eine.sh' ".auspack-$kurz.txt"

    echo "   fertig: $(ls "$ziel_dir"/*.tif | wc -l) GeoTIFF, $(du -sh "$ziel_dir" | cut -f1)"
}

# --- Stufe 2+3: warpen und kacheln -------------------------------------------
# $1 Name  $2 hoechste Zoomstufe  $3 Filter  $4 guete (grob|fein)
bereich_bauen() {
    local bereich="$1" maxzoom="$2" filter="$3" guete="$4"
    local tr quelle_dir warp_co kachel_q
    tr=$(aufloesung "$maxzoom")

    if [ "$guete" = "fein" ]; then
        quelle_dir="tif-fein"   # verlustfrei ausgepackt: nur EINE verlustbehaftete Stufe
        kachel_q=92
    else
        quelle_dir="tif"
        kachel_q=90
    fi

    echo "== $bereich (bis z$maxzoom, $tr m/px in Mercator, $guete aus $quelle_dir/) =="
    liste_fuer "$filter" "$quelle_dir/" ".tif" > "$WURZEL/$bereich.txt"
    echo "   $(wc -l < "$WURZEL/$bereich.txt") Kacheln"

    # Virtuelles Mosaik — kopiert keine Bilddaten.
    gdal gdalbuildvrt -overwrite -input_file_list "$bereich.txt" "$bereich.vrt"

    # ★★★ EIN Schritt: gdalwarp schreibt direkt nach MBTILES. Bis 2026-08-29 lief
    #   das zweistufig ueber ein gekacheltes GeoTIFF — das kostete bei der
    #   z20-Flaeche ein 46-GB-Zwischenbild und war dabei nicht einmal schneller.
    #   Gemessen an einem 2x2-km-Block (Nordwestecke, z20):
    #     zweistufig JPEG   110 s, Zwischenbild  70 MB, Kacheln 77 MB
    #     direkt     WEBP   128 s, KEIN Zwischenbild,   Kacheln 51 MB
    #
    # ★★ -dstalpha ist der eigentliche Fix. Ohne Alphakanal fuellt GDAL alles
    #   ausserhalb des Datenrechtecks mit 0 = Schwarz, und JPEG kann keine
    #   Transparenz tragen. Die Quelle ist ein UTM32-Rechteck, in WebMercator
    #   leicht gedreht; dazu muss der Kachelschreiber vollstaendige 256er-Kacheln
    #   liefern. Ergebnis waren Randkacheln mit 82-99 % schwarzen Bildpunkten,
    #   die als dicker schwarzer Rahmen ums Luftbild standen. Gemessen ueber die
    #   ganze Pilotflaeche: vorher 2,03 % schwarz UND deckend, jetzt 0,00 %.
    #
    # ★ WEBP statt JPEG, weil es als einziges verbreitetes Kachelformat mit
    #   JPEG-aehnlicher Groesse einen Alphakanal hat — und hier sogar 34 %
    #   kleiner ausfaellt. PNG koennte es auch, waere aber ein Vielfaches gross.
    #   pmtiles traegt genau EIN Kachelformat je Archiv, gemischt geht nicht.
    #
    # ⚠ Zwei Wege, die NICHT funktionieren (beide gemessen, nicht vermutet):
    #   - `-co TILE_FORMAT=AUTO` gibt es im MBTILES-Treiber nicht (nur im GPKG).
    #   - Ein internes Maskenband (`gdal_translate -mask 4` mit
    #     GDAL_TIFF_INTERNAL_MASK) ueberlebt den Weg in die WEBP-Kacheln nicht:
    #     das Ergebnis hatte weiterhin 1,93 % schwarz UND deckend. Es sieht auf
    #     dem Zwischenbild richtig aus und ist es am Ende trotzdem nicht.
    echo "   -> warpen und kacheln in einem (WEBP Q$kachel_q, mit Alphakanal)"
    rm -f "$AUS/$bereich.mbtiles"
    gdal gdalwarp -overwrite -t_srs EPSG:3857 -tr "$tr" "$tr" -r bilinear -dstalpha \
        -multi -wo NUM_THREADS=ALL_CPUS --config GDAL_CACHEMAX 1536 \
        -of MBTILES -co TILE_FORMAT=WEBP -co QUALITY="$kachel_q" \
        "$bereich.vrt" "aus/$bereich.mbtiles"

    # Uebersichtsstufen. Ohne sie gibt es nur die hoechste Zoomstufe und die
    # Ansicht bleibt beim Herauszoomen leer.
    echo "   -> Uebersichtsstufen"
    gdal gdaladdo -r average "aus/$bereich.mbtiles" 2 4 8 16 32 64 128 256

    ls -lh "$AUS/$bereich.mbtiles"
}

# Welche Kachelsaetze gebaut werden. Ohne Angabe alle drei.
#   BEREICHE=umland ./build-dop-tiles.sh   — nur die Flaeche neu
# Sinnvoll, weil die beiden Inseln teuer sind (verlustfreies Auspacken) und
# sich selten aendern, waehrend die Flaeche bei jeder Erweiterung neu muss.
BEREICHE="${BEREICHE:-umland stadt solingen}"
gebaut() { [[ " $BEREICHE " == *" $1 "* ]]; }

# Durchgang 1: alle Kacheln als JPEG Q90 — Vorlage fuer die Flaeche.
gebaut umland && auspacken "$TIF" JPEG alle
# Durchgang 2: nur die Kacheln der Inseln verlustfrei.
gebaut stadt && auspacken "$TIF_FEIN" DEFLATE innen
gebaut solingen && auspacken "$TIF_FEIN" DEFLATE solingen

# ★ umland laeuft seit 2026-08-25 bis z20, nicht mehr bis z19: die Quelle hat
#   10 cm Bodenaufloesung, z20 entspricht 9,3 cm — auf der Flaeche lag also die
#   halbe Schaerfe brach, waehrend nur zwei Inseln von 8x8 km sie nutzten.
#   Kostet rund das Vierfache (z19 5,5 GB -> z20 gut 20 GB je 1000 km2).
gebaut umland   && bereich_bauen umland   20 alle     grob
gebaut stadt    && bereich_bauen stadt    20 innen    fein
gebaut solingen && bereich_bauen solingen 20 solingen fein

echo
du -sh "$AUS"/*.mbtiles
echo "Naechster Schritt: nach pmtiles wandeln und nach node1 ins Karten-Portal legen."
