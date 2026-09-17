#!/usr/bin/env bash
# ausliefern.sh — holt fertige Kachelarchive vom Bau-LXC und legt sie als
# .pmtiles ins Karten-Portal.
#
# ★ Laeuft auf host, nicht auf einem der beiden Endpunkte: node1 hat keinen
#   Zugang zum Bau-LXC (kein Host-Key, kein autorisierter Schluessel), und der
#   Bau-LXC hat keinen zu node1. host erreicht beide, also leitet host durch.
#   Das kostet einen zweiten Netzsprung — der Alternativweg waere ein neuer
#   Schluessel zwischen zwei Hosts, die sonst nichts miteinander zu tun haben.
#
# ★★ Der Tausch am Ende ist ATOMAR (`mv` im selben Dateisystem). Ein Abbruch
#    mitten in der Uebertragung darf die servierte Karte nie kaputt zuruecklassen
#    — dieselbe Lehre wie bei extract-maps.sh, wo ein gekillter Lauf einmal einen
#    Null-Header hinterliess und die Karte tot war.
set -uo pipefail

BAU="${BAU:-root@192.0.2.10}"
BAU_KEY="${BAU_KEY:-$HOME/.ssh/id_node1}"
ZIEL="${ZIEL:-user@192.0.2.10}"
ZIEL_KEY="${ZIEL_KEY:-$HOME/.ssh/id_node1}"
MAPS="${MAPS:-/home/user/knowledge-vault/maps}"
# Umgewandelt wird auf dem BAU-LXC, nicht auf node1. Beide haben pmtiles 1.31.2.
PMTILES_BAU="${PMTILES_BAU:-/usr/local/bin/pmtiles}"
PMTILES_ZIEL="${PMTILES_ZIEL:-/home/user/bin/pmtiles}"
BAU_WERK="${BAU_WERK:-/opt/pmtiles-werk}"
# Werkstatt bewusst NEBEN dem servierten Verzeichnis, aber im selben
# Dateisystem: nichts Halbfertiges unter maps/, und `mv` bleibt trotzdem atomar.
WERK="${WERK:-/home/user/.mapbuild}"

# ServerAlive: die Umwandlung grosser Kachelsaetze laeuft minutenlang ohne ein
# einziges Byte auf dem Kanal. Ohne Lebenszeichen raeumt eine Zwischenstation
# die scheinbar tote Verbindung ab.
SSH_ART=(-o IdentitiesOnly=yes -o BatchMode=yes
         -o ServerAliveInterval=30 -o ServerAliveCountMax=20)
sb() { ssh -i "$BAU_KEY" "${SSH_ART[@]}" "$BAU" "$@"; }
sz() { ssh -i "$ZIEL_KEY" "${SSH_ART[@]}" "$ZIEL" "$@"; }

# name:pfad auf dem Bau-LXC
ARCHIVE=(
    "umland:/opt/luftbilder/aus/umland.mbtiles"
    "stadt:/opt/luftbilder/aus/stadt.mbtiles"
    "solingen:/opt/luftbilder/aus/solingen.mbtiles"
    "gelaende:/opt/hoehendaten/aus/gelaende.mbtiles"
    "gebaeude:/opt/gebaeude/aus/gebaeude.mbtiles"
)

sz "mkdir -p '$WERK'"
sb "mkdir -p '$BAU_WERK'"
fehler=0

for eintrag in "${ARCHIVE[@]}"; do
    name="${eintrag%%:*}"
    pfad="${eintrag#*:}"

    roh=$(sb "stat -c%s '$pfad' 2>/dev/null" || true)
    if [ -z "$roh" ]; then
        echo "== $name: noch nicht gebaut, uebersprungen"
        continue
    fi

    echo "== $name (mbtiles $(numfmt --to=iec "$roh"))"

    # ★★★ Umgewandelt wird AUF DEM BAU-LXC, nicht auf node1. Beide Gruende sind
    #     gemessen: node1 ist ein Pi 5 und schaffte bei umland 1-2 Kacheln je
    #     Sekunde — bei 3,1 Mio Kacheln sind das ueber 400 Stunden, der Lauf vom
    #     26.08. wurde bei 20 Prozent abgebrochen. Der Bau-LXC hat vier
    #     x86-Kerne, auf denen das Bauen selbst laeuft. Und uebertragen wird
    #     danach die KLEINERE Datei: pmtiles ist kompakter als mbtiles, und die
    #     40 GB, die frueher als mbtiles ueber zwei Netzsprünge gingen, entfallen.
    # ★★ Der Fortschrittsbalken bleibt auf der Gegenseite in einer Logdatei.
    #    Er wird mehrmals je Sekunde neu gezeichnet; bei einer halben Million
    #    Kacheln fliessen so megabyteweise Steuerzeichen durch den SSH-Kanal
    #    zurueck, bis er mit „Broken pipe" abbricht und die Umwandlung mitreisst.
    #    Genau so scheiterte umland am 2026-08-23 bei 533.087 Kacheln, waehrend
    #    die kleineren Saetze durchliefen.
    echo "   wandle auf dem Bau-LXC nach pmtiles …"
    if ! sb "$PMTILES_BAU convert '$pfad' '$BAU_WERK/$name.pmtiles' --tmpdir '$BAU_WERK' > '$BAU_WERK/$name.convert.log' 2>&1"; then
        echo "   FEHLER bei der Umwandlung — letzte Zeilen:" >&2
        sb "tail -4 '$BAU_WERK/$name.convert.log' 2>/dev/null | tr -d '\r'" >&2
        fehler=1; continue
    fi
    if ! sb "$PMTILES_BAU verify '$BAU_WERK/$name.pmtiles' >/dev/null 2>&1"; then
        echo "   FEHLER: verify auf dem Bau-LXC fehlgeschlagen — nichts uebertragen" >&2
        sb "rm -f '$BAU_WERK/$name.pmtiles'"
        fehler=1; continue
    fi
    groesse=$(sb "stat -c%s '$BAU_WERK/$name.pmtiles'")
    echo "   pmtiles $(numfmt --to=iec "$groesse") — uebertrage …"

    if ! sb "cat '$BAU_WERK/$name.pmtiles'" | sz "cat > '$WERK/$name.pmtiles.tmp'"; then
        echo "   FEHLER bei der Uebertragung" >&2; fehler=1; continue
    fi

    # ★ Groessenpruefung ist kein Luxus: ein abgebrochener Datenstrom liefert
    #   eine kuerzere, aber voellig gueltig aussehende Datei. Der Header stimmt,
    #   die Kacheln am Ende fehlen — die Karte haette Loecher statt eines Fehlers.
    da=$(sz "stat -c%s '$WERK/$name.pmtiles.tmp' 2>/dev/null" || true)
    if [ "$da" != "$groesse" ]; then
        echo "   FEHLER: $da statt $groesse Byte angekommen — verworfen" >&2
        sz "rm -f '$WERK/$name.pmtiles.tmp'"
        fehler=1; continue
    fi
    # Zweite, unabhaengige Pruefung am Ziel: die Groesse allein faende eine
    # Verfaelschung in der Mitte nicht.
    if ! sz "$PMTILES_ZIEL verify '$WERK/$name.pmtiles.tmp' >/dev/null 2>&1"; then
        echo "   FEHLER: verify am Ziel fehlgeschlagen — alte Datei bleibt gueltig" >&2
        sz "rm -f '$WERK/$name.pmtiles.tmp'"
        fehler=1; continue
    fi

    sz "mv -f '$WERK/$name.pmtiles.tmp' '$MAPS/$name.pmtiles' && rm -f '$WERK/$name.mbtiles'"
    sb "rm -f '$BAU_WERK/$name.pmtiles'"
    echo "   live: $(sz "du -h '$MAPS/$name.pmtiles' | cut -f1")"
done

echo
echo "== Karten im Portal =="
sz "ls -lh '$MAPS'/*.pmtiles | awk '{printf \"  %-22s %s\n\", \$9, \$5}'"
exit "$fehler"
