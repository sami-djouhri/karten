#!/usr/bin/env bash
# nachladen.sh — holt Luftbilder, Hoehen und Gebaeude fuer eine erweiterte Flaeche.
#
# Nach jeder Aenderung an `luftbilder/dop-auswahl.py` muessen die abgeleiteten
# Datensaetze mitwachsen, sonst liegt das Gelaende neben dem Bild. Die
# Kachellisten leiten sich aus `dop-liste.tsv` ab — dieses Skript ruft nur die
# Werkzeuge in der richtigen Reihenfolge auf.
#
# Alle drei Laeufe sind wiederaufnehmbar: was in der richtigen Groesse daliegt,
# wird uebersprungen.
set -uo pipefail

LOG="${LOG:-/opt/nrw-werkzeug/nachladen.log}"
melde() { echo "[nachladen $(date -Is)] $*" | tee -a "$LOG"; }

melde "=== Luftbilder ==="
/opt/luftbilder/fetch-dop.sh 2>&1 | tail -3 | tee -a "$LOG"

melde "=== Hoehenmodell ==="
# Kataloge liegen schon lokal — die Auswahl rechnet nur die Schnittmenge neu.
python3 /opt/nrw-werkzeug/nrw-auswahl.py dgm1 > /opt/hoehendaten/dgm1-liste.tsv 2>>"$LOG"
/opt/nrw-werkzeug/hole-nrw.sh dgm1 /opt/hoehendaten/dgm1-liste.tsv \
    /opt/hoehendaten/quelle 2>&1 | tail -2 | tee -a "$LOG"

melde "=== Gebaeudemodelle ==="
python3 /opt/nrw-werkzeug/nrw-auswahl.py lod2 > /opt/gebaeude/lod2-liste.tsv 2>>"$LOG"
/opt/nrw-werkzeug/hole-nrw.sh lod2 /opt/gebaeude/lod2-liste.tsv \
    /opt/gebaeude/quelle 2>&1 | tail -2 | tee -a "$LOG"

melde "=== Bestand ==="
{
    printf "  Luftbilder: %s Kacheln, %s\n" \
        "$(ls /opt/luftbilder/quelle/*.jp2 2>/dev/null | wc -l)" \
        "$(du -sh /opt/luftbilder/quelle | cut -f1)"
    printf "  Hoehen    : %s Kacheln, %s\n" \
        "$(ls /opt/hoehendaten/quelle/*.tif 2>/dev/null | wc -l)" \
        "$(du -sh /opt/hoehendaten/quelle | cut -f1)"
    printf "  Gebaeude  : %s Kacheln, %s\n" \
        "$(ls /opt/gebaeude/quelle/*.gml 2>/dev/null | wc -l)" \
        "$(du -sh /opt/gebaeude/quelle | cut -f1)"
    df -h / | tail -1
} | tee -a "$LOG"
melde "=== fertig ==="
