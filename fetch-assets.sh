#!/usr/bin/env bash
# Vendored die MapLibre-/pmtiles-/Font-/Sprite-Assets lokal in static/, damit der
# Karten-Viewer OHNE Internet läuft (kein CDN zur Laufzeit). Einmal mit Internet
# ausführen; danach ist der Stack offline reproduzierbar. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/static"

MAPLIBRE=4.7.1
PMTILES=3.2.1
FONTS_BASE="https://protomaps.github.io/basemaps-assets/fonts"
SPRITE_BASE="https://protomaps.github.io/basemaps-assets/sprites/v4"

echo ">>> MapLibre $MAPLIBRE + pmtiles $PMTILES"
curl -sfL "https://cdn.jsdelivr.net/npm/maplibre-gl@${MAPLIBRE}/dist/maplibre-gl.js"  -o maplibre-gl.js
curl -sfL "https://cdn.jsdelivr.net/npm/maplibre-gl@${MAPLIBRE}/dist/maplibre-gl.css" -o maplibre-gl.css
curl -sfL "https://cdn.jsdelivr.net/npm/pmtiles@${PMTILES}/dist/pmtiles.js"           -o pmtiles.js

echo ">>> Sprites (light)"
mkdir -p sprites
for f in light.json light.png light@2x.json light@2x.png; do
  curl -sfL "${SPRITE_BASE}/${f}" -o "sprites/${f}" || echo "  (optional fehlt: ${f})"
done

echo ">>> Font-Glyphen (Noto Sans, europäische Bereiche inkl. Kyrillisch)"
# Fehlende Bereiche stürzen nicht ab, sie fehlen still: der Name wird nicht
# gezeichnet und es bleibt ein 404 im Log.
# ★ 1024-1279 ist Kyrillisch und war lange nicht dabei, weil die Liste aus der
#   Zeit vor der Weltebene stammt, als die Karte an der DE/NL-Kante endete.
#   Seit die Weltebene darunterliegt, fragte jede Ansicht Richtung Osten
#   (Balkan, Osteuropa, Türkei) den Bereich an und bekam 404. Sichtbar wurde es
#   kaum, weil der Stil zuerst `name:de` nimmt und die meisten Orte dort einen
#   deutschen Namen haben. Aufgefallen ist es erst, seit die Sichtprüfung
#   gescheiterte Anfragen meldet.
RANGES=(0-255 256-511 512-767 768-1023 1024-1279 7680-7935 8192-8447)
for STACK in "Noto Sans Regular"; do
  ENC=$(printf '%s' "$STACK" | sed 's/ /%20/g')
  mkdir -p "fonts/${STACK}"
  for R in "${RANGES[@]}"; do
    curl -sfL "${FONTS_BASE}/${ENC}/${R}.pbf" -o "fonts/${STACK}/${R}.pbf" || echo "  (fehlt: ${STACK} ${R})"
  done
done

echo ">>> fertig. Vendored Dateien:"
ls -1 maplibre-gl.js pmtiles.js sprites/ "fonts/Noto Sans Regular/" 2>/dev/null | sed 's/^/   /'
