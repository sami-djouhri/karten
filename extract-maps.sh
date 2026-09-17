#!/usr/bin/env bash
# extract-maps.sh — zieht per HTTP-Range eine Region aus dem Protomaps-Planeten
# in eine lokale .pmtiles fuer den karten-Dienst. Braucht EINMALIG Internet.
# Laeuft auf node1 (dort liegt go-pmtiles + der knowledge-vault).
#
# ROBUSTHEIT (der Grund fuer dieses Skript):
#  - Extrahiert in eine .tmp und tauscht ATOMAR erst nach Magic-Verify um.
#    Ein abgebrochener/gekillter Lauf laesst die servierte Karte NIE kaputt
#    zurueck (frueher Bug: gekillter Extract -> Null-Header -> Karte tot).
#  - Findet das juengste verfuegbare Protomaps-Build automatisch.
#  - Loggt nach $OUT_DIR/extract-maps.log.
#
# Detached starten (ueberlebt SSH-Trennung/Session-Ende):
#   setsid nohup ./extract-maps.sh >/dev/null 2>&1 < /dev/null &
#
# Region wechseln:  BBOX="-11,34,40,72" ./extract-maps.sh   (Europa, viel groesser)
set -euo pipefail

PMTILES_BIN="${PMTILES_BIN:-/home/user/bin/pmtiles}"
OUT_DIR="${OUT_DIR:-/home/user/knowledge-vault/maps}"
OUT_NAME="${OUT_NAME:-de-nl.pmtiles}"
# bbox = minLon,minLat,maxLon,maxLat
#   Standard: Deutschland + Niederlande, volle Detailstufe z15.
#   Nur hier werden Strassennamen gebraucht (Owner-Vorgabe 2026-08-16) — jede
#   Verbreiterung kostet quadratisch, ganz Europa waere bei z15 dreistellig GB.
#   Deutschland allein:  5.5,47.0,15.5,55.2   (~8 GB)
#   Europa-Uebersicht:  -11,34,40,72  mit MAXZOOM=9-10 (~1-2 GB, ohne Strassen)
BBOX="${BBOX:-3.2,47.0,15.5,55.3}"
MAXZOOM="${MAXZOOM:-15}"
LOG="${LOG:-$OUT_DIR/extract-maps.log}"

log() { echo "[extract-maps $(date -Is)] $*" | tee -a "$LOG"; }

# Juengstes verfuegbares Protomaps-Build finden (Range-Probe der letzten ~12 Tage).
find_build() {
  local d code i
  for i in $(seq 1 12); do
    d=$(date -u -d "-$i day" +%Y%m%d)
    code=$(curl -sf -o /dev/null -w "%{http_code}" -r 0-1023 \
      "https://build.protomaps.com/$d.pmtiles" 2>/dev/null || true)
    if [ "$code" = "206" ] || [ "$code" = "200" ]; then echo "$d"; return 0; fi
  done
  return 1
}

mkdir -p "$OUT_DIR"

if ! command -v "$PMTILES_BIN" >/dev/null 2>&1 && [ ! -x "$PMTILES_BIN" ]; then
  log "FEHLER: go-pmtiles nicht gefunden unter $PMTILES_BIN"
  exit 1
fi

BUILD="${BUILD:-$(find_build)}" || { log "FEHLER: kein Protomaps-Build erreichbar (Internet?)"; exit 1; }
SRC="https://build.protomaps.com/$BUILD.pmtiles"
# .tmp bewusst AUSSERHALB von OUT_DIR: der Download-in-Arbeit liegt so weder im
# servierten maps/ (nginx) noch im restic-Pfad -> kein halb-fertiges File im
# Backup, keine Kollision mit einem parallel laufenden Seed. mv am Ende ist ein
# atomarer Rename, solange BUILD_DIR + OUT_DIR dasselbe Dateisystem sind.
BUILD_DIR="${BUILD_DIR:-/home/user/.mapbuild}"
mkdir -p "$BUILD_DIR"
TMP="$BUILD_DIR/$OUT_NAME.tmp"

log "Start: Build=$BUILD bbox=$BBOX maxzoom=$MAXZOOM -> $OUT_DIR/$OUT_NAME"

# Ohne diese Falle endet ein Fehlschlag wegen `set -e` STILL: im Log steht dann
# nur "Start" und nie ein Ergebnis (passiert am 2026-08-16). Jetzt hinterlaesst
# jeder Abbruch eine Zeile.
trap 'rc=$?; [ $rc -ne 0 ] && log "ABBRUCH (exit $rc) — alte Karte bleibt gueltig"; exit $rc' EXIT

# Der Protomaps-Planet wird per HTTP-Range in tausenden Chunks geholt; einzelne
# HTTP/2-Streams brechen dabei gelegentlich ab ("INTERNAL_ERROR; received from
# peer") — am 2026-08-16 bei 99 % von 9,3 GB. Das ist transient, also mehrfach
# versuchen statt den ganzen Lauf zu verlieren. pmtiles kann nicht fortsetzen,
# jeder Versuch beginnt neu.
VERSUCHE="${VERSUCHE:-3}"
erfolg=0
for i in $(seq 1 "$VERSUCHE"); do
  rm -f "$TMP"
  log "Extract-Versuch $i/$VERSUCHE"
  if "$PMTILES_BIN" extract "$SRC" "$TMP" --bbox="$BBOX" --maxzoom="$MAXZOOM"; then
    erfolg=1
    break
  fi
  log "Versuch $i fehlgeschlagen"
  sleep 30
done
if [ "$erfolg" -ne 1 ]; then
  log "FEHLER: Extract nach $VERSUCHE Versuchen aufgegeben — alte Karte bleibt gueltig"
  rm -f "$TMP"
  exit 3
fi

# Magic/Struktur verifizieren BEVOR umbenannt wird -> nur gueltige Karten gehen live.
if "$PMTILES_BIN" verify "$TMP" >/dev/null 2>&1; then
  mv -f "$TMP" "$OUT_DIR/$OUT_NAME"
  log "OK: verifiziert + atomar getauscht ($(du -h "$OUT_DIR/$OUT_NAME" | cut -f1))"
else
  log "FEHLER: pmtiles verify fehlgeschlagen — .tmp NICHT uebernommen (alte Karte bleibt gueltig)"
  rm -f "$TMP"
  exit 2
fi
