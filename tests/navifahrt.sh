#!/usr/bin/env bash
# Simulierte Fahrt durch den Folgemodus. Startet ein headless Chromium, faehrt
# eine Route ab und wertet aus, was das Navi dabei angezeigt hat.
#
# Aufruf (auf node1):
#   tests/navifahrt.sh /tmp/navifahrt 51.3269,7.0169 51.1714,7.0846
#
# ★ Gefahren wird ueber den HTTPS-Vhost, nicht ueber die LAN-IP:
#   navigator.geolocation gibt es nur im sicheren Kontext, und ohne Standort
#   ist der Folgemodus nicht pruefbar. Der naheliegende Schalter
#   --unsafely-treat-insecure-origin-as-secure traegt NICHT — gemessen am
#   2026-08-29: isSecureContext blieb false und getCurrentPosition antwortete
#   "Only secure origins are allowed", auch mit --user-data-dir. Der HTTPS-Weg
#   ist ohnehin der, den ein Mensch benutzt.
#   --ignore-certificate-errors, weil das Zertifikat von der Homelab-CA kommt.
set -euo pipefail

ORDNER="${1:-/tmp/navifahrt}"
START="${2:-51.3269,7.0169}"
ZIEL="${3:-51.1714,7.0846}"

BILD="${BILD:-zenika/alpine-chrome:latest}"
PORTAL="${PORTAL:-https://karten.home.arpa}"
# Der Routen-Abruf des Testskripts laeuft ueber die LAN-IP: node muesste
# sonst der Homelab-CA vertrauen, und das ist fuer einen Test zu viel Umbau.
ROUTE_BASIS="${ROUTE_BASIS:-http://192.0.2.10:8144}"
NAME="navifahrt-$$"

mkdir -p "$ORDNER"; chmod 777 "$ORDNER"
aufraeumen() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap aufraeumen EXIT

docker run -d --rm --name "$NAME" --network host --entrypoint chromium-browser "$BILD" \
  --no-sandbox --headless --disable-dev-shm-usage --hide-scrollbars \
  --use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader \
  --ignore-certificate-errors \
  --window-size="${BREITE:-900}","${HOEHE:-1000}" \
  --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 \
  about:blank >/dev/null

for _ in $(seq 40); do
  curl -sf http://127.0.0.1:9222/json/version >/dev/null && break
  sleep 0.25
done

NODEOPT=""
node -e 'process.exit(typeof WebSocket === "function" ? 0 : 1)' || NODEOPT="--experimental-websocket"
PORTAL="$PORTAL" ROUTE_BASIS="$ROUTE_BASIS" node $NODEOPT "$(dirname "$0")/navifahrt.mjs" "$ORDNER" "$START" "$ZIEL"
