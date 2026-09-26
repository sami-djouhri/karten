#!/usr/bin/env python3
"""Waehlt aus einem NRW-Produktkatalog die Kacheln der bereits gewaehlten Flaeche.

    python3 nrw-auswahl.py dgm1  > dgm-liste.tsv
    python3 nrw-auswahl.py lod2  > lod2-liste.tsv

★ Die Flaechengeometrie steht NICHT hier drin. Sie wird aus `dop-liste.tsv`
  abgeleitet — derselben Datei, aus der die Luftbilder gebaut werden. Jede
  weitere Box-Definition waere eine zweite Stelle, an der jemand die Flaeche
  aendern muesste; die zweite vergisst man, und dann liegt das Gelaende neben
  dem Bild. So ist Deckungsgleichheit strukturell garantiert statt beabsichtigt.

Die Kachelnamen tragen Ost/Nord in Kilometern — die Produkte sind sich nur
uneinig, ob Zone und Ostwert getrennt stehen:
    dop10rgbi_32_361_5688_1_nw_2025.jp2  ->  (361, 5688)
    dgm1_32_361_5688_1_nw_2022.tif       ->  (361, 5688)
    LoD2_32_361_5688_1_NW.gml            ->  (361, 5688)
    ndom50_32361_5688_1_nw_2023.tif      ->  (361, 5688)   <- ohne Trennzeichen

Ausgabe: <name>\t<sollgroesse>, eine Zeile je Kachel.
"""
import json
import os
import re
import sys
import urllib.request

BASIS = "https://www.opengeodata.nrw.de/produkte/geobasis"
PRODUKTE = {
    "dgm1": (f"{BASIS}/hm/dgm1_tiff/dgm1_tiff/index.json", ".tif"),
    "dom1": (f"{BASIS}/hm/dom1_tiff/dom1_tiff/index.json", ".tif"),
    "lod2": (f"{BASIS}/3dg/lod2_gml/lod2_gml/index.json", ".gml"),
}

HIER = os.path.dirname(os.path.abspath(__file__))
DOP_LISTE = os.environ.get("DOP_LISTE", "/opt/luftbilder/dop-liste.tsv")

# Zone 32, dann Ostwert (3-stellig) und Nordwert (4-stellig) — mit oder ohne
# Trennzeichen dazwischen.
MUSTER = re.compile(r"_32_?(\d{3})_(\d{4})[_.]")


def index_aus(name):
    treffer = MUSTER.search(name)
    return (int(treffer.group(1)), int(treffer.group(2))) if treffer else None


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in PRODUKTE:
        sys.exit(f"Aufruf: {sys.argv[0]} <{'|'.join(PRODUKTE)}>")
    produkt = sys.argv[1]
    katalog_url, endung = PRODUKTE[produkt]
    katalog_datei = os.path.join(HIER, f"{produkt}_index.json")

    # --- Sollflaeche aus der Luftbild-Auswahl --------------------------------
    if not os.path.exists(DOP_LISTE):
        sys.exit(f"FEHLT: {DOP_LISTE} — erst dop-auswahl.py laufen lassen")
    soll = set()
    with open(DOP_LISTE) as fh:
        for zeile in fh:
            spalten = zeile.rstrip("\n").split("\t")
            if len(spalten) >= 2:
                idx = index_aus(spalten[1])
                if idx:
                    soll.add(idx)
    print(f"Sollflaeche aus {os.path.basename(DOP_LISTE)}: {len(soll):,} Kacheln",
          file=sys.stderr)
    if not soll:
        sys.exit("FEHLER: keine Kachelindizes in der Luftbild-Liste erkannt")

    # --- Katalog besorgen ----------------------------------------------------
    if not os.path.exists(katalog_datei):
        print(f"Katalog fehlt, hole {katalog_url}", file=sys.stderr)
        with urllib.request.urlopen(katalog_url, timeout=180) as antwort:
            roh = antwort.read()
        # Erst vollstaendig lesen, dann schreiben — ein Abbruch mitten im
        # Download darf keine halbe Datei hinterlassen, die spaeter als gueltig
        # durchgeht.
        with open(katalog_datei + ".tmp", "wb") as fh:
            fh.write(roh)
        os.replace(katalog_datei + ".tmp", katalog_datei)
        print(f"   {len(roh)/1e6:.1f} MB nach {katalog_datei}", file=sys.stderr)

    with open(katalog_datei) as fh:
        daten = json.load(fh)

    katalog = {}
    for satz in daten.get("datasets", []):
        for eintrag in satz.get("files", []):
            if not eintrag["name"].endswith(endung):
                continue
            idx = index_aus(eintrag["name"])
            if idx:
                katalog[idx] = (eintrag["name"], int(eintrag["size"]))
    print(f"{produkt}-Katalog: {len(katalog):,} Kacheln", file=sys.stderr)
    if not katalog:
        sys.exit("FEHLER: Katalog leer — hat sich das Format geaendert?")

    # --- Schnittmenge --------------------------------------------------------
    gewaehlt, fehlend = [], []
    for idx in sorted(soll):
        eintrag = katalog.get(idx)
        (gewaehlt.append(eintrag) if eintrag else fehlend.append(idx))

    byte = sum(g for _, g in gewaehlt)
    print(f"gewaehlt: {len(gewaehlt):,} Kacheln, {byte/1e9:.1f} GB", file=sys.stderr)
    if fehlend:
        # Kein Abbruch: eine Luecke macht ein Loch in der Ebene, aber keinen
        # kaputten Kachelsatz. Sichtbar soll sie trotzdem sein.
        print(f"WARNUNG: {len(fehlend)} Kacheln ohne {produkt}: "
              f"{fehlend[:8]}{' …' if len(fehlend) > 8 else ''}", file=sys.stderr)

    for name, groesse in gewaehlt:
        print(f"{name}\t{groesse}")


if __name__ == "__main__":
    main()
