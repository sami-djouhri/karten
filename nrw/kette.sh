#!/usr/bin/env bash
# kette.sh — arbeitet die rechenintensiven Kartenlaeufe NACHEINANDER ab.
#
# Warum nicht alles parallel starten: der LXC hat vier Kerne, und jeder der
# Laeufe will drei davon. Gleichzeitig gestartet werden alle drei langsamer als
# hintereinander, weil sie sich gegenseitig aus dem Plattenpuffer draengen —
# und Valhalla, das im selben LXC bedient, wird dabei zaeh.
#
# Die Kette ist bewusst dumm: sie wartet, ruft auf, protokolliert. Ein
# fehlgeschlagener Schritt haelt die spaeteren NICHT auf — Terrain und Gebaeude
# haengen nicht voneinander ab, und ein Abbruch beim ersten soll nicht die
# ganze Nacht kosten.
#
# Nicht enthalten: der Valhalla-Neubau. Der laeuft getrennt, weil er als
# einziger den laufenden Routing-Dienst beruehrt (siehe valhalla-hoehen.sh).
set -uo pipefail

LOG="${LOG:-/opt/nrw-werkzeug/kette.log}"

melde() { echo "[kette $(date -Is)] $*" | tee -a "$LOG"; }

warte_auf() {
    local unit="$1"
    if systemctl is-active --quiet "$unit"; then
        melde "warte auf $unit …"
        while systemctl is-active --quiet "$unit"; do sleep 60; done
        melde "$unit beendet (Ergebnis: $(systemctl show -p Result --value "$unit"))"
    fi
}

schritt() {
    local name="$1"; shift
    melde "START $name"
    local start=$SECONDS
    if "$@"; then
        melde "OK $name nach $(( (SECONDS - start) / 60 )) min"
    else
        # Kein `exit`: die folgenden Schritte sind unabhaengig.
        melde "FEHLGESCHLAGEN $name (rc=$?) — Kette laeuft weiter"
    fi
}

melde "=== Kette startet ==="
warte_auf luftbild-bau

schritt "Terrain (DGM1)" bash -c \
    '/opt/hoehendaten/build-terrain.sh > /opt/hoehendaten/build.log 2>&1'

schritt "Gebaeude (LoD2)" bash -c \
    '/opt/gebaeude/build-gebaeude.sh > /opt/gebaeude/build.log 2>&1'

melde "=== Kette fertig ==="
melde "Ergebnisse:"
ls -lh /opt/luftbilder/aus/*.mbtiles /opt/hoehendaten/aus/*.mbtiles \
       /opt/gebaeude/aus/*.mbtiles 2>/dev/null | tee -a "$LOG"
df -h / | tail -1 | tee -a "$LOG"
