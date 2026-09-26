#!/usr/bin/env bash
# Sichtpruefung des Karten-Portals: startet ein headless Chromium, fotografiert
# die angegebenen Ansichten und raeumt wieder auf.
#
# Aufruf (auf node1, dort liegt docker und das Portal):
#   tests/kartenbild.sh /tmp/kartenbilder \
#       paris=12/48.86/2.35 \
#       heiligenhaus=15/51.3269/7.0169 \
#       luftbild_west=13/51.30/6.72:Luftbild
#
# Jede Ansicht ist <name>=<kartenhash>[:<Knopf>,<Knopf>]. Die Knoepfe sind die
# Aufschriften der Umschalter, und zwar buchstabengetreu MIT Umlaut:
# Karte, Satellit, Luftbild, Gelände, Gebäude.
# ★ Diese Zeile stand hier lange in Umschrift ("Gelaende") und traf damit keinen
#   Knopf. Das Bild entstand trotzdem, zeigte die Grundkarte und trug den Namen
#   der Ebene, die gar nicht an war. Seit die fehlende Aufschrift ein Mangel ist,
#   endet der Lauf in dem Fall mit einem Rueckgabewert ungleich 0.
#
# ★ Warum ein Treiber statt `chromium --screenshot`: siehe Kopf von
#   kartenbild.mjs. Kurz — unter --virtual-time-budget feuert requestAnimationFrame
#   nicht, MapLibre malt nur dort, und das leere Bild sieht aus wie fehlende
#   Kartendaten statt wie ein kaputtes Werkzeug.
set -euo pipefail

ORDNER="${1:?Aufruf: kartenbild.sh <ausgabeordner> <name>=<hash>[:Knopf] ...}"
shift
[ $# -gt 0 ] || { echo "keine Ansicht angegeben" >&2; exit 2; }

BILD="${BILD:-zenika/alpine-chrome:latest}"
PORTAL="${PORTAL:-https://karten.home.arpa}"
NAME="kartenbild-$$"

mkdir -p "$ORDNER"
chmod 777 "$ORDNER"

aufraeumen() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap aufraeumen EXIT

# --remote-debugging-address bleibt auf 127.0.0.1: der Steuerkanal ist
# root-aequivalent und hat im LAN nichts verloren.
docker run -d --rm --name "$NAME" --network host --entrypoint chromium-browser "$BILD" \
  --no-sandbox --headless --disable-dev-shm-usage --hide-scrollbars \
  --use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader \
  --ignore-certificate-errors \
  --window-size="${BREITE:-1280}","${HOEHE:-900}" \
  --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 \
  about:blank >/dev/null

for _ in $(seq 40); do
  curl -sf http://127.0.0.1:9222/json/version >/dev/null && break
  sleep 0.25
done
curl -sf http://127.0.0.1:9222/json/version >/dev/null || { echo "Chromium kam nicht hoch" >&2; exit 1; }

# node 21+ hat WebSocket eingebaut, node 20 braucht die Option.
NODEOPT=""
node -e 'process.exit(typeof WebSocket === "function" ? 0 : 1)' || NODEOPT="--experimental-websocket"

PORTAL="$PORTAL" node $NODEOPT "$(dirname "$0")/kartenbild.mjs" "$ORDNER" "$@"
